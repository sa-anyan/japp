"""Optional safe pull of company JSON records from a configured GitHub remote."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable


class DirtyResearchTree(RuntimeError):
    pass


def _run(runner: Callable, command: list[str], *, cwd: Path, binary: bool = False):
    result = runner(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=not binary,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace") if isinstance(result.stderr, bytes) else result.stderr
        raise RuntimeError(f"Git command failed ({' '.join(command)}): {detail.strip()}")
    return result


def pull_research_records(repository_root: Path | str, *, remote: str = "origin",
                          branch: str = "main", runner: Callable = subprocess.run) -> dict:
    root = Path(repository_root).resolve()
    dirty = _run(
        runner, ["git", "status", "--porcelain", "--", "research/companies"], cwd=root
    ).stdout
    if dirty.strip():
        raise DirtyResearchTree(
            "Local research files have uncommitted changes; commit or stash them before pulling."
        )

    _run(runner, ["git", "fetch", "--no-tags", remote, branch], cwd=root)
    ref = f"{remote}/{branch}"
    commit = _run(runner, ["git", "rev-parse", ref], cwd=root).stdout.strip()
    paths = _run(
        runner, ["git", "ls-tree", "-r", "--name-only", ref, "--", "research/companies"], cwd=root
    ).stdout.splitlines()
    updated = []

    for relative in paths:
        if not relative.startswith("research/companies/") or not relative.endswith(".json"):
            continue
        target = (root / relative).resolve()
        if root not in target.parents:
            raise ValueError(f"Invalid research path from Git: {relative}")
        content = _run(runner, ["git", "show", f"{ref}:{relative}"], cwd=root, binary=True).stdout
        if target.is_file() and target.read_bytes() == content:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}-", delete=False) as temp:
                temporary = Path(temp.name)
                temp.write(content)
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        updated.append(relative)

    return {"remote": remote, "branch": branch, "commit": commit, "updated": updated}