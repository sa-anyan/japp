"""Apollo CSV contact import; no scraping or credit-consuming API calls."""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .state import StateStore
from .sync import _load_company_registry, normalize_company_name, normalize_id
from .workbook import write_contacts


class ApolloUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Contact:
    company_id: str
    company: str
    full_name: str
    role: str = ""
    email: str = ""
    linkedin_url: str = ""
    email_status: str = "unknown"
    source: str = "Apollo CSV"

    @property
    def contact_status(self) -> str:
        if not self.email:
            return "no_email"
        if self.email_status == "verified":
            return "found"
        return "unverified"


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def _field(row: dict[str, str], *aliases: str) -> str:
    by_key = {_key(key): value.strip() for key, value in row.items() if key and value}
    return next((by_key[_key(alias)] for alias in aliases if by_key.get(_key(alias))), "")


def _email_status(row: dict[str, str], email: str) -> str:
    if not email:
        return "no_email"
    value = _field(row, "Email Verification Status", "Email Status", "Verification Status").casefold()
    if "unverified" in value or "guessed" in value or "unverified" in _field(row, "Email Status").casefold():
        return "unverified"
    if value in {"verified", "valid", "deliverable"}:
        return "verified"
    return "unknown"


def rank_contact(contact: Contact, target_titles: Iterable[str]) -> int:
    role = _key(contact.role)
    if not role:
        return 0
    score = 0
    for title in target_titles:
        token = _key(title)
        if token and token in role:
            score += 10 + min(len(token), 12)
    seniority_penalty = ("chiefexecutive", "founder", "president", "boardmember")
    if any(token in role for token in seniority_penalty) and not any(
        term in role for term in ("hiring", "talent", "people")
    ):
        score -= 8
    return score


def import_apollo_csv(path: Path | str, registry_path: Path | str,
                      target_titles: Iterable[str] = ()) -> list[Contact]:
    path = Path(path)
    registry = _load_company_registry(Path(registry_path))
    registry_by_name = {normalize_company_name(name): company_id for company_id, name in registry.items()}
    contacts = []
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        header_keys = {_key(header) for header in reader.fieldnames if header}
        has_person = any(key in header_keys for key in (_key("Full Name"), _key("Name"))) or {
            _key("First Name"), _key("Last Name")
        }.issubset(header_keys)
        has_company = any(
            key in header_keys
            for key in (_key("Company"), _key("Organization Name"), _key("Account Name"), _key("Company Name"))
        )
        if not has_person or not has_company:
            raise ValueError(f"CSV must include person name and company identity columns: {path}")
        for line_number, row in enumerate(reader, 2):
            name = _field(row, "Full Name", "Name")
            if not name:
                first = _field(row, "First Name")
                last = _field(row, "Last Name")
                name = " ".join(part for part in (first, last) if part)
            company = _field(row, "Company", "Organization Name", "Account Name", "Company Name")
            raw_id = _field(row, "Company ID", "ID Number", "company_id")
            company_id = normalize_id(raw_id) if raw_id else None
            if company_id:
                registered = registry.get(company_id)
                if not registered or normalize_company_name(registered) != normalize_company_name(company):
                    continue
            else:
                company_id = registry_by_name.get(normalize_company_name(company))
                if not company_id:
                    continue
                company = registry[company_id]

            if not name:
                continue
            email = _field(row, "Email", "Email Address")
            linkedin = _field(row, "LinkedIn URL", "Person LinkedIn", "LinkedIn")
            role = _field(row, "Title", "Job Title", "Role")
            contact = Contact(
                company_id=company_id,
                company=registry[company_id],
                full_name=name,
                role=role,
                email=email,
                linkedin_url=linkedin,
                email_status=_email_status(row, email),
                source=f"Apollo CSV:{path.name}:{line_number}",
            )
            contacts.append(contact)
    return sorted(contacts, key=lambda item: (-rank_contact(item, target_titles), item.full_name.casefold()))


def import_apollo_file(path: Path | str, *, registry_path: Path | str,
                       workbook_path: Path | str, state: StateStore,
                       target_titles: Iterable[str] = ()) -> dict[str, int]:
    path = Path(path)
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if state.import_seen(file_hash):
        return {"added": 0, "skipped": 0, "already_imported": 1}
    contacts = import_apollo_csv(path, registry_path, target_titles)
    grouped: dict[str, list[dict]] = {}
    for contact in contacts:
        contact_id, created = state.save_contact(
            contact.company_id,
            contact.company,
            full_name=contact.full_name,
            role=contact.role,
            email=contact.email,
            linkedin_url=contact.linkedin_url,
            email_status=contact.email_status,
            source=contact.source,
        )
        grouped.setdefault(contact.company_id, []).append({
            **asdict(contact),
            "contact_id": contact_id,
            "contact_status": contact.contact_status,
        })
    added = skipped = 0
    for company_id, company_contacts in grouped.items():
        result = write_contacts(workbook_path, company_id, company_contacts[:5])
        added += result["added"]
        skipped += result["skipped"] + max(0, len(company_contacts) - 5)
    state.mark_imported(file_hash, path.name)
    return {"added": added, "skipped": skipped, "already_imported": 0}


class ApolloProvider:
    """Interface for a permitted Apollo integration; disabled by default."""

    def search_contacts(self, company: str, titles: list[str]) -> list[Contact]:
        raise ApolloUnavailable(
            "Apollo API integration is unavailable. Import an authorised Apollo CSV export instead."
        )