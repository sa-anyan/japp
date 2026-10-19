"""Discover approved-company decision makers and publish resumable contact results."""

from __future__ import annotations

import json
from typing import Any

from .exchange import approved, company_folder, write_json
from .private_repository import PrivateRepository, inside
from .providers.apollo import ApolloAPIProvider, domain
from .state import StateStore
from .sync import normalize_company_name
from .workbook import write_contacts, update_pipeline_status


def discover_contacts(config: dict[str, Any], repo: PrivateRepository, state: StateStore, *,
                      reveal_emails: bool, max_reveals: int,
                      provider: ApolloAPIProvider | None = None) -> dict[str, Any]:
    if not reveal_emails or max_reveals < 1:
        raise ValueError("Contact discovery requires --reveal-emails and a positive --max-reveals call budget")
    per_company = config["apollo"]["max_contacts_per_company"]
    if not isinstance(per_company, int) or not 1 <= per_company <= 5:
        raise ValueError("apollo.max_contacts_per_company must be between 1 and 5")
    provider = provider or ApolloAPIProvider(config)
    reveals, saved, waiting, files, ready_ids = 0, 0, [], [], []
    for row in approved(config):
        folder = company_folder(repo.path, row.company_id, row.company)
        research = inside(folder, "inputs/research.json")
        if not research.is_file():
            waiting.append(f"{row.company_id}: publish research inputs first")
            continue
        record = json.loads(research.read_text(encoding="utf-8"))
        if (str(record.get("company_id")) != row.company_id
                or normalize_company_name(record.get("company", "")) != normalize_company_name(row.company)):
            raise ValueError(f"Research identity mismatch for {row.company_id}")
        try:
            company_domain = domain(record.get("website") or "")
        except ValueError:
            waiting.append(f"{row.company_id}: official company website required in research")
            continue
        people = provider.search(company_domain, config["execution"]["target_titles"], per_company)
        for candidate in people:
            person_id = candidate.get("id")
            if not person_id:
                continue
            # Search can also match previous employers: require current employer
            # identity before spending credits, then check the enriched domain.
            organization = candidate.get("organization") or {}
            if normalize_company_name(organization.get("name", "")) != normalize_company_name(row.company):
                continue
            cached = state.enrichment(person_id)
            if cached:
                if cached["status"] != "completed":
                    waiting.append(f"{row.company_id}: enrichment {person_id} is uncertain; not retried")
                    continue
                result = cached["payload"]
            else:
                if reveals >= max_reveals:
                    waiting.append(f"{row.company_id}: reveal call budget reached")
                    break
                if not state.reserve_enrichment(person_id):
                    continue
                reveals += 1
                result = provider.enrich(person_id)
                state.finish_enrichment(person_id, result)
            person = result.get("person") or {}
            org = person.get("organization") or {}
            try:
                current_domain = domain(org.get("primary_domain") or org.get("website_url") or "")
            except ValueError:
                continue
            if current_domain != company_domain or person.get("id") != person_id:
                continue
            email = person.get("email") or ""
            if email and ("@" not in email or "email_not_unlocked" in email or "\n" in email or "\r" in email):
                email = ""
            name = person.get("name") or " ".join(filter(None, [person.get("first_name"), person.get("last_name")]))
            if not name:
                continue
            _, created = state.save_contact(
                row.company_id, row.company, full_name=name, role=person.get("title") or "",
                email=email, linkedin_url=person.get("linkedin_url") or "",
                email_status="verified" if email and person.get("email_status") == "verified" else "unknown",
                source=f"Apollo API:{person_id}",
            )
            saved += int(created)
        contacts = state.list_contacts(row.company_id)
        workbook_contacts = [{**c, "contact_status": "found" if c["email_status"] == "verified" else "unverified"}
                             for c in contacts if not c["suppressed"]]
        write_contacts(config["paths"]["workbook"], row.company_id, workbook_contacts[:5])
        if any(c["email"] and c["email_status"] == "verified" and not c["suppressed"] for c in contacts):
            ready_ids.append(row.company_id)
        files.append(write_json(inside(folder, "contacts.json"), {
            "company_id": row.company_id, "company": row.company, "domain": company_domain,
            "contacts": contacts,
        }))
    if files:
        repo.publish(files, "Publish approved company Apollo contacts")
        for company_id in ready_ids:
            update_pipeline_status(config["paths"]["workbook"], company_id, "Contacts Ready")
    from .linkedin_queue import refresh_linkedin_queue
    refresh_linkedin_queue({**config, "paths": {**config["paths"], "private_repo_dir": repo.path}}, state)
    return {"reveals": reveals, "contacts_saved": saved, "waiting": waiting,
            "next": "Ask ChatGPT to use contacts.json for final outreach and commit ready output manifests."}
