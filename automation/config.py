"""Load and validate local, non-secret engine configuration."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPOSITORY_ROOT / "automation" / "config.json"
EXAMPLE_CONFIG_PATH = REPOSITORY_ROOT / "automation" / "config.example.json"

DEFAULTS: dict[str, Any] = {
    "research": {"pull_before_sync": False, "remote": "origin", "branch": "main"},
    "paths": {
        "workbook": "data/reference/JAPP_Automation_Dashboard.xlsx",
        "research_dir": "research/companies",
        "registry": "data/reference/JAPP_company_registry.csv",
        "manifest": "data/state/research-sync.json",
        "database": "data/state/japp.sqlite",
        "backup_dir": "backups",
        "generated_dir": "data/generated",
        "contacts_dir": "data/contacts",
        "apollo_import_dir": "automation/imports/apollo",
        "logs_dir": "logs",
        "prompts_dir": "prompts",
        "master_cv": "",
        "profile_dir": ".japp-browser-profile",
        "stop_file": "STOP",
        "lock_file": ".japp.lock",
    },
    "polling": {
        "research_sync_seconds": 3600,
        "approval_seconds": 30,
        "handoff_seconds": 30,
        "contact_import_seconds": 60,
        "batch_seconds": 30,
    },
    "execution": {
        "enabled": True,
        "max_companies_per_day": 15,
        "min_step_pause_seconds": 20,
        "max_step_pause_seconds": 60,
        "min_company_pause_seconds": 180,
        "max_company_pause_seconds": 480,
        "target_titles": [
            "Hiring Manager",
            "Department Head",
            "Commercial Director",
            "Talent Acquisition",
        ],
    },
    "ai": {"provider": "manual"},
    "apollo": {"provider": "csv", "reveal_emails": False, "max_reveals_per_run": 0},
    "mail": {
        "provider": "mock",
        "auto_draft": False,
        "send_enabled": False,
        "auto_send_approved_batches": False,
        "daily_send_limit": 20,
        "min_send_pause_seconds": 20,
        "max_send_pause_seconds": 60,
        "subject_template": "Introduction: {company}",
        "outlook": {
            "tenant": "common",
            "client_id_env": "JAPP_MICROSOFT_CLIENT_ID",
            "scopes": ["Mail.ReadWrite", "Mail.Send", "User.Read", "offline_access"],
        },
        "gmail": {
            "client_id_env": "JAPP_GOOGLE_CLIENT_ID",
            "client_secret_env": "JAPP_GOOGLE_CLIENT_SECRET",
            "scopes": ["https://www.googleapis.com/auth/gmail.compose", "https://www.googleapis.com/auth/gmail.send"],
        },
    },
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _resolve_paths(config: dict[str, Any], root: Path) -> None:
    paths = config["paths"]
    for key, value in tuple(paths.items()):
        if key == "master_cv" and not value:
            paths[key] = None
            continue
        path = Path(value).expanduser()
        paths[key] = path if path.is_absolute() else root / path


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    config_path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    if not config_path.is_absolute():
        config_path = REPOSITORY_ROOT / config_path
    override: dict[str, Any] = {}
    if config_path.is_file():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot load configuration {config_path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError("Configuration must be a JSON object")
        override = loaded

    config = _merge(DEFAULTS, override)
    if config["ai"]["provider"] not in {"manual", "mock", "api", "local"}:
        raise ValueError("ai.provider must be manual, mock, api, or local")
    if config["apollo"]["provider"] not in {"csv", "disabled", "api"}:
        raise ValueError("apollo.provider must be csv, disabled, or api")
    if config["mail"]["provider"] not in {"mock", "outlook", "gmail"}:
        raise ValueError("mail.provider must be mock, outlook, or gmail")
    if config["mail"]["daily_send_limit"] < 1:
        raise ValueError("mail.daily_send_limit must be positive")
    for section, minimum_key, maximum_key in (
        ("execution", "min_step_pause_seconds", "max_step_pause_seconds"),
        ("execution", "min_company_pause_seconds", "max_company_pause_seconds"),
        ("mail", "min_send_pause_seconds", "max_send_pause_seconds"),
    ):
        if config[section][minimum_key] < 0 or config[section][maximum_key] < config[section][minimum_key]:
            raise ValueError(f"Invalid pause range in {section}")

    root = config_path.parent.parent if config_path.name == "config.json" else REPOSITORY_ROOT
    _resolve_paths(config, root)
    config["config_path"] = config_path
    config["repository_root"] = root
    return config