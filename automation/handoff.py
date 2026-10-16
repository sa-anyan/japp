"""Attended ChatGPT handoff UI and safe Downloads-to-company document monitor."""

from __future__ import annotations

import hashlib
import html
import hmac
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from docx import Document

from .document_generation import DocumentValidationError, expected_filename, validate_docx
from .state import StateStore
from .workbook import update_pipeline_status


CV_TERMS = (
    "curriculum vitae", "resume", "professional experience", "work experience",
    "employment history", "education", "qualifications", "technical skills",
    "key skills", "career summary",
)
LETTER_TERMS = (
    "dear ", "cover letter", "i am writing", "i would welcome", "kind regards",
    "best regards", "sincerely", "yours faithfully", "proposal",
)
TEMP_SUFFIXES = (".crdownload", ".part", ".download", ".tmp")


def file_fingerprint(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def snapshot_downloads(downloads_dir: Path | str) -> dict[str, dict[str, int]]:
    directory = Path(downloads_dir).expanduser()
    if not directory.is_dir():
        return {}
    snapshot = {}
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file() or path.suffix.casefold() != ".docx":
            continue
        if path.name.startswith("~$") or path.name.casefold().endswith(TEMP_SUFFIXES):
            continue
        try:
            snapshot[str(path.resolve())] = file_fingerprint(path)
        except OSError:
            continue
    return snapshot


def extract_docx_text(path: Path | str) -> str:
    document = Document(path)
    parts = [paragraph.text for paragraph in document.paragraphs]
    parts.extend(cell.text for table in document.tables for row in table.rows for cell in row.cells)
    return "\n".join(part for part in parts if part)


def classify_document(path: Path | str, text: str) -> str | None:
    """Return cv/cover_letter only when the filename or content gives a clear signal."""
    path = Path(path)
    name = path.stem.casefold()
    if any(token in name for token in ("cover letter", "cover_letter", "coverletter", "proposal", "introduction")):
        return "cover_letter"
    if any(token in name for token in ("resume", "curriculum vitae", "_cv", " cv", "cv_")):
        return "cv"
    normalized = re.sub(r"\s+", " ", text.casefold())
    cv_score = sum(token in normalized for token in CV_TERMS)
    letter_score = sum(token in normalized for token in LETTER_TERMS)
    if cv_score >= 2 and cv_score > letter_score:
        return "cv"
    if letter_score >= 2 and letter_score > cv_score:
        return "cover_letter"
    return None


def _unique_destination(directory: Path, filename: str) -> Path:
    base = directory / filename
    if not base.exists():
        return base
    for version in range(2, 1000):
        candidate = base.with_name(f"{base.stem}__{version}{base.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Too many existing versions for {base.name}")


def move_without_overwrite(source: Path, destination: Path) -> Path:
    """Copy to a same-directory temporary file, atomically install, then remove source."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    target = _unique_destination(destination.parent, destination.name)
    temporary = None
    try:
        with source.open("rb") as incoming, tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.stem}-", suffix=target.suffix, delete=False
        ) as outgoing:
            temporary = Path(outgoing.name)
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        os.replace(temporary, target)
        temporary = None
        source.unlink()
        return target
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


class SupervisedChatGPTHandoff:
    def __init__(self, config: dict[str, Any], state: StateStore, *,
                 browser_open: Callable[[str], Any] = webbrowser.open,
                 open_file: Callable[[Path], Any] | None = None,
                 stable_polls: int = 2,
                 poll_seconds: float = 5.0):
        self.config = config
        self.state = state
        self.browser_open = browser_open
        self.open_file = open_file or self._open_file
        self.stable_polls = max(2, stable_polls)
        self.poll_seconds = poll_seconds
        self.server: ThreadingHTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self._stable: dict[str, tuple[dict[str, int], int]] = {}
        self._opened_sessions: set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _open_file(path: Path) -> None:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif system == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _ensure_server(self) -> None:
        if self.server:
            return
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _session(self):
                handoff = service.state.active_handoff()
                token = urlparse(self.path).path.strip("/").split("/")[0]
                if not handoff or not hmac.compare_digest(token, handoff["session_token"]):
                    self.send_error(404)
                    return None, ""
                route = "/".join(urlparse(self.path).path.strip("/").split("/")[1:])
                return handoff, route

            def _send(self, body: bytes, content_type: str = "text/html; charset=utf-8", status: int = 200):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                handoff, route = self._session()
                if not handoff:
                    return
                if route == "status":
                    body = json.dumps({
                        "company_id": handoff["company_id"],
                        "company": handoff["company_name"],
                        "status": handoff["status"],
                        "error": handoff["last_error"],
                    }).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8")
                    return
                if route:
                    self.send_error(404)
                    return
                self._send(service._render_page(handoff).encode("utf-8"))

            def do_POST(self):
                handoff, route = self._session()
                if not handoff:
                    return
                if route != "open-cv":
                    self.send_error(404)
                    return
                try:
                    service.open_file(Path(handoff["master_cv_path"]))
                    self._send(b"Master CV opened.", "text/plain; charset=utf-8")
                except Exception as exc:
                    self._send(str(exc).encode("utf-8"), "text/plain; charset=utf-8", 500)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def _render_page(self, handoff: dict[str, Any]) -> str:
        token = html.escape(handoff["session_token"], quote=True)
        company = html.escape(handoff["company_name"])
        role = html.escape(handoff["role"])
        prompt = html.escape(handoff["prompt_text"])
        proposal = html.escape(handoff["proposal_text"])
        cv_name = html.escape(Path(handoff["master_cv_path"]).name)
        status_url = f"/{token}/status"
        return f"""<!doctype html>
    <html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>JAPP handoff - {company}</title>
    <style>
    body{{font:16px system-ui,sans-serif;max-width:920px;margin:32px auto;padding:0 20px;color:#1b2430;background:#f5f7fa}}
    main{{background:white;border:1px solid #d5dbe3;border-radius:8px;padding:24px}}
    h1{{font-size:24px;margin-top:0}} h2{{font-size:18px;margin-top:26px}}
    textarea{{box-sizing:border-box;width:100%;min-height:220px;padding:12px;border:1px solid #9aa6b2;border-radius:4px;font:14px/1.5 ui-monospace,monospace;white-space:pre-wrap}}
    button,a.button{{display:inline-block;margin:10px 8px 10px 0;padding:10px 14px;border:0;border-radius:4px;background:#145a80;color:white;text-decoration:none;font:inherit;cursor:pointer}}
    .muted{{color:#526170}} #status{{font-weight:600}}
    </style><main>
    <h1>JAPP document handoff</h1><p><b>{company}</b> · {role} · ID {html.escape(handoff['company_id'])}</p>
    <p class="muted">Copy a prepared prompt, attach the opened master CV in ChatGPT, submit it yourself, then download the resulting DOCX files. JAPP does not submit messages or read ChatGPT responses.</p>
    <p><button id="open-cv">Open master CV · {cv_name}</button><a class="button" href="https://chatgpt.com/" target="_blank" rel="noopener">Open ChatGPT</a></p>
    <h2>CV tailoring prompt</h2><textarea id="cv-prompt" readonly>{prompt}</textarea>
    <button onclick="copyText('cv-prompt')">Copy CV prompt</button>
    <h2>Proposal / introduction prompt</h2><textarea id="proposal-prompt" readonly>{proposal}</textarea>
    <button onclick="copyText('proposal-prompt')">Copy proposal prompt</button>
    <p id="status">Waiting for generated CV and cover letter in Downloads.</p>
    <p class="muted">JAPP matches only new stable DOCX files from this handoff, validates company content, then files them automatically. Do not rename or move downloads.</p>
    </main><script>
    async function copyText(id){{try{{await navigator.clipboard.writeText(document.getElementById(id).value);document.getElementById('status').textContent='Prompt copied.'}}catch(e){{document.getElementById('status').textContent='Clipboard unavailable; select and copy the prompt text.'}}}}
    document.getElementById('open-cv').addEventListener('click',async()=>{{const r=await fetch('/{token}/open-cv',{{method:'POST'}});document.getElementById('status').textContent=await r.text()}});
    async function refresh(){{try{{const r=await fetch('{status_url}');const d=await r.json();if(d.error)document.getElementById('status').textContent=d.error;else if(d.status==='completed')document.getElementById('status').textContent='Documents validated and filed.'}}catch(e){{}}}}
    setInterval(refresh,5000);refresh();
    </script></html>"""

    def start_for_package(self, package: dict[str, Any]) -> dict[str, Any] | None:
        if package.get("status") != "ready_for_attended_handoff":
            return None
        active = self.state.active_handoff()
        company_id = str(package["company_id"])
        if active:
            if active["company_id"] != company_id:
                return None
            handoff = active
        else:
            downloads_dir = Path(self.config["paths"]["downloads_dir"]).expanduser()
            baseline = snapshot_downloads(downloads_dir)
            handoff = self.state.create_handoff(
                company_id=company_id,
                company_name=package["company"],
                session_token=__import__("secrets").token_urlsafe(24),
                package_path=package["package_path"],
                output_dir=package["output_dir"],
                master_cv_path=package["master_cv_path"],
                downloads_dir=str(downloads_dir),
                download_baseline=baseline,
                prompt_text=package["prompts"]["cv_tailoring"],
                proposal_text=package["prompts"]["proposal"],
                role=package["role"],
            )
            output_path = Path(package["output_dir"]) / "chatgpt_handoff.md"
            output_path.write_text(
                f"# {package['company']} · {package['role']}\n\n"
                f"## CV tailoring prompt\n\n{handoff['prompt_text']}\n\n"
                f"## Proposal prompt\n\n{handoff['proposal_text']}\n",
                encoding="utf-8",
            )
        self._ensure_server()
        base_url = f"http://127.0.0.1:{self.server.server_port}/{handoff['session_token']}"
        if handoff["company_id"] not in self._opened_sessions:
            self._opened_sessions.add(handoff["company_id"])
            self.browser_open("https://chatgpt.com/")
            self.browser_open(base_url)
            print(f"JAPP handoff ready for {handoff['company_name']}: {base_url}")
        return {"url": base_url, "company_id": company_id, "company": handoff["company_name"]}
    
    def _new_downloads(self, handoff: dict[str, Any]) -> list[tuple[Path, dict[str, int]]]:
        baseline = handoff["download_baseline"]
        candidates = []
        directory = Path(handoff["downloads_dir"])
        if not directory.is_dir():
            self.state.set_handoff_error(handoff["company_id"], f"Downloads folder not found: {directory}")
            return []
        for path in directory.iterdir():
            if path.is_symlink() or not path.is_file() or path.suffix.casefold() != ".docx":
                continue
            if path.name.startswith("~$") or path.name.casefold().endswith(TEMP_SUFFIXES):
                continue
            try:
                resolved = path.resolve()
                signature = file_fingerprint(path)
            except OSError:
                continue
            prior = baseline.get(str(resolved))
            if prior == signature:
                continue
            key = str(resolved)
            previous_signature, count = self._stable.get(key, ({}, 0))
            count = count + 1 if previous_signature == signature else 1
            self._stable[key] = (signature, count)
            if count >= self.stable_polls:
                candidates.append((path, signature))
        return candidates
    
    @staticmethod
    def _company_matches(company: str, text: str) -> bool:
        return company.casefold() in text.casefold()
    
    def _move_pair(self, handoff: dict[str, Any], candidates: list[tuple[Path, str]]) -> bool:
        company = handoff["company_name"]
        role = handoff["role"]
        matched: dict[str, list[tuple[Path, str]]] = {"cv": [], "cover_letter": []}
        issues = []
        for source, kind in candidates:
            try:
                text = extract_docx_text(source)
                if not self._company_matches(company, text):
                    continue
                doc_type = classify_document(source, text)
                if not doc_type:
                    issues.append(f"Could not classify {source.name} as a CV or cover letter; file left in Downloads.")
                    continue
                validate_docx(source, company, role if role != "Speculative Approach" else "")
                matched[doc_type].append((source, text))
            except (OSError, ValueError) as exc:
                issues.append(f"Could not validate {source.name}; file left in Downloads: {exc}")
    
        if issues:
            self.state.set_handoff_error(handoff["company_id"], " ".join(issues))
        if not matched["cv"] or not matched["cover_letter"]:
            return False
    
        # Choose the newest validated version of each type; older/unrelated downloads stay untouched.
        cv_source = max((item[0] for item in matched["cv"]), key=lambda item: item.stat().st_mtime_ns)
        letter_source = max((item[0] for item in matched["cover_letter"]), key=lambda item: item.stat().st_mtime_ns)
        output_dir = Path(handoff["output_dir"])
        cv_name = expected_filename(company, role, "CV")
        letter_name = expected_filename(company, role, "Cover Letter")
        try:
            cv_destination = move_without_overwrite(cv_source, output_dir / cv_name)
            letter_destination = move_without_overwrite(letter_source, output_dir / letter_name)
        except (OSError, RuntimeError) as exc:
            self.state.set_handoff_error(handoff["company_id"], f"Could not file validated documents: {exc}")
            return False
    
        self.state.finish_handoff(
            handoff["company_id"], cv_path=str(cv_destination), cover_letter_path=str(letter_destination)
        )
        state_record = self.state.get_company(handoff["company_id"])
        if state_record and state_record["state"] == "AWAITING_AI_OUTPUT":
            self.state.transition(handoff["company_id"], company, "DOCUMENTS_READY")
        update_pipeline_status(
            self.config["paths"]["workbook"], handoff["company_id"], "DOCUMENTS_READY"
        )
        return True
    
    def monitor_downloads(self) -> dict[str, Any]:
        handoff = self.state.active_handoff()
        if not handoff:
            return {"active": False, "completed": False}
        if handoff["company_id"] not in self._opened_sessions:
            self._ensure_server()
            url = f"http://127.0.0.1:{self.server.server_port}/{handoff['session_token']}"
            self._opened_sessions.add(handoff["company_id"])
            self.browser_open("https://chatgpt.com/")
            self.browser_open(url)
            print(f"Resumed JAPP handoff for {handoff['company_name']}: {url}")
        candidates = self._new_downloads(handoff)
        completed = self._move_pair(handoff, candidates) if candidates else False
        return {"active": True, "company_id": handoff["company_id"], "completed": completed}
    
    def close(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=2)
        self.server_thread = None