"""Email drafting and separately approved batch delivery."""

from __future__ import annotations

import csv
import hashlib
import json
import random
import re
import time
from pathlib import Path
from typing import Any, Callable

from .approvals import eligible_companies
from .document_generation import DocumentValidationError, safe_component, validate_docx
from .providers.mail import DeliveryUncertain, MailProvider
from .state import StateStore, stable_hash, utc_now
from .workbook import (
    contact_is_email_eligible,
    mark_contact_emailed,
    mark_execution_emailed,
    update_pipeline_status,
)


class BatchSafetyError(RuntimeError):
    pass


def validate_final_outreach(payload: dict[str, Any], contacts: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Validate final AI-returned copy, never treat a writing prompt as an email."""
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list) or not payload["messages"]:
        raise BatchSafetyError("outreach.json must contain a non-empty messages array")
    verified = {c["email"].strip().casefold() for c in contacts
                if c.get("email") and c["email_status"] == "verified" and not c.get("suppressed")}
    result = {}
    for message in payload["messages"]:
        if not isinstance(message, dict) or any(not isinstance(message.get(k), str) or not message[k].strip()
                                                for k in ("recipient", "subject", "body")):
            raise BatchSafetyError("Every final email requires recipient, subject and body")
        email = message["recipient"].strip().casefold()
        if email not in verified or email in result:
            raise BatchSafetyError("Final email recipient is duplicated, unknown, suppressed or unverified")
        if "\n" in message["subject"] or "\r" in message["subject"]:
            raise BatchSafetyError("Email subjects must be single-line")
        content = message["subject"] + "\n" + message["body"]
        if re.search(r"\{[a-zA-Z_][^{}\n]*\}|\[(?:recipient|company|hook|the pitch|value proposition|call to action|linkedin|portfolio|verified|your|name)[^\]]*\]",
                     content, re.IGNORECASE):
            raise BatchSafetyError("Final outreach contains unresolved prompt placeholders")
        result[email] = {key: message[key].strip() for key in ("subject", "body")}
    return result


def _read_template(path: Path) -> str:
    if not path.is_file():
        raise BatchSafetyError(f"Outreach template is missing: {path}")
    content = path.read_text(encoding="utf-8").strip()
    if not content or "add your approved outreach template" in content.casefold():
        raise BatchSafetyError("Fill in prompts/outreach_template.md before creating email drafts")
    return content


def _render(template: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        return values.get(match.group(1), match.group(0))
    rendered = re.sub(r"\{([a-z_]+)\}", replace, template)
    unresolved = re.findall(r"\{([a-z_]+)\}", rendered)
    if unresolved:
        raise BatchSafetyError(f"Outreach template has unsupported placeholders: {', '.join(sorted(set(unresolved)))}")
    return rendered


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _assets_for_company(config: dict[str, Any], company_id: str, company: str) -> tuple[list[Path], str]:
    folder = Path(config["paths"]["generated_dir"]) / f"{company_id}_{safe_component(company)}"
    if not folder.is_dir():
        raise BatchSafetyError(f"Generated document folder is missing for company {company_id}")
    cv_files = sorted(folder.glob("*_CV.docx"))
    letter_files = sorted(folder.glob("*_Cover Letter.docx"))
    if not cv_files or not letter_files:
        raise BatchSafetyError(f"CV and cover letter are required before drafting for {company_id}")
    cv_path, letter_path = cv_files[-1], letter_files[-1]
    validate_docx(cv_path, company)
    validate_docx(letter_path, company)
    if (folder / "outreach.json").is_file():
        return [cv_path, letter_path], ""
    intro_path = folder / "introduction.md"
    if not intro_path.is_file():
        raise BatchSafetyError(f"Manual AI introduction is missing: {intro_path}")
    introduction = intro_path.read_text(encoding="utf-8").strip()
    if not introduction:
        raise BatchSafetyError(f"Manual AI introduction is empty: {intro_path}")
    return [cv_path, letter_path], introduction


def create_email_batch(config: dict[str, Any], state: StateStore,
                       *, company_ids: set[str] | None = None) -> dict[str, Any]:
    approved = eligible_companies(config["paths"]["workbook"], config["paths"]["registry"])
    template = None
    messages = []
    skipped: list[str] = []
    contacts = state.list_contacts()

    for company_record in approved:
        if company_ids and company_record.company_id not in company_ids:
            continue
        company_id = company_record.company_id
        company = company_record.company
        saved_company = state.get_company(company_id)
        if saved_company and saved_company["state"] == "COMPLETED":
            skipped.append(f"{company_id}: company already completed in SQLite")
            continue
        company_contacts = [c for c in contacts if c["company_id"] == company_id]
        eligible_contacts = [
            contact for contact in company_contacts
            if contact["email"] and contact["email_status"] == "verified"
            and not contact["suppressed"] and not state.contact_has_message(contact["contact_id"])
            and contact_is_email_eligible(
                config["paths"]["workbook"], company_id,
                contact["full_name"], contact["role"], contact["email"],
            )
        ]
        if not eligible_contacts:
            skipped.append(f"{company_id}: no verified, unsuppressed, unused contacts")
            continue
        try:
            attachments, introduction = _assets_for_company(config, company_id, company)
            folder = Path(config["paths"]["generated_dir"]) / f"{company_id}_{safe_component(company)}"
            final_path = folder / "outreach.json"
            final_copy = validate_final_outreach(json.loads(final_path.read_text(encoding="utf-8")), company_contacts) if final_path.is_file() else None
            if final_copy is None and template is None:
                template = _read_template(Path(config["paths"]["prompts_dir"]) / "outreach_template.md")
        except (BatchSafetyError, DocumentValidationError) as exc:
            skipped.append(f"{company_id}: {exc}")
            continue
        attachment_records = [
            {"path": str(path), "name": path.name, "sha256": _sha256(path)}
            for path in attachments
        ]
        for contact in eligible_contacts[:5]:
            values = {
                "company": company,
                "name": contact["full_name"],
                "role": contact["role"],
                "introduction": introduction,
            }
            if final_copy is not None:
                copy = final_copy.get(contact["email"].strip().casefold())
                if copy is None:
                    skipped.append(f"{company_id}: no final outreach for contact {contact['contact_id']}")
                    continue
                body, subject = copy["body"], copy["subject"]
            else:
                body = _render(template, values)
                subject = config["mail"]["subject_template"].format(**values)
            identity = stable_hash([
                company_id,
                contact["contact_id"],
                hashlib.sha256(body.encode("utf-8")).hexdigest(),
                [item["sha256"] for item in attachment_records],
            ])
            messages.append({
                "idempotency_key": identity,
                "company_id": company_id,
                "company": company,
                "contact_id": contact["contact_id"],
                "recipient": contact["email"],
                "email_status": contact["email_status"],
                "subject": subject,
                "body": body,
                "body_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "attachments": attachment_records,
            })

    payload = {
        "version": 1,
        "messages": messages,
        "excluded": skipped,
        "proposed_schedule": {"daily_cap": config["mail"]["daily_send_limit"]},
    }
    if not messages:
        return {"batch_id": None, "payload": payload, "excluded": skipped}
    batch_id = stable_hash(payload)[:12]
    state.create_batch(batch_id, payload)
    for company_id in {message["company_id"] for message in messages}:
        company_state = state.get_company(company_id)
        if company_state and company_state["state"] == "CONTACTS_READY":
            state.transition(company_id, company_state["company_name"], "EMAIL_DRAFTED")
            state.transition(company_id, company_state["company_name"], "AWAITING_BATCH_APPROVAL")
            update_pipeline_status(config["paths"]["workbook"], company_id, "AWAITING_BATCH_APPROVAL")
    return {"batch_id": batch_id, "payload": payload, "excluded": skipped}


def draft_email_batch(config: dict[str, Any], state: StateStore, batch_id: str,
                      provider: MailProvider) -> dict[str, int]:
    batch = state.get_batch(batch_id)
    if not batch:
        raise BatchSafetyError(f"Unknown batch {batch_id}")
    payload = batch["payload"]
    drafted = skipped = failed = 0
    for index, message in enumerate(payload["messages"]):
        if Path(config["paths"]["stop_file"]).exists():
            skipped += len(payload["messages"]) - index
            break
        reserved = state.reserve_message(
            idempotency_key=message["idempotency_key"],
            batch_id=batch_id,
            company_id=message["company_id"],
            contact_id=message["contact_id"],
            recipient=message["recipient"],
            subject=message["subject"],
            body_hash=message["body_hash"],
            attachment_hashes=[item["sha256"] for item in message["attachments"]],
        )
        if not reserved:
            skipped += 1
            continue
        try:
            provider_id = provider.create_draft(
                recipient=message["recipient"],
                subject=message["subject"],
                body=message["body"],
                attachments=[Path(item["path"]) for item in message["attachments"]],
            )
            state.finish_message(message["idempotency_key"], "drafted", provider_id=provider_id)
            drafted += 1
        except DeliveryUncertain as exc:
            state.finish_message(message["idempotency_key"], "uncertain", error=str(exc))
            failed += 1
        except Exception as exc:
            state.finish_message(message["idempotency_key"], "failed", error=str(exc))
            failed += 1
    stored_messages = state.list_messages(batch_id)
    for company_id in {message["company_id"] for message in payload["messages"]}:
        expected = [m for m in payload["messages"] if m["company_id"] == company_id]
        actual = [m for m in stored_messages if m["company_id"] == company_id]
        if len(actual) == len(expected) and all(m["status"] == "drafted" for m in actual):
            update_pipeline_status(config["paths"]["workbook"], company_id, "Email Drafted")
    return {"drafted": drafted, "skipped": skipped, "failed": failed}


def approve_email_batch(state: StateStore, batch_id: str,
                        workbook_path: Path | str | None = None) -> bool:
    batch = state.get_batch(batch_id)
    if not batch:
        return False
    messages = state.list_messages(batch_id)
    expected_count = len(batch["payload"]["messages"])
    if expected_count == 0 or len(messages) != expected_count:
        return False
    if any(message["status"] != "drafted" for message in messages):
        return False
    approved = state.approve_batch(batch_id, batch["snapshot_hash"])
    if approved:
        state.finish_batch(batch_id, "approved")
        for company_id in {message["company_id"] for message in batch["payload"]["messages"]}:
            company = state.get_company(company_id)
            if company and company["state"] == "AWAITING_BATCH_APPROVAL":
                state.transition(company_id, company["company_name"], "EMAIL_QUEUED")
                if workbook_path:
                    update_pipeline_status(workbook_path, company_id, "EMAIL_QUEUED")
    return approved


def cancel_email_batch(state: StateStore, batch_id: str, provider: MailProvider) -> None:
    batch = state.get_batch(batch_id)
    if not batch:
        raise BatchSafetyError(f"Unknown batch {batch_id}")
    messages = state.list_messages(batch_id)
    if any(message["status"] in {"submitting", "sending", "sent", "uncertain"} for message in messages):
        raise BatchSafetyError("A batch with sent or uncertain delivery cannot be cancelled")
    for message in messages:
        if message["provider_id"] and message["status"] == "drafted":
            provider.delete_draft(message["provider_id"])
    state.cancel_batch(batch_id)


def append_outreach_log(config: dict[str, Any], row: dict[str, Any]) -> None:
    log_path = Path(config["paths"]["logs_dir"]) / "outreach_log.csv"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    exists = log_path.exists()
    fields = ["timestamp", "company_id", "recipient", "subject", "result"]
    with log_path.open("a", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


def send_approved_batch(config: dict[str, Any], state: StateStore, batch_id: str,
                        provider: MailProvider, *, send_flag: bool,
                        sleep: Callable[[float], None] = time.sleep,
                        random_uniform: Callable[[float, float], float] = random.uniform
                        ) -> dict[str, int]:
    if not send_flag or not config["mail"]["send_enabled"]:
        raise BatchSafetyError("Sending requires both --send and mail.send_enabled=true")
    batch = state.get_batch(batch_id)
    if not batch or batch["status"] not in {"approved", "partial"}:
        raise BatchSafetyError("Only a user-approved email batch can be sent")
    if stable_hash(batch["payload"]) != batch["snapshot_hash"]:
        raise BatchSafetyError("Approved batch snapshot changed; create a new batch and reapprove")

    database_messages = {message["idempotency_key"]: message for message in state.list_messages(batch_id)}
    sent = skipped = failed = 0
    sent_company_ids: set[str] = set()
    state.finish_batch(batch_id, "sending")

    messages = batch["payload"]["messages"]
    for index, message in enumerate(messages):
        if Path(config["paths"]["stop_file"]).exists():
            skipped += len(messages) - index
            break
        if state.daily_sent_count() >= config["mail"]["daily_send_limit"]:
            skipped += 1
            continue
        eligible = {
            row.company_id
            for row in eligible_companies(config["paths"]["workbook"], config["paths"]["registry"])
        }
        stored = database_messages.get(message["idempotency_key"])
        contact = next(
            (item for item in state.list_contacts()
             if item["contact_id"] == message["contact_id"]),
            None,
        )
        company_state = state.get_company(message["company_id"])
        if (
            not stored or stored["status"] != "drafted" or not stored["provider_id"]
            or message["company_id"] not in eligible
            or (company_state and company_state["state"] == "COMPLETED")
            or message["email_status"] != "verified"
            or not contact or contact["suppressed"]
            or contact["email_status"] != "verified"
            or contact["email"].strip().casefold() != message["recipient"].strip().casefold()
            or not contact_is_email_eligible(
                config["paths"]["workbook"], message["company_id"],
                contact["full_name"], contact["role"], message["recipient"],
            )
        ):
            skipped += 1
            continue
        if any(not Path(item["path"]).is_file() or _sha256(Path(item["path"])) != item["sha256"]
               for item in message["attachments"]):
            state.finish_message(message["idempotency_key"], "failed", error="Attachment changed after approval")
            failed += 1
            continue
        if not state.begin_send(message["idempotency_key"]):
            skipped += 1
            continue
        try:
            provider.send_draft(stored["provider_id"])
        except DeliveryUncertain as exc:
            state.finish_message(message["idempotency_key"], "uncertain", error=str(exc))
            failed += 1
            continue
        except Exception as exc:
            state.finish_message(message["idempotency_key"], "failed", error=str(exc))
            failed += 1
            continue

        state.finish_message(message["idempotency_key"], "sent", provider_id=stored["provider_id"])
        sent += 1
        try:
            mark_contact_emailed(config["paths"]["workbook"], message["company_id"], message["recipient"])
            company_state = state.get_company(message["company_id"])
            if company_state and company_state["state"] == "EMAIL_QUEUED":
                state.transition(message["company_id"], company_state["company_name"], "EMAIL_SUBMITTED")
                update_pipeline_status(
                    config["paths"]["workbook"], message["company_id"], "EMAIL_SUBMITTED"
                )
            append_outreach_log(config, {
                "timestamp": utc_now(), "company_id": message["company_id"],
                "recipient": message["recipient"], "subject": message["subject"], "result": "sent",
            })
        except Exception as exc:
            state.record_event("post_send_update_failed", message["company_id"], {
                "contact_id": message["contact_id"], "error": f"{type(exc).__name__}: {exc}",
            })
        sent_company_ids.add(message["company_id"])
        pause = random_uniform(
            config["mail"]["min_send_pause_seconds"],
            config["mail"]["max_send_pause_seconds"],
        )
        if pause:
            sleep(pause)

    final_messages = state.list_messages(batch_id)
    for company_id in sent_company_ids:
        company_messages = [item for item in final_messages if item["company_id"] == company_id]
        if company_messages and all(item["status"] == "sent" for item in company_messages):
            try:
                company_state = state.get_company(company_id)
                if company_state and company_state["state"] == "EMAIL_SUBMITTED":
                    state.transition(company_id, company_state["company_name"], "COMPLETED")
                    update_pipeline_status(config["paths"]["workbook"], company_id, "COMPLETED")
                mark_execution_emailed(config["paths"]["workbook"], company_id)
            except Exception as exc:
                state.record_event("post_send_status_update_failed", company_id, {
                    "error": f"{type(exc).__name__}: {exc}",
                })

    all_sent = bool(final_messages) and all(item["status"] == "sent" for item in final_messages)
    state.finish_batch(batch_id, "completed" if all_sent else "partial")
    return {"sent": sent, "skipped": skipped, "failed": failed}


def monitor_outreach_batches(config: dict[str, Any], state: StateStore,
                             provider_factory: Callable[[dict[str, Any]], MailProvider]) -> dict[str, int]:
    state.initialize()
    drafted = sent = pending = 0
    if config["mail"]["auto_draft"]:
        for company in state.list_companies():
            if company["state"] != "CONTACTS_READY":
                continue
            batch = create_email_batch(config, state, company_ids={company["company_id"]})
            if not batch["batch_id"]:
                continue
            current_batch = state.get_batch(batch["batch_id"])
            current_messages = state.list_messages(batch["batch_id"])
            expected = len(batch["payload"]["messages"])
            if current_batch and expected and len(current_messages) == expected and all(
                row["status"] == "drafted" for row in current_messages
            ):
                continue
            result = draft_email_batch(config, state, batch["batch_id"], provider_factory(config))
            drafted += result["drafted"]
            if result["drafted"] == expected:
                current = state.get_company(company["company_id"])
                if current and current["state"] == "CONTACTS_READY":
                    state.transition(company["company_id"], company["company_name"], "EMAIL_DRAFTED")
                    state.transition(company["company_id"], company["company_name"], "AWAITING_BATCH_APPROVAL")
    if config["mail"]["auto_send_approved_batches"] and config["mail"]["send_enabled"]:
        for batch in state.list_batches():
            if batch and batch["status"] in {"approved", "partial"}:
                result = send_approved_batch(
                    config, state, batch["batch_id"], provider_factory(config), send_flag=True
                )
                sent += result["sent"]
    else:
        pending = sum(1 for batch in state.list_batches() if batch and batch["status"] == "approved")
    return {"drafted": drafted, "sent": sent, "approved_batches_waiting": pending}
