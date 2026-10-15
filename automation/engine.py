"""Long-running service lifecycle with isolated APScheduler jobs."""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler

from .orchestrator import import_pending_contacts, monitor_handoffs, run_approved_queue, sync_research
from .outreach import monitor_outreach_batches
from .providers.factory import create_mail_provider
from .state import StateStore


class InstanceAlreadyRunning(RuntimeError):
    pass


class SingleInstanceLock:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.pid = os.getpid()
        self.acquired = False

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                try:
                    existing_pid = int(self.path.read_text(encoding="ascii").strip())
                except (OSError, ValueError):
                    existing_pid = -1
                if existing_pid > 0 and self._alive(existing_pid):
                    raise InstanceAlreadyRunning(f"JAPP is already running with PID {existing_pid}")
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(descriptor, "w", encoding="ascii") as lock:
                lock.write(f"{self.pid}\n")
            self.acquired = True
            return
        raise InstanceAlreadyRunning(f"Could not acquire JAPP lock: {self.path}")

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            current_pid = int(self.path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            current_pid = None
        if current_pid == self.pid:
            self.path.unlink(missing_ok=True)
        self.acquired = False


def request_stop(stop_path: Path | str, lock_path: Path | str | None = None) -> bool:
    path = Path(stop_path)
    lock = Path(lock_path) if lock_path else path.parent / ".japp.lock"
    if not lock.is_file():
        return False
    path.write_text("stop requested\n", encoding="ascii")
    return True


def start_engine(config: dict[str, Any], *, scheduler_factory=BackgroundScheduler,
                 wait: Callable[[float], None] | None = None) -> None:
    paths = config["paths"]
    for key in ("database", "generated_dir", "contacts_dir", "logs_dir", "backup_dir"):
        Path(paths[key]).parent.mkdir(parents=True, exist_ok=True) if key == "database" else Path(paths[key]).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=Path(paths["logs_dir"]) / "japp.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("japp.engine")
    state = StateStore(paths["database"])
    state.initialize()
    lock = SingleInstanceLock(paths["lock_file"])
    lock.acquire()
    stop_file = Path(paths["stop_file"])
    stop_file.unlink(missing_ok=True)
    stopped = threading.Event()
    scheduler = scheduler_factory(timezone="UTC")
    execution_lock = threading.Lock()
    mail_provider = None

    def get_mail_provider():
        nonlocal mail_provider
        if mail_provider is None:
            from .providers.factory import create_mail_provider
            mail_provider = create_mail_provider(config)
        return mail_provider

    def serialized(action: Callable[[], Any]) -> None:
        if not execution_lock.acquire(blocking=False):
            return
        try:
            action()
        finally:
            execution_lock.release()

    def guarded(name: str, action: Callable[[], Any]) -> None:
        state.service_status(name, "running")
        try:
            action()
        except Exception as exc:
            logger.exception("Service %s failed", name)
            state.service_status(name, "error", f"{type(exc).__name__}: {exc}")
        else:
            state.service_status(name, "idle")

    intervals = config["polling"]
    jobs = (
        ("research_sync", lambda: sync_research(config), intervals["research_sync_seconds"]),
        ("approval_queue", lambda: serialized(lambda: run_approved_queue(config, state, limit=1)), intervals["approval_seconds"]),
        ("contact_import", lambda: import_pending_contacts(config, state), intervals["contact_import_seconds"]),
        ("handoff_monitor", lambda: serialized(lambda: monitor_handoffs(config, state)), intervals["handoff_seconds"]),
        ("batch_monitor", lambda: monitor_outreach_batches(
            config, state, lambda _config: get_mail_provider()
        ), intervals["batch_seconds"]),
    )

    old_handlers = {}

    def handle_signal(signum, _frame):
        logger.info("Received shutdown signal %s", signum)
        stopped.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            old_handlers[signum] = signal.signal(signum, handle_signal)
        except ValueError:
            pass

    try:
        guarded("research_sync", lambda: sync_research(config))
        guarded("contact_import", lambda: import_pending_contacts(config, state))
        for name, action, seconds in jobs:
            scheduler.add_job(
                guarded,
                "interval",
                args=[name, action],
                seconds=max(1, int(seconds)),
                id=name,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        scheduler.start()
        logger.info("JAPP master engine started")
        waiter = wait or time.sleep
        while not stopped.is_set():
            if stop_file.exists():
                stopped.set()
                break
            waiter(1)
    finally:
        if getattr(scheduler, "running", False):
            scheduler.shutdown(wait=True)
        stop_file.unlink(missing_ok=True)
        lock.release()
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
        logger.info("JAPP master engine stopped")