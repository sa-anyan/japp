"""Durable SQLite state, audit events, and idempotency records."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PIPELINE_STATES = {
    "PENDING_RESEARCH",
    "RESEARCH_READY",
    "PENDING_APPROVAL",
    "APPROVED",
    "APPLICATION_PACKAGE_READY",
    "AWAITING_AI_OUTPUT",
    "DOCUMENTS_READY",
    "CONTACTS_PENDING",
    "CONTACTS_READY",
    "EMAIL_DRAFTED",
    "AWAITING_BATCH_APPROVAL",
    "EMAIL_QUEUED",
    "EMAIL_SUBMITTED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
}

TRANSITIONS = {
    "PENDING_RESEARCH": {"RESEARCH_READY", "FAILED", "CANCELLED"},
    "RESEARCH_READY": {"PENDING_APPROVAL", "APPROVED", "FAILED", "CANCELLED"},
    "PENDING_APPROVAL": {"APPROVED", "CANCELLED", "FAILED"},
    "APPROVED": {"APPLICATION_PACKAGE_READY", "CANCELLED", "FAILED"},
    "APPLICATION_PACKAGE_READY": {"AWAITING_AI_OUTPUT", "DOCUMENTS_READY", "FAILED", "CANCELLED"},
    "AWAITING_AI_OUTPUT": {"DOCUMENTS_READY", "FAILED", "CANCELLED"},
    "DOCUMENTS_READY": {"CONTACTS_PENDING", "CONTACTS_READY", "FAILED", "CANCELLED"},
    "CONTACTS_PENDING": {"CONTACTS_READY", "FAILED", "CANCELLED"},
    "CONTACTS_READY": {"EMAIL_DRAFTED", "FAILED", "CANCELLED"},
    "EMAIL_DRAFTED": {"AWAITING_BATCH_APPROVAL", "FAILED", "CANCELLED"},
    "AWAITING_BATCH_APPROVAL": {"EMAIL_QUEUED", "CANCELLED", "FAILED"},
    "EMAIL_QUEUED": {"EMAIL_SUBMITTED", "FAILED", "CANCELLED"},
    "EMAIL_SUBMITTED": {"COMPLETED", "FAILED"},
    "COMPLETED": set(),
    "FAILED": {"PENDING_RESEARCH", "APPROVED", "CANCELLED"},
    "CANCELLED": {"APPROVED"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class InvalidTransition(ValueError):
    pass


class StateStore:
    def __init__(self, database: Path | str):
        self.database = Path(database).expanduser()

    def _connect(self) -> sqlite3.Connection:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS companies (
                    company_id TEXT PRIMARY KEY,
                    company_name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS contacts (
                    contact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    linkedin_url TEXT NOT NULL DEFAULT '',
                    email_status TEXT NOT NULL DEFAULT 'unknown',
                    source TEXT NOT NULL,
                    contact_key TEXT NOT NULL UNIQUE,
                    suppressed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(company_id) REFERENCES companies(company_id)
                );
                CREATE TABLE IF NOT EXISTS imports (
                    file_hash TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS email_batches (
                    batch_id TEXT PRIMARY KEY,
                    snapshot_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    approved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS messages (
                    idempotency_key TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    company_id TEXT NOT NULL,
                    contact_id INTEGER NOT NULL,
                    recipient TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body_hash TEXT NOT NULL,
                    attachment_hashes TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider_id TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(batch_id) REFERENCES email_batches(batch_id),
                    FOREIGN KEY(company_id) REFERENCES companies(company_id),
                    FOREIGN KEY(contact_id) REFERENCES contacts(contact_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    company_id TEXT,
                    action TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS services (
                    service_name TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    last_started_at TEXT,
                    last_finished_at TEXT,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS handoffs (
                    company_id TEXT PRIMARY KEY,
                    company_name TEXT NOT NULL,
                    session_token TEXT NOT NULL,
                    package_path TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    master_cv_path TEXT NOT NULL,
                    downloads_dir TEXT NOT NULL,
                    download_baseline TEXT NOT NULL,
                    prompt_text TEXT NOT NULL,
                    proposal_text TEXT NOT NULL,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    cv_path TEXT,
                    cover_letter_path TEXT,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS enrichments (
                    person_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS exchange_inputs (
                    company_id TEXT PRIMARY KEY,
                    input_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def _record_event(self, db: sqlite3.Connection, action: str,
                      company_id: str | None = None, detail: Any = None) -> None:
        db.execute(
            "INSERT INTO events(occurred_at, company_id, action, detail) VALUES (?, ?, ?, ?)",
            (utc_now(), company_id, action, json.dumps(detail or {}, sort_keys=True)),
        )

    def record_event(self, action: str, company_id: str | None = None,
                     detail: Any = None) -> None:
        with self._connect() as db:
            self._record_event(db, action, company_id, detail)

    def transition(self, company_id: int | str, company_name: str, new_state: str,
                   error: str | None = None) -> str:
        if new_state not in PIPELINE_STATES:
            raise InvalidTransition(f"Unknown pipeline state: {new_state}")
        key = str(company_id)
        with self._connect() as db:
            row = db.execute("SELECT state FROM companies WHERE company_id = ?", (key,)).fetchone()
            old_state = row["state"] if row else "PENDING_RESEARCH"
            if old_state != new_state and new_state not in TRANSITIONS[old_state]:
                raise InvalidTransition(f"Cannot move company {key} from {old_state} to {new_state}")
            db.execute(
                """INSERT INTO companies(company_id, company_name, state, updated_at, last_error)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(company_id) DO UPDATE SET company_name=excluded.company_name,
                     state=excluded.state, updated_at=excluded.updated_at, last_error=excluded.last_error""",
                (key, company_name, new_state, utc_now(), error),
            )
            if old_state != new_state or error:
                self._record_event(
                    db, "state_transition", key,
                    {"from": old_state, "to": new_state, "error": error},
                )
        return new_state

    def get_company(self, company_id: int | str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM companies WHERE company_id = ?", (str(company_id),)).fetchone()
        return dict(row) if row else None

    def list_companies(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM companies ORDER BY company_id").fetchall()
        return [dict(row) for row in rows]

    def save_contact(self, company_id: int | str, company_name: str, *, full_name: str,
                     role: str = "", email: str = "", linkedin_url: str = "",
                     email_status: str = "unknown", source: str = "import") -> tuple[int, bool]:
        key = str(company_id)
        if not self.get_company(key):
            self.transition(key, company_name, "PENDING_RESEARCH")
        identity = email.strip().casefold() or linkedin_url.strip().casefold() or full_name.strip().casefold()
        contact_key = stable_hash([key, identity])
        with self._connect() as db:
            existing = db.execute(
                "SELECT contact_id FROM contacts WHERE contact_key = ?", (contact_key,)
            ).fetchone()
            if existing:
                return int(existing["contact_id"]), False
            cursor = db.execute(
                """INSERT INTO contacts(company_id, full_name, role, email, linkedin_url,
                   email_status, source, contact_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (key, full_name.strip(), role.strip(), email.strip(), linkedin_url.strip(),
                 email_status, source, contact_key, utc_now()),
            )
            contact_id = int(cursor.lastrowid)
            self._record_event(db, "contact_imported", key, {"contact_id": contact_id, "source": source})
        return contact_id, True

    def list_contacts(self, company_id: int | str | None = None) -> list[dict[str, Any]]:
        with self._connect() as db:
            if company_id is None:
                rows = db.execute("SELECT * FROM contacts ORDER BY company_id, contact_id").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM contacts WHERE company_id = ? ORDER BY contact_id", (str(company_id),)
                ).fetchall()
        return [dict(row) for row in rows]

    def set_contact_suppressed(self, contact_id: int, suppressed: bool = True) -> None:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE contacts SET suppressed=? WHERE contact_id=?",
                (int(suppressed), contact_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Contact {contact_id} was not found")
            self._record_event(db, "contact_suppression_changed", detail={
                "contact_id": contact_id, "suppressed": bool(suppressed),
            })

    def import_seen(self, file_hash: str) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1 FROM imports WHERE file_hash = ?", (file_hash,)).fetchone() is not None

    def mark_imported(self, file_hash: str, filename: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO imports(file_hash, filename, imported_at) VALUES (?, ?, ?)",
                (file_hash, filename, utc_now()),
            )

    def create_batch(self, batch_id: str, payload: dict[str, Any]) -> str:
        snapshot_hash = stable_hash(payload)
        with self._connect() as db:
            existing = db.execute(
                "SELECT snapshot_hash, status FROM email_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            if existing:
                if existing["snapshot_hash"] != snapshot_hash:
                    raise ValueError("Batch content changed; create a new batch requiring fresh approval")
                return existing["status"]
            db.execute(
                "INSERT INTO email_batches(batch_id, snapshot_hash, payload, status, created_at) "
                "VALUES (?, ?, ?, 'pending_approval', ?)",
                (batch_id, snapshot_hash, json.dumps(payload, sort_keys=True), utc_now()),
            )
            self._record_event(db, "batch_created", detail={"batch_id": batch_id, "snapshot_hash": snapshot_hash})
        return "pending_approval"

    def approve_batch(self, batch_id: str, expected_hash: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT snapshot_hash, status FROM email_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            if not row or row["snapshot_hash"] != expected_hash or row["status"] != "pending_approval":
                return False
            db.execute(
                "UPDATE email_batches SET status='approved', approved_at=? WHERE batch_id=?",
                (utc_now(), batch_id),
            )
            self._record_event(db, "batch_approved", detail={"batch_id": batch_id})
            return True

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM email_batches WHERE batch_id = ?", (batch_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def list_batches(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as db:
            if status is None:
                rows = db.execute("SELECT batch_id FROM email_batches ORDER BY created_at").fetchall()
            else:
                rows = db.execute(
                    "SELECT batch_id FROM email_batches WHERE status=? ORDER BY created_at", (status,)
                ).fetchall()
        return [self.get_batch(row["batch_id"]) for row in rows]

    def finish_batch(self, batch_id: str, status: str) -> None:
        if status not in {"approved", "sending", "completed", "partial", "cancelled"}:
            raise ValueError(f"Invalid batch status: {status}")
        with self._connect() as db:
            db.execute("UPDATE email_batches SET status=? WHERE batch_id=?", (status, batch_id))
            self._record_event(db, f"batch_{status}", detail={"batch_id": batch_id})

    def cancel_batch(self, batch_id: str) -> None:
        with self._connect() as db:
            row = db.execute("SELECT status FROM email_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if not row or row["status"] not in {"pending_approval", "approved", "partial"}:
                raise ValueError("Only a pending or partially delivered batch can be cancelled")
            in_flight = db.execute(
                "SELECT 1 FROM messages WHERE batch_id=? "
                "AND status IN ('submitting','sending','sent','uncertain') LIMIT 1",
                (batch_id,),
            ).fetchone()
            if in_flight:
                raise ValueError("Batch contains a sent or uncertain message and cannot be cancelled")
            db.execute("UPDATE email_batches SET status='cancelled' WHERE batch_id=?", (batch_id,))
            db.execute("UPDATE messages SET status='cancelled', updated_at=? WHERE batch_id=?", (utc_now(), batch_id))
            self._record_event(db, "batch_cancelled", detail={"batch_id": batch_id})

    def contact_has_message(self, contact_id: int) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM messages WHERE contact_id=? "
                "AND status IN ('submitting','drafted','sending','sent','uncertain') LIMIT 1",
                (contact_id,),
            ).fetchone()
        return row is not None

    def reserve_message(self, *, idempotency_key: str, batch_id: str, company_id: str,
                        contact_id: int, recipient: str, subject: str, body_hash: str,
                        attachment_hashes: list[str]) -> bool:
        timestamp = utc_now()
        with self._connect() as db:
            existing = db.execute(
                "SELECT status FROM messages WHERE contact_id=? "
                "AND status IN ('submitting','drafted','sending','sent','uncertain') LIMIT 1",
                (contact_id,),
            ).fetchone()
            if existing:
                return False
            cursor = db.execute(
                """INSERT OR IGNORE INTO messages(idempotency_key, batch_id, company_id,
                   contact_id, recipient, subject, body_hash, attachment_hashes, status,
                   created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'submitting', ?, ?)""",
                (idempotency_key, batch_id, company_id, contact_id, recipient, subject,
                 body_hash, json.dumps(attachment_hashes), timestamp, timestamp),
            )
            return cursor.rowcount == 1

    def list_messages(self, batch_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM messages WHERE batch_id=? ORDER BY created_at, idempotency_key",
                (batch_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def finish_message(self, idempotency_key: str, status: str, *, provider_id: str | None = None,
                       error: str | None = None) -> None:
        if status not in {"sent", "failed", "uncertain", "drafted", "cancelled"}:
            raise ValueError(f"Invalid message status: {status}")
        with self._connect() as db:
            db.execute(
                "UPDATE messages SET status=?, provider_id=?, last_error=?, updated_at=? "
                "WHERE idempotency_key=?",
                (status, provider_id, error, utc_now(), idempotency_key),
            )
            self._record_event(db, f"message_{status}", detail={"idempotency_key": idempotency_key})

    def begin_send(self, idempotency_key: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE messages SET status='sending', updated_at=? "
                "WHERE idempotency_key=? AND status='drafted'",
                (utc_now(), idempotency_key),
            )
            return cursor.rowcount == 1

    def daily_sent_count(self, day: str | None = None) -> int:
        day = day or datetime.now(timezone.utc).date().isoformat()
        with self._connect() as db:
            row = db.execute(
                "SELECT COUNT(*) AS count FROM messages WHERE status='sent' AND substr(updated_at,1,10)=?",
                (day,),
            ).fetchone()
        return int(row["count"])

    def service_status(self, name: str, status: str, error: str | None = None) -> None:
        now = utc_now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO services(service_name,status,last_started_at,last_finished_at,last_error)
                   VALUES(?,?,?,?,?) ON CONFLICT(service_name) DO UPDATE SET status=excluded.status,
                   last_started_at=CASE WHEN excluded.status='running' THEN excluded.last_started_at
                                        ELSE services.last_started_at END,
                   last_finished_at=CASE WHEN excluded.status!='running' THEN excluded.last_finished_at
                                         ELSE services.last_finished_at END,
                   last_error=excluded.last_error""",
                (name, status, now if status == "running" else None,
                 None if status == "running" else now, error),
            )

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM events ORDER BY event_id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def services(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM services ORDER BY service_name").fetchall()
        return [dict(row) for row in rows]

    def count_events_today(self, action: str) -> int:
        day = datetime.now(timezone.utc).date().isoformat()
        with self._connect() as db:
            row = db.execute(
                "SELECT COUNT(*) AS count FROM events WHERE action=? AND substr(occurred_at,1,10)=?",
                (action, day),
            ).fetchone()
        return int(row["count"])

    def enrichment(self, person_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM enrichments WHERE person_id=?", (person_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"]) if result["payload"] else None
        return result

    def published_input_hash(self, company_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT input_hash FROM exchange_inputs WHERE company_id=?", (company_id,)).fetchone()
        return row[0] if row else None

    def save_input_hash(self, company_id: str, input_hash: str) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO exchange_inputs VALUES(?,?,?) ON CONFLICT(company_id) DO UPDATE "
                       "SET input_hash=excluded.input_hash,updated_at=excluded.updated_at",
                       (company_id, input_hash, utc_now()))

    def reserve_enrichment(self, person_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO enrichments(person_id,status,updated_at) VALUES(?,'pending',?)",
                (person_id, utc_now()),
            )
            return cursor.rowcount == 1

    def finish_enrichment(self, person_id: str, payload: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("UPDATE enrichments SET status='completed',payload=?,updated_at=? WHERE person_id=?",
                       (json.dumps(payload), utc_now(), person_id))

    @staticmethod
    def _handoff_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["download_baseline"] = json.loads(result["download_baseline"])
        return result

    def active_handoff(self) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM handoffs WHERE status='waiting' ORDER BY started_at LIMIT 1"
            ).fetchone()
        return self._handoff_dict(row) if row else None

    def get_handoff(self, company_id: int | str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM handoffs WHERE company_id=?", (str(company_id),)).fetchone()
        return self._handoff_dict(row) if row else None

    def create_handoff(self, *, company_id: int | str, company_name: str,
                       session_token: str, package_path: str, output_dir: str,
                       master_cv_path: str, downloads_dir: str,
                       download_baseline: dict[str, Any], prompt_text: str,
                       proposal_text: str, role: str) -> dict[str, Any]:
        company_key = str(company_id)
        with self._connect() as db:
            active = db.execute(
                "SELECT company_id FROM handoffs WHERE status='waiting' AND company_id!=? LIMIT 1",
                (company_key,),
            ).fetchone()
            if active:
                raise ValueError(f"Company {active['company_id']} already has an active document handoff")
            db.execute(
                """INSERT INTO handoffs(company_id,company_name,session_token,package_path,
                   output_dir,master_cv_path,downloads_dir,download_baseline,prompt_text,
                   proposal_text,role,status,started_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,'waiting',?)
                   ON CONFLICT(company_id) DO UPDATE SET company_name=excluded.company_name,
                     session_token=excluded.session_token,package_path=excluded.package_path,
                     output_dir=excluded.output_dir,master_cv_path=excluded.master_cv_path,
                     downloads_dir=excluded.downloads_dir,download_baseline=excluded.download_baseline,
                     prompt_text=excluded.prompt_text,proposal_text=excluded.proposal_text,
                     role=excluded.role,status='waiting',started_at=excluded.started_at,
                     completed_at=NULL,cv_path=NULL,cover_letter_path=NULL,last_error=NULL""",
                (company_key, company_name, session_token, package_path, output_dir,
                 master_cv_path, downloads_dir, json.dumps(download_baseline, sort_keys=True),
                 prompt_text, proposal_text, role, utc_now()),
            )
            self._record_event(db, "document_handoff_started", company_key, {"package": package_path})
        return self.get_handoff(company_key)

    def finish_handoff(self, company_id: int | str, *, cv_path: str | None = None,
                       cover_letter_path: str | None = None, error: str | None = None) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE handoffs SET status=?,completed_at=?,cv_path=?,cover_letter_path=?,last_error=?
                   WHERE company_id=?""",
                ("failed" if error else "completed", utc_now(), cv_path, cover_letter_path,
                 error, str(company_id)),
            )
            self._record_event(
                db, "document_handoff_failed" if error else "document_handoff_completed",
                str(company_id), {"error": error, "cv_path": cv_path, "cover_letter_path": cover_letter_path},
            )

    def set_handoff_error(self, company_id: int | str, error: str | None) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE handoffs SET last_error=? WHERE company_id=? AND status='waiting'",
                (error, str(company_id)),
            )
            self._record_event(db, "document_handoff_notice", str(company_id), {"message": error})

    def list_handoffs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM handoffs ORDER BY started_at DESC").fetchall()
        return [self._handoff_dict(row) for row in rows]
