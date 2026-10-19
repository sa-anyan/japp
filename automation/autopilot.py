"""Resumable private-exchange controller. Never submit ChatGPT or send mail.

The browser queue is a data contract for an attended browser operator (including
Codex), not a scraper. Authentication and security challenges remain handoffs.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .approvals import eligible_companies
from .engine import SingleInstanceLock
from .exchange import collect_outputs, company_folder, export_research, package_inputs, write_json
from .linkedin_queue import refresh_linkedin_queue
from .outreach import create_email_batch, draft_email_batch
from .private_repository import PrivateRepository, inside, command
from .providers.factory import create_mail_provider
from .state import StateStore, stable_hash, utc_now
from .workbook import mark_contact_emailed, mark_execution_emailed, update_pipeline_status, write_contacts


BROWSER_RULES = """Use supported browser controls and visible page state only.
Search the exact researched name; verify the LinkedIn /in/ identity before revealing
one work email. Never reveal mobile/personal details or use waterfall vendors.
Record the observed email, verification label, catch-all flag, person/contact IDs
and source URL. No email may be guessed. A different employer/title requires review.
Invalid access credentials means login_required, not no_match. A widget/security
challenge means security_check_required: hand off, never bypass. A timeout after
reveal is uncertain: do not click reveal again. Zero results counts as no_match
only after the completed search has no error. Do not loop alternate queries.
Save outcomes using the result schema in browser_queue.json. Re-run start afterward.
Do not send emails, submit ChatGPT messages or extract ChatGPT responses.
"""


def read(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def profile(value: str) -> str:
    parsed = urlparse(value or "")
    host = (parsed.hostname or "").casefold()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return ""
    return parsed.path.rstrip("/").casefold() if parsed.path.startswith("/in/") else ""


def reconcile_mail(config, state, provider, rows):
    """Mailbox truth wins over local draft records; sent recipients stay blocked."""
    remote = provider.application_messages()
    sent = {}; drafts = {}
    for item in remote:
        for recipient in item.get("toRecipients", []):
            address = recipient.get("emailAddress", {}).get("address", "").strip().casefold()
            if address:
                target = drafts if item.get("isDraft") else sent
                target.setdefault(address, []).append(item)
    known = {m["contact_id"]: m for b in state.list_batches() for m in state.list_messages(b["batch_id"])}
    blocked = set(); waiting = []
    for row in rows:
        for contact in state.list_contacts(row.company_id):
            email = contact["email"].strip().casefold()
            if not email or contact["suppressed"] or contact["email_status"] != "verified":
                continue
            message = known.get(contact["contact_id"])
            # Any sent mail to this recipient requires explicit follow-up outside
            # this controller, even if the generated subject subsequently changed.
            if email in sent:
                blocked.add(contact["contact_id"])
                if message:
                    exact = next((x for x in sent[email] if x["subject"] == message["subject"]), None)
                    if exact and message["status"] != "sent":
                        state.finish_message(message["idempotency_key"], "sent", provider_id=exact["id"])
                        state.record_event("mailbox_sent_item_observed", row.company_id,
                                           {"recipient": email, "sent_at": exact.get("sentDateTime")})
                        mark_contact_emailed(config["paths"]["workbook"], row.company_id, email)
                waiting.append(f"{row.company_id}: recipient already in Sent Items; no repeat draft")
            elif email in drafts:
                blocked.add(contact["contact_id"])
                if message and message["status"] == "drafted":
                    exact = next((x for x in drafts[email] if x["subject"] == message["subject"]), None)
                    if exact and exact["id"] != message["provider_id"]:
                        state.finish_message(message["idempotency_key"], "drafted", provider_id=exact["id"])
                waiting.append(f"{row.company_id}: draft already exists")
            elif message:
                blocked.add(contact["contact_id"])
                waiting.append(f"{row.company_id}: recorded {message['status']} missing from mailbox; review, no automatic recreation")
        messages = [m for b in state.list_batches() for m in state.list_messages(b["batch_id"])
                    if m["company_id"] == row.company_id]
        if messages and all(m["status"] == "sent" for m in messages):
            current = state.get_company(row.company_id)
            if current and current["state"] != "COMPLETED":
                # Reconciliation observes an external send; it must not fabricate
                # a local approval/queue/send sequence to reach this state.
                with state._connect() as db:
                    db.execute("UPDATE companies SET state='COMPLETED',updated_at=? WHERE company_id=?",
                               (utc_now(), row.company_id))
                mark_execution_emailed(config["paths"]["workbook"], row.company_id)
                update_pipeline_status(config["paths"]["workbook"], row.company_id, "COMPLETED")
    return blocked, waiting


def import_browser_results(config, repo, state, rows, result_file):
    """Import operator-observed outcomes once, bound to an approved research lead."""
    data = read(result_file, {"results": []})
    indexed = {r.company_id: r for r in rows}; files = []; imported = []
    for result in data.get("results", []):
        cid = str(result.get("company_id", ""))
        receipt = repo.path / "research/contact_checks" / f"{cid}.json" if cid.isdigit() else None
        if receipt and read(receipt, {}).get("result_hash") == stable_hash(result):
            continue
        if cid not in indexed:
            raise ValueError(f"Browser result {cid} is not currently approved + Start")
        row = indexed[cid]
        leads = read(repo.path / "research/contact_candidates" / f"{cid}.json", {})
        match = next((p for p in leads.get("candidates", []) + leads.get("unverified_people", [])
                      if profile(p.get("linkedin_url")) and profile(p.get("linkedin_url")) == profile(result.get("linkedin_url"))), None)
        if not match:
            raise ValueError(f"Browser result {cid} does not match a researched LinkedIn identity")
        status = result.get("status")
        if status not in {"verified", "no_email", "no_match", "login_required", "security_check_required", "uncertain", "review_required"}:
            raise ValueError(f"Unsupported browser outcome for {cid}")
        if status in {"login_required", "security_check_required", "uncertain", "review_required"}:
            continue  # Never turn a browser failure into a negative contact result.
        email = str(result.get("email") or "").strip()
        if status == "verified":
            url = urlparse(result.get("source_url", ""))
            if (email.count("@") != 1 or any(c.isspace() for c in email)
                    or result.get("verification_label") != "Verified in Apollo"
                    or url.scheme != "https" or url.hostname != "app.apollo.io"
                    or not result.get("apollo_person_id") or not result.get("apollo_contact_id")):
                raise ValueError(f"Verified browser result {cid} lacks observed Apollo evidence")
        elif email:
            raise ValueError(f"Non-verified result {cid} must not contain an email")
        identity = stable_hash(result)
        receipt = inside(repo.path, f"research/contact_checks/{cid}.json")
        if read(receipt, {}).get("result_hash") == identity:
            continue
        s = "verified" if email else "no_email"
        state.save_contact(cid, row.company, full_name=match["full_name"],
                           role=result.get("role") or match.get("role", ""), email=email,
                           linkedin_url=match["linkedin_url"], email_status=s,
                           source="Apollo browser; " + str(result.get("note", status)))
        pid = result.get("apollo_person_id")
        if pid and state.reserve_enrichment(pid):
            state.finish_enrichment(pid, {"person": {"id": pid, "name": match["full_name"],
                "email": email, "email_status": s, "linkedin_url": match["linkedin_url"]},
                "source": "Apollo browser", "catch_all": bool(result.get("catch_all"))})
        contacts = state.list_contacts(cid)
        write_contacts(config["paths"]["workbook"], cid,
                       [{**x, "contact_status": "found" if x["email_status"] == "verified" else "no_email"}
                        for x in contacts if not x["suppressed"]][:5])
        files.append(write_json(company_folder(repo.path, cid, row.company) / "contacts.json",
                                {"company_id": cid, "company": row.company, "contacts": contacts,
                                 "catch_all": bool(result.get("catch_all")), "notes": [result.get("note", "")]}))
        files.append(write_json(receipt, {**result, "result_hash": identity, "checked_at": utc_now()}))
        imported.append((cid, bool(email)))
    if files:
        repo.publish(files, "Publish cached supervised Apollo browser outcomes")
        for cid, has_email in imported:
            if has_email: update_pipeline_status(config["paths"]["workbook"], cid, "Contacts Ready")
    return len(imported)


def run_cycle(config: dict[str, Any], *, repo=None, state=None, provider=None,
              mail: bool = True, plan: bool = False, max_reveals: int = 3,
              _lock_held: bool = False, _synced: bool = False) -> dict:
    """One bounded cycle. A rerun resumes; it never resubmits an unchanged stage."""
    rows = eligible_companies(config["paths"]["workbook"], config["paths"]["registry"])
    if plan:
        return {"approved_ids": [r.company_id for r in rows], "mode": "read-only plan", "sent": 0}
    if max_reveals < 0:
        raise ValueError("max_reveals cannot be negative")
    state = state or StateStore(config["paths"]["database"]); state.initialize()
    repo = repo or PrivateRepository(config)
    config = {**config, "paths": {**config["paths"], "private_repo_dir": repo.path}}
    lock = SingleInstanceLock(config["paths"]["lock_file"])
    if not _lock_held: lock.acquire()
    try:
        if Path(config["paths"]["stop_file"]).exists():
            return {"stopped": True, "sent": 0}
        # Refuse Excel's owner lock before writing, rather than overwrite unsaved edits.
        book = Path(config["paths"]["workbook"])
        if book.with_name("~$" + book.name).exists():
            raise ValueError("Save and close the dashboard in Excel, then start again")
        if not _synced and hasattr(repo, "sync"): repo.sync()
        work = Path(config["paths"]["generated_dir"]) / "autopilot"
        work.mkdir(parents=True, exist_ok=True)
        imported = import_browser_results(config, repo, state, rows, work / "browser_results.json")
        browser_outcomes = {str(x.get("company_id")): x for x in
                            read(work / "browser_results.json", {"results": []}).get("results", [])}
        exported = {str(x["company_id"]) for f in (repo.path / "requests").glob("*/companies.json")
                    for x in read(f, {}).get("companies", [])}
        missing = {r.company_id for r in rows if r.company_id not in exported
                   and not (repo.path / "research/companies" / f"{r.company_id}.json").is_file()}
        research = export_research(config, repo, company_ids=missing) if missing else None
        errors = []; packaged = []; ready = set(); browser = []; contact_waits = []
        stages = {"research": [], "documents": [], "outreach": []}
        collected_cache = read(work / "collected.json", {})
        for row in rows:
            cid = row.company_id; folder = company_folder(repo.path, cid, row.company)
            if not (repo.path / "research/companies" / f"{cid}.json").is_file():
                stages["research"].append(cid); continue
            # Never silently invalidate existing packages/drafts after a prompt edit.
            if not (folder / "inputs/input_package.json").is_file() or not state.published_input_hash(cid):
                try:
                    result = package_inputs(config, repo, state, company_ids={cid})
                    packaged.extend(result["packaged"] if isinstance(result["packaged"], list) else [cid] if result["packaged"] else [])
                    if not (folder / "inputs/input_package.json").is_file():
                        errors.extend(result["waiting"]); continue
                except (ValueError, OSError) as exc:
                    errors.append(f"{cid}: {exc}"); continue
            try:
                manifest = read(folder / "outputs/manifest.json", {})
                leads = read(repo.path / "research/contact_candidates" / f"{cid}.json", {})
                checked = read(repo.path / "research/contact_checks" / f"{cid}.json", {})
            except (ValueError, OSError) as exc:
                errors.append(f"{cid}: malformed exchange record: {exc}"); continue
            contacts = state.list_contacts(cid)
            if manifest.get("ready") is True:
                try:
                    digest = stable_hash({p.name: stable_hash(p.read_bytes().hex())
                                          for p in (folder / "outputs").iterdir() if p.is_file()})
                    if collected_cache.get(cid) != digest:
                        collect_outputs(config, repo, state, company_ids={cid})
                        collected_cache[cid] = digest
                        write_json(work / "collected.json", collected_cache)
                    ready.add(cid)
                except (ValueError, OSError) as exc:
                    errors.append(f"{cid}: {exc}")
            else:
                docs = list((folder / "outputs").glob("*.docx"))
                if len(docs) < 2:
                    stages['documents'].append(cid)
                elif any(x['email'] and x['email_status'] == 'verified' and not x['suppressed'] for x in contacts):
                    stages['outreach'].append(cid)
                elif contacts and not (folder/'outputs/linkedin_outreach.json').is_file():
                    stages['outreach'].append(cid)
                elif not contacts:
                    contact_waits.append(f"{cid}: awaiting contact matching/research; do not regenerate documents")
            for person in leads.get("candidates", []):
                linked = profile(person.get("linkedin_url"))
                if linked and not checked and not any(profile(x["linkedin_url"]) == linked for x in contacts):
                    outcome = browser_outcomes.get(cid, {})
                    blocked = outcome.get("status") in {"uncertain", "review_required"}
                    browser.append({"company_id": cid, "company": row.company, **person,
                                    "reveal_allowed": not blocked,
                                    "prior_outcome": outcome.get("status"),
                                    "action": "human_review_no_reveal" if blocked else "match_or_resume_login"})
        waits = []; drafted = None
        if mail and ready:
            provider = provider or create_mail_provider(config, draft_only=True)
            if not hasattr(provider, "application_messages"):
                raise ValueError("Autopilot requires Outlook mailbox reconciliation before drafting")
            blocked, waits = reconcile_mail(config, state, provider, rows)
            allowed = {cid for cid in ready if any(c["email"] and c["email_status"] == "verified"
                       and not c["suppressed"] and c["contact_id"] not in blocked
                       for c in state.list_contacts(cid))}
            # Finish only previously reserved drafts that have never reached the
            # provider. Failed/uncertain operations require review, never retry.
            for batch in state.list_batches():
                if (batch["status"] == "pending_approval"
                        and all(m["company_id"] in allowed and m["contact_id"] not in blocked for m in batch["payload"]["messages"])
                        and not state.list_messages(batch["batch_id"])):
                    drafted = draft_email_batch(config, state, batch["batch_id"], provider)
            batch = create_email_batch(config, state, company_ids=allowed,
                                       excluded_contact_ids=blocked) if allowed else {"batch_id": None}
            if batch["batch_id"]:
                payload = batch["payload"]
                preview = {**payload, "messages": [{**m, "attachments": [
                    {k: v for k, v in a.items() if k != "path"} for a in m["attachments"]]} for m in payload["messages"]]}
                repo.publish([write_json(repo.path / "batches" / f"{batch['batch_id']}.json", preview)],
                             "Publish supervised autopilot draft preview")
                drafted = draft_email_batch(config, state, batch["batch_id"], provider)
                actual = provider.application_messages()
                for message in batch["payload"]["messages"]:
                    if not any(x.get("isDraft") and x.get("subject") == message["subject"] and x.get("hasAttachments")
                               and any(t.get("emailAddress", {}).get("address", "").casefold() == message["recipient"].casefold()
                                       for t in x.get("toRecipients", [])) for x in actual):
                        errors.append(f"{message['company_id']}: draft not confirmed with attachments; review before retry")
        refresh_linkedin_queue(config, state)
        result = {"research_export": research, "packaged": packaged, "browser_results_imported": imported,
                  "browser_contacts_pending": len(browser), "stages": stages, "mailbox": waits,
                  "waiting_for_contacts": contact_waits, "drafts": drafted, "errors": errors, "sent": 0}
        write_json(work / "browser_queue.json", {"instructions": BROWSER_RULES,
            "max_new_reveal_attempts": max_reveals, "tasks": browser,
            "result_file": str(work / "browser_results.json"), "result_schema": {"results": [{
                "company_id": "ID", "linkedin_url": "researched profile", "status": "verified|no_email|no_match|login_required|security_check_required|uncertain|review_required",
                "email": "observed work email or empty", "role": "observed title", "apollo_person_id": "observed ID",
                "apollo_contact_id": "observed ID", "source_url": "https://app.apollo.io/#/contacts/ID",
                "verification_label": "Verified in Apollo", "catch_all": False, "note": "evidence and uncertainty"}]}})
        prompt = (f"Continue private {repo.repository} on main. Pull first. Read README and preserve exact IDs.\n"
                  f"Research only IDs {stages['research']}: write research/companies/<ID>.json and research/contact_candidates/<ID>.json with real evidenced LinkedIn /in/ URLs.\n"
                  f"Prepare CV/proposal documents for IDs {stages['documents']} from their complete inputs.\n"
                  f"Finalise outreach/audits for IDs {stages['outreach']} using contacts.json; where contacts are missing, finish documents and wait.\n"
                  "Follow each packaged cv_tailoring_prompt.md exactly, including My background in... P1–P3, evidence, one full A4 CV page and correct recipient filenames. Render actual committed binaries.\n"
                  "Use only verified unsuppressed work emails from contacts.json. For LinkedIn without email create linkedin_outreach.json for manual use. Record catch-all and group/hiring uncertainty.\n"
                  "Commit structured outputs and current-input-hash ready manifests only for genuinely final email packages. Do not change inputs or other batches. Do not send any messages.\n")
        # Stable handoffs need no model to rewrite them on every invocation.
        if any(stages.values()):
            handoff = work / "chatgpt_handoff.txt"
            changed = not handoff.exists() or handoff.read_text() != prompt
            if changed: handoff.write_text(prompt, encoding="utf-8")
            result["chatgpt_handoff"] = str(handoff)
            result['handoff_changed'] = changed
        else:
            (work/'chatgpt_handoff.txt').unlink(missing_ok=True)
        write_json(work / "status.json", result)
        return result
    finally:
        if not _lock_held: lock.release()


def watch(config, *, mail=True, max_reveals=3, interval=60, emit=print, sleep=time.sleep,
          repo=None, state=None, provider=None):
    """Poll Git/workbook changes cheaply; reuse one in-memory mail connection.

    An exception stops the watcher. In particular, do not repeatedly request login,
    spend enrichment credits or retry an uncertain write while nobody is watching.
    """
    repo = repo or PrivateRepository(config)
    state = state or StateStore(config['paths']['database']); state.initialize()
    lock = SingleInstanceLock(config['paths']['lock_file']); lock.acquire()
    previous = None
    class LazyMailbox:
        def __init__(self): self.provider = provider
        def _get(self):
            if self.provider is None: self.provider = create_mail_provider(config, draft_only=True)
            return self.provider
        def application_messages(self): return self._get().application_messages()
        def create_draft(self, **kwargs): return self._get().create_draft(**kwargs)
    mailbox = LazyMailbox()
    try:
        while not Path(config['paths']['stop_file']).exists():
            repo.sync()
            def fingerprint(path):
                path = Path(path)
                return (path.stat().st_mtime_ns, path.stat().st_size) if path.is_file() else None
            def key():
                return (command('git','rev-parse','HEAD',cwd=repo.path),
                        fingerprint(config['paths']['workbook']),
                        fingerprint(Path(config['paths']['generated_dir'])/'autopilot/browser_results.json'))
            current = key()
            if current != previous:
                result = run_cycle(config,repo=repo,state=state,provider=mailbox,
                                   mail=mail,max_reveals=max_reveals,_lock_held=True,_synced=True)
                emit(json.dumps(result,indent=2))
                previous = key()  # include our own workbook/private commits
            sleep(max(10, interval))
    finally:
        lock.release()
