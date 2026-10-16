"""One terminal stage to collect, draft and optionally approve/send an email batch."""

from __future__ import annotations

from typing import Any

from .exchange import collect_outputs, write_json
from .outreach import (approve_email_batch, create_email_batch, draft_email_batch, send_approved_batch)
from .private_repository import PrivateRepository, inside
from .providers.factory import create_mail_provider
from .state import StateStore


def deliver(config: dict[str, Any], repo: PrivateRepository, state: StateStore, *, send: bool = False,
            provider=None) -> dict[str, Any]:
    if send and not config["mail"]["send_enabled"]:
        raise ValueError("Set mail.send_enabled=true before explicitly running exchange deliver --send")
    if send and provider is None and config["mail"]["provider"] == "mock":
        raise ValueError("Select a configured Outlook or Gmail provider before live delivery")
    collected = collect_outputs(config, repo, state)
    pending = [b for b in state.list_batches() if b["status"] in {"pending_approval", "approved", "partial"}]
    if len(pending) > 1:
        raise ValueError("Multiple pending batches; use existing batch commands to review/send or cancel them")
    if pending:
        batch = pending[0]
        batch_id = batch["batch_id"]
    else:
        created = create_email_batch(config, state)
        batch_id = created["batch_id"]
        if not batch_id:
            return {**collected, "batch_id": None, "excluded": created["excluded"]}
        batch = state.get_batch(batch_id)
    preview = {"batch_id": batch_id, "snapshot_hash": batch["snapshot_hash"], "messages": [
        {"company_id": m["company_id"], "company": m["company"], "recipient": m["recipient"],
         "subject": m["subject"], "body": m["body"],
         "attachments": [{"name": a["name"], "sha256": a["sha256"]} for a in m["attachments"]]}
        for m in batch["payload"]["messages"]
    ]}
    preview_path = write_json(inside(repo.path, f"batches/{batch_id}.json"), preview)
    repo.publish([preview_path], f"Publish outreach batch {batch_id} for review")
    provider = provider or create_mail_provider(config)
    drafted = draft_email_batch(config, state, batch_id, provider)
    result = {**collected, "batch_id": batch_id, "drafts": drafted,
              "preview": f"batches/{batch_id}.json", "sent": 0}
    if send:
        status = state.get_batch(batch_id)["status"]
        if status == "pending_approval" and not approve_email_batch(state, batch_id, config["paths"]["workbook"]):
            raise ValueError("Drafting was incomplete; the batch was not approved or sent")
        result["delivery"] = send_approved_batch(config, state, batch_id, provider, send_flag=True)
        result["sent"] = result["delivery"]["sent"]
    return result
