"""Explicit research -> package -> contacts -> collect stages for japp_data."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .approvals import eligible_companies
from .document_generation import build_input_package, expected_filename, safe_component, validate_docx
from .private_repository import PrivateRepository, inside
from .state import StateStore, stable_hash
from .sync import _load_company_registry, _parse_record, normalize_company_name, normalize_id, run_sync
from .workbook import update_pipeline_status


CONTRACT = """# JAPP private exchange

The public sa-anyan/japp repository contains code. This private repository holds application data.
Do not place passwords, OAuth tokens, Apollo API keys or browser profiles here.

## Research stage
Read requests/<batch-id>/companies.json. Research only its listed companies.
Write research/companies/<company_id>.json, one JSON object per company using schema_version "1.0".
Required fields: company_id, company, business_model, entry_level_fit, current_workflow,
opportunity (all text except ID), proposed_product {name, description}, confidence
(low/medium/high), sources (array of {url, supports, accessed_at}).
Optional: website (official company URL), role and job_description (only a verified vacancy).
Preserve the exact ID/name pair. Cite evidence, label unknowns, and never invent an internal
workflow, tool gap, vacancy, contact, or financial saving. Commit research on main.

## Document stage
For each companies/<ID>_<Company>/inputs folder, read input_package.json,
master_cv.docx, research.json, all prompts, and TASK.md. Read contacts.json beside inputs
when available. Never infer candidate credentials from company research.
Write final files under the sibling outputs folder. The filenames may be anything:
JAPP validates, renames and files them locally. Do not modify inputs.

Write outputs/outreach.json as {"messages": [{"recipient": "verified work email",
"subject": "final subject", "body": "final plain-text email"}]}.
Use the supplied outreach prompt as writing instructions, not literal email content.
Only use exact verified, unsuppressed recipients from contacts.json. No template labels,
unresolved placeholders or invented personalisation. Contacts may arrive after CV preparation:
finish CVs first, then add recipient-specific outreach when contacts.json is present.

Commit files first, then mark each company complete by committing outputs/manifest.json:
{"schema_version":"1.0", "ready":true, "company_id":"ID", "company":"Exact Company",
 "role":"role from input package", "input_hash":"from input package",
 "cv":"your-cv.docx", "cover_letter":"your-letter.docx", "outreach":"outreach.json"}
Paths are relative to outputs, must stay within it and must not be symlinks.
The ready flag means files are final; partial files without a ready manifest are ignored.
Preserve all working hyperlinks and verify final CV layout and evidence before marking ready.
JAPP checks DOCX readability, company/role content and identity; it cannot certify visual quality.
Commit the completed structure on main. Do not send emails from ChatGPT.
"""

TASK = """# Prepare final application documents

Use master_cv.docx as the sole source of candidate facts. Follow the supplied CV layout
and evidence rules. The input package's company research supplies context, never credentials.

For an advertised vacancy, apply the weighted JD algorithm in the CV prompt.
For a speculative approach (no verified JD), use this separate speculative workflow:
select evidenced CV experience relevant to the researched business and proposed contribution;
do not invent a JD, calculate an ATS/JD score, or describe proposed work as completed work.
This speculative branch replaces JD-dependent steps, including the instruction to stop and ask
for a speculative workflow. Retain the CV prompt's accuracy, formatting and validation rules.

