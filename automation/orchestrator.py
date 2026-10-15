"""Approval-gated sequential company execution and local CSV import services."""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any, Callable

from .approvals import ApprovedCompany, eligible_companies, read_execution_rows
from .contacts import import_apollo_file
from .document_generation import DocumentValidationError, build_input_package, validate_docx
from .github_research import pull_research_records
from .state import StateStore
from .sync import run_sync
from .workbook import update_pipeline_status


ACTIVE_STATES = {
    "APPROVED", "APPLICATION_PACKAGE_READY", "AWAITING_AI_OUTPUT", "DOCUMENTS_READY",
    "CONTACTS_PENDING", "CONTACTS_READY", "EMAIL_DRAFTED", "AWAITING_BATCH_APPROVAL",
}
NEW_QUEUE_STATES = {None, "PENDING_RESEARCH", "RESEARCH_READY", "PENDING_APPROVAL", "APPROVED", "FAILED"}


def _stop_requested(config: dict[str, Any]) -> bool:
    return Path(config["paths"]["stop_file"]).exists()


def _set_approved_state(state: StateStore, company: ApprovedCompany) -> None:
    current = state.get_company(company.company_id)
    current_state = current["state"] if current else "PENDING_RESEARCH"
    if current_state == "PENDING_RESEARCH":
        state.transition(company.company_id, company.company, "RESEARCH_READY")
        current_state = "RESEARCH_READY"
    if current_state == "RESEARCH_READY":
        state.transition(company.company_id, company.company, "PENDING_APPROVAL")
        current_state = "PENDING_APPROVAL"
    if current_state in {"PENDING_APPROVAL", "FAILED", "CANCELLED"}:
        state.transition(company.company_id, company.company, "APPROVED")


def _mark_revoked(state: StateStore, workbook_path: Path, company_id: str) -> None:
    current = state.get_company(company_id)
    if current and current["state"] in ACTIVE_STATES:
        state.transition(company_id, current["company_name"], "CANCELLED")
        update_pipeline_status(workbook_path, company_id, "CANCELLED")


def _find_output_documents(config: dict[str, Any], company_id: str,
                           company: str) -> tuple[Path | None, Path | None]:
    folder = Path(config["paths"]["generated_dir"]) / f"{company_id}_{company}"
    if not folder.is_dir():
        safe = __import__("automation.document_generation", fromlist=["safe_component"]).safe_component(company)
        folder = Path(config["paths"]["generated_dir"]) / f"{company_id}_{safe}"
    cvs = sorted(folder.glob("*_CV.docx")) if folder.is_dir() else []
    letters = sorted(folder.glob("*_CoverLetter.docx")) if folder.is_dir() else []
    return (cvs[-1] if cvs else None, letters[-1] if letters else None)


def run_approved_queue(config: dict[str, Any], state: StateStore, *, dry_run: bool = False,
                       limit: int | None = None, only: str | None = None,
                       sleep: Callable[[float], None] = time.sleep,
                       random_uniform: Callable[[float, float], float] = random.uniform
                       ) -> dict[str, Any]:
    workbook_path = Path(config["paths"]["workbook"])
    registry_path = Path(config["paths"]["registry"])
    all_eligible = eligible_companies(workbook_path, registry_path)
    candidates = all_eligible
    if only:
        candidates = [company for company in candidates if company.company_id == str(only)]
    if dry_run:
        dry_run_cap = limit if limit is not None else config["execution"]["max_companies_per_day"]
        candidates = candidates[:max(0, dry_run_cap)]
        return {
            "planned": len(candidates),
            "companies": [
                {"company_id": item.company_id, "company": item.company,
                 "next_action": "prepare handoff package"}
                for item in candidates
            ],
            "written": False,
        }

    state.initialize()
    queued_today = state.count_events_today("company_queued")
    daily_remaining = max(0, config["execution"]["max_companies_per_day"] - queued_today)
    resumable = []
    new_candidates = []
    for company in candidates:
        current = state.get_company(company.company_id)
        current_state = current["state"] if current else None
        if current_state in NEW_QUEUE_STATES:
            new_candidates.append(company)
        elif current_state in ACTIVE_STATES:
            continue
    capacity = daily_remaining if limit is None else min(daily_remaining, max(0, limit))
    candidates = new_candidates[:capacity]
    processed = 0
    results = []
    for company in candidates:
        if _stop_requested(config):
            break
        current_approval = {
            item.company_id for item in eligible_companies(workbook_path, registry_path)
        }
        if company.company_id not in current_approval:
            _mark_revoked(state, workbook_path, company.company_id)
            continue
        try:
            _set_approved_state(state, company)
            state.record_event("company_queued", company.company_id)
            current = state.get_company(company.company_id)
            current_state = current["state"] if current else "APPROVED"
            package_path = None
            if current_state == "APPROVED":
                state.transition(company.company_id, company.company, "APPLICATION_PACKAGE_READY")
                update_pipeline_status(workbook_path, company.company_id, "APPLICATION_PACKAGE_READY")
                package = build_input_package(company.company_id, config)
                package_path = package["package_path"]
                if _stop_requested(config):
                    break
                step_pause = random_uniform(
                    config["execution"]["min_step_pause_seconds"],
                    config["execution"]["max_step_pause_seconds"],
                )
                if step_pause:
                    sleep(step_pause)
                state.transition(company.company_id, company.company, "AWAITING_AI_OUTPUT")
                update_pipeline_status(workbook_path, company.company_id, "AWAITING_AI_OUTPUT")

            cv_path, letter_path = _find_output_documents(config, company.company_id, company.company)
            if cv_path and letter_path:
                validate_docx(cv_path, company.company)
                validate_docx(letter_path, company.company)
                current = state.get_company(company.company_id)
                if current and current["state"] == "AWAITING_AI_OUTPUT":
                    state.transition(company.company_id, company.company, "DOCUMENTS_READY")
                    update_pipeline_status(workbook_path, company.company_id, "DOCUMENTS_READY")
                current = state.get_company(company.company_id)
                if current and current["state"] == "DOCUMENTS_READY":
                    state.transition(company.company_id, company.company, "CONTACTS_PENDING")
                    update_pipeline_status(workbook_path, company.company_id, "CONTACTS_PENDING")
                if state.list_contacts(company.company_id):
                    current = state.get_company(company.company_id)
                    if current and current["state"] == "CONTACTS_PENDING":
                        state.transition(company.company_id, company.company, "CONTACTS_READY")
                        update_pipeline_status(workbook_path, company.company_id, "CONTACTS_READY")

            current = state.get_company(company.company_id)
            results.append({
                "company_id": company.company_id,
                "state": current["state"],
                "package_path": package_path,
            })
            processed += 1
        except (OSError, ValueError, RuntimeError) as exc:
            current = state.get_company(company.company_id)
            if current and current["state"] not in {"COMPLETED", "CANCELLED", "FAILED"}:
                state.transition(company.company_id, company.company, "FAILED", str(exc))
                update_pipeline_status(workbook_path, company.company_id, f"FAILED: {type(exc).__name__}")
            results.append({"company_id": company.company_id, "state": "FAILED", "error": str(exc)})

        if processed < len(candidates) and not _stop_requested(config):
            pause = random_uniform(
                config["execution"]["min_company_pause_seconds"],
                config["execution"]["max_company_pause_seconds"],
            )
            if pause:
                sleep(pause)
    return {"planned": len(candidates), "processed": processed, "results": results, "written": True}