Generate a CV DOCX, a concise cover-letter/proposal DOCX using proposal_prompt.md, and
recipient-specific final emails using outreach_template.md and contacts.json. Never send them.
If contacts have not arrived, prepare the documents and wait to finalise outreach/manifest.
Follow the private repository README's output contract exactly. Copy input_hash from
input_package.json to the output manifest so stale results cannot enter the mailing workflow.
"""


def write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def approved(config: dict[str, Any]) -> list:
    paths = config["paths"]
    rows = eligible_companies(paths["workbook"], paths["registry"])
    registry = _load_company_registry(Path(paths["registry"]))
    seen = set()
    for row in rows:
        if row.company_id in seen or not row.company or not re_safe_id(row.company_id):
            raise ValueError(f"Missing identity, unsafe ID or duplicate approval: {row.company_id}")
        seen.add(row.company_id)
        registered = registry.get(row.company_id)
        if registered and normalize_company_name(registered) != normalize_company_name(row.company):
            raise ValueError(f"Registry/company mismatch for {row.company_id}")
    return rows


def re_safe_id(value: str) -> bool:
    return bool(value) and all(c.isalnum() or c in "_-" for c in value)


def company_folder(root: Path, company_id: str, company: str) -> Path:
    return inside(root, f"companies/{company_id}_{safe_component(company)}")


def export_research(config: dict[str, Any], repo: PrivateRepository) -> dict[str, Any]:
    rows = approved(config)
    # Capture source context by ID rather than spreadsheet row position.
    book = load_workbook(config["paths"]["workbook"], read_only=True, data_only=True)
    try:
        sheet = book["01 Companies"]
        headers = list(next(sheet.values))
        id_column = headers.index("ID Number")
        metadata = {normalize_id(row[id_column]): dict(zip(headers, row))
                    for row in sheet.iter_rows(min_row=2, values_only=True)}
    finally:
        book.close()
    companies = []
    for row in rows:
        context = metadata.get(row.company_id, {})
        name = context.get("Company")
        if name and normalize_company_name(str(name)) != normalize_company_name(row.company):
            raise ValueError(f"01 Companies/execution identity mismatch for {row.company_id}")
        companies.append({"company_id": row.company_id, "company": row.company, "context": context})
    if not companies:
        return {"companies": 0, "published": False}
    batch_id = stable_hash(companies)[:16]
    request = inside(repo.path, f"requests/{batch_id}/companies.json")
    write_json(request, {"schema_version": "1.0", "batch_id": batch_id, "companies": companies})
    readme = inside(repo.path, "README.md")
    readme.write_text(CONTRACT, encoding="utf-8")
    repo.publish([request, readme], f"Export approved research request {batch_id}")
    for row in rows:
        update_pipeline_status(config["paths"]["workbook"], row.company_id, "Exported")
    return {"companies": len(companies), "batch_id": batch_id, "published": True,
            "next": f"Ask ChatGPT to research requests/{batch_id}/companies.json and commit research/companies JSON."}


def package_inputs(config: dict[str, Any], repo: PrivateRepository, state: StateStore) -> dict[str, Any]:
    rows = approved(config)
    research_dir = inside(repo.path, "research/companies")
    records = {}
    for path in sorted(research_dir.glob("*.json")):
        inside(repo.path, path.relative_to(repo.path).as_posix())
        parsed = _parse_record(path)
        company_id = parsed["company_id"]
        if company_id in records:
            raise ValueError(f"Duplicate research ID {company_id}")
        records[company_id] = (path, json.loads(path.read_text(encoding="utf-8")))
    files, waiting, packaged = [], [], []
    for row in rows:
        if row.company_id not in records:
            waiting.append(f"{row.company_id}: awaiting research")
            continue
        research_path, record = records[row.company_id]
        if normalize_company_name(record["company"]) != normalize_company_name(row.company):
            raise ValueError(f"Research/company mismatch for {row.company_id}")
        update_pipeline_status(config["paths"]["workbook"], row.company_id, "Researched")
        private_config = {**config, "paths": {**config["paths"], "research_dir": research_dir}}
        package = build_input_package(row.company_id, private_config,
                                      role=record.get("role", ""), job_description=record.get("job_description", ""))
        if package["missing_inputs"]:
            waiting.append(f"{row.company_id}: missing {', '.join(package['missing_inputs'])}")
            update_pipeline_status(config["paths"]["workbook"], row.company_id, "Awaiting User Inputs")
            continue
        folder = company_folder(repo.path, row.company_id, row.company)
        inputs = inside(repo.path, f"{folder.relative_to(repo.path).as_posix()}/inputs")
        assets = {"master_cv.docx": Path(package["master_cv_path"]).read_bytes(),
                  "research.json": research_path.read_bytes(), "TASK.md": TASK.encode()}
        for name in ("cv_tailoring_prompt.md", "proposal_prompt.md", "outreach_template.md"):
            assets[name] = (Path(config["paths"]["prompts_dir"]) / name).read_bytes()
        # The public source repository and absolute private local paths are not uploaded.
        remote_package = {k: v for k, v in package.items() if k not in {"package_path", "output_dir", "master_cv_path"}}
        remote_package["master_cv_path"] = "master_cv.docx"
        digest = stable_hash({"package": remote_package,
                              "assets": {name: hashlib.sha256(data).hexdigest() for name, data in assets.items()}})
        remote_package["input_hash"] = digest
        current = state.get_company(row.company_id)
        old_hash = state.published_input_hash(row.company_id)
        if current and current["state"] in {"DOCUMENTS_READY", "CONTACTS_PENDING", "CONTACTS_READY", "EMAIL_DRAFTED",
                                              "AWAITING_BATCH_APPROVAL", "EMAIL_QUEUED", "EMAIL_SUBMITTED", "COMPLETED"}:
            if old_hash != digest:
                raise ValueError(f"Company {row.company_id} has downstream work; cancel/archive it before changing inputs")
        inputs.mkdir(parents=True, exist_ok=True)
        for name, data in assets.items():
            target = inside(inputs, name)
            target.write_bytes(data)
            files.append(target)
        files.append(write_json(inside(inputs, "input_package.json"), remote_package))
        packaged.append((row, digest))
    if files:
        readme = inside(repo.path, "README.md")
        readme.write_text(CONTRACT, encoding="utf-8")
        files.append(readme)
        repo.publish(files, "Publish approved company CV inputs and prompts")
        run_sync(research_dir, config["paths"]["workbook"], config["paths"]["manifest"],
                 config["paths"]["backup_dir"], registry_path=config["paths"]["registry"])
        from .orchestrator import _set_approved_state
        for row, digest in packaged:
            state.save_input_hash(row.company_id, digest)
            _set_approved_state(state, row)
            current = state.get_company(row.company_id)
            if current["state"] == "APPROVED":
                state.transition(row.company_id, row.company, "APPLICATION_PACKAGE_READY")
            if state.get_company(row.company_id)["state"] == "APPLICATION_PACKAGE_READY":
                state.transition(row.company_id, row.company, "AWAITING_AI_OUTPUT")
            update_pipeline_status(config["paths"]["workbook"], row.company_id,
                                   state.get_company(row.company_id)["state"])
            state.record_event("private_inputs_published", row.company_id, {"repository": repo.repository})
    return {"packaged": len(packaged), "waiting": waiting,
            "next": "Ask ChatGPT to prepare documents from companies/*/inputs. Run exchange contacts while it works."}


def _install(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != source.read_bytes():
            raise ValueError(f"Local document differs; archive it before importing a revision: {target}")
    else:
        shutil.copyfile(source, target)


def collect_outputs(config: dict[str, Any], repo: PrivateRepository, state: StateStore) -> dict[str, Any]:
    collected, waiting = [], []
    for row in approved(config):
        folder = company_folder(repo.path, row.company_id, row.company)
        manifest_path = inside(folder, "outputs/manifest.json")
        if not manifest_path.is_file():
            waiting.append(f"{row.company_id}: awaiting ready output manifest")
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("ready") is not True:
            waiting.append(f"{row.company_id}: outputs not ready")
            continue
        package = json.loads(inside(folder, "inputs/input_package.json").read_text(encoding="utf-8"))
        if (manifest.get("schema_version") != "1.0" or str(manifest.get("company_id")) != row.company_id or manifest.get("company") != row.company
                or manifest.get("role") != package["role"] or manifest.get("input_hash") != package["input_hash"]):
            raise ValueError(f"Output identity or input version mismatch for {row.company_id}")
        if state.published_input_hash(row.company_id) != package["input_hash"]:
            raise ValueError(f"Company {row.company_id} inputs differ from the locally published version")
        output = inside(folder, "outputs")
        cv = inside(output, manifest["cv"])
        letter = inside(output, manifest["cover_letter"])
        outreach = inside(output, manifest["outreach"])
        role = package["role"]
        for doc in (cv, letter):
            validate_docx(doc, row.company, role if package["opportunity_type"] == "advertised_vacancy" else "")
        messages = json.loads(outreach.read_text(encoding="utf-8"))
        from .outreach import validate_final_outreach
        validate_final_outreach(messages, state.list_contacts(row.company_id))
        local = Path(config["paths"]["generated_dir"]) / f"{row.company_id}_{safe_component(row.company)}"
        targets = [(cv, local / expected_filename(row.company, role, "CV")),
                   (letter, local / expected_filename(row.company, role, "Cover Letter")),
                   (outreach, local / "outreach.json")]
        # Preflight the whole pair before copying any files.
        for source, target in targets:
            if target.exists() and target.read_bytes() != source.read_bytes():
                raise ValueError(f"Local document differs; archive before importing revision: {target}")
        current = state.get_company(row.company_id)
        if not current or current["state"] in {"PENDING_RESEARCH", "RESEARCH_READY", "PENDING_APPROVAL", "APPROVED", "FAILED", "CANCELLED"}:
            raise ValueError(f"Publish input packages before collecting company {row.company_id}")
        for source, target in targets:
            _install(source, target)
        current = state.get_company(row.company_id)
        if current["state"] in {"APPLICATION_PACKAGE_READY", "AWAITING_AI_OUTPUT"}:
            state.transition(row.company_id, row.company, "DOCUMENTS_READY")
        if state.get_company(row.company_id)["state"] == "DOCUMENTS_READY":
            state.transition(row.company_id, row.company, "CONTACTS_PENDING")
        if state.get_company(row.company_id)["state"] == "CONTACTS_PENDING":
            state.transition(row.company_id, row.company, "CONTACTS_READY")
        update_pipeline_status(config["paths"]["workbook"], row.company_id,
                               state.get_company(row.company_id)["state"])
        state.record_event("private_outputs_collected", row.company_id, {"input_hash": package["input_hash"]})
        collected.append(row.company_id)
    return {"collected": collected, "waiting": waiting}