def monitor_handoffs(config: dict[str, Any], state: StateStore) -> dict[str, int]:
    state.initialize()
    approvals = {
        row.company_id: row
        for row in read_execution_rows(config["paths"]["workbook"], config["paths"]["registry"])
        if row.execution_status.strip().casefold() == "approved"
        and row.cv_tailoring.strip().casefold() == "start"
    }
    resumed = cancelled = 0
    for company_state in state.list_companies():
        if _stop_requested(config):
            break
        company_id = company_state["company_id"]
        if company_state["state"] not in {"AWAITING_AI_OUTPUT", "DOCUMENTS_READY", "CONTACTS_PENDING"}:
            continue
        company = approvals.get(company_id)
        if company is None:
            _mark_revoked(state, Path(config["paths"]["workbook"]), company_id)
            cancelled += 1
            continue
        try:
            cv_path, letter_path = _find_output_documents(config, company_id, company.company)
            current = state.get_company(company_id)
            if cv_path and letter_path and current["state"] == "AWAITING_AI_OUTPUT":
                validate_docx(cv_path, company.company)
                validate_docx(letter_path, company.company)
                state.transition(company_id, company.company, "DOCUMENTS_READY")
                update_pipeline_status(config["paths"]["workbook"], company_id, "DOCUMENTS_READY")
                current = state.get_company(company_id)
            if current and current["state"] == "DOCUMENTS_READY":
                state.transition(company_id, company.company, "CONTACTS_PENDING")
                update_pipeline_status(config["paths"]["workbook"], company_id, "CONTACTS_PENDING")
                current = state.get_company(company_id)
            if current and current["state"] == "CONTACTS_PENDING" and state.list_contacts(company_id):
                state.transition(company_id, company.company, "CONTACTS_READY")
                update_pipeline_status(config["paths"]["workbook"], company_id, "CONTACTS_READY")
            if current and current["state"] == "CONTACTS_READY":
                resumed += 1
        except (OSError, ValueError, RuntimeError) as exc:
            state.transition(company_id, company.company, "FAILED", str(exc))
            update_pipeline_status(config["paths"]["workbook"], company_id, f"FAILED: {type(exc).__name__}")
    return {"resumed": resumed, "cancelled": cancelled}


def sync_research(config: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    paths = config["paths"]
    pull = None
    if config.get("research", {}).get("pull_before_sync") and not dry_run:
        pull = pull_research_records(
            config["repository_root"],
            remote=config["research"]["remote"],
            branch=config["research"]["branch"],
        )
    result = run_sync(
        paths["research_dir"], paths["workbook"], paths["manifest"], paths["backup_dir"],
        dry_run=dry_run, registry_path=paths["registry"],
        source_commit=pull["commit"] if pull else None,
    )
    if pull:
        result["messages"].append(
            f"Research source {pull['remote']}/{pull['branch']} at {pull['commit']}; "
            f"updated {len(pull['updated'])} local JSON files."
        )
    return result


def import_pending_contacts(config: dict[str, Any], state: StateStore) -> dict[str, int]:
    import_dir = Path(config["paths"]["apollo_import_dir"])
    if not import_dir.is_dir():
        return {"files": 0, "added": 0, "skipped": 0, "failed": 0}
    counts = {"files": 0, "added": 0, "skipped": 0, "failed": 0}
    for path in sorted(import_dir.glob("*.csv")):
        if _stop_requested(config):
            break
        counts["files"] += 1
        try:
            result = import_apollo_file(
                path,
                registry_path=config["paths"]["registry"],
                workbook_path=config["paths"]["workbook"],
                state=state,
                target_titles=config["execution"]["target_titles"],
            )
            counts["added"] += result["added"]
            counts["skipped"] += result["skipped"]
        except (OSError, ValueError, RuntimeError) as exc:
            counts["failed"] += 1
            state.record_event("contact_import_failed", detail={
                "filename": path.name,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return counts