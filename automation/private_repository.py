"""A guarded Git checkout for private application data, separate from source code."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


def command(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        # Do not echo command arguments: future callers may include credentials.
        raise RuntimeError(f"{args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def inside(root: Path, relative: str) -> Path:
    """Reject traversal and symlinks, including links pointing within the checkout."""
    parts = PurePosixPath(relative)
    if not relative or parts.is_absolute() or any(p in {"..", ".git"} for p in parts.parts):
        raise ValueError(f"Unsafe exchange path: {relative}")
    path = root
    for part in parts.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"Symlinks are not accepted in exchange paths: {relative}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Exchange path escapes its root: {relative}")
    return path


class PrivateRepository:
    def __init__(self, config: dict[str, Any]):
        self.path = Path(config["paths"]["private_repo_dir"])
        self.repository = config["exchange"]["repository"]
        self.branch = config["exchange"]["branch"]

    def verify_private(self) -> None:
        metadata = json.loads(command(
            "gh", "repo", "view", self.repository, "--json", "nameWithOwner,isPrivate"
        ))
        if metadata.get("nameWithOwner", "").casefold() != self.repository.casefold() or not metadata.get("isPrivate"):
            raise ValueError("The data repository must exist and be private; no data was published")

    def _verify_origin(self) -> None:
        remote = command("git", "remote", "get-url", "origin", cwd=self.path)
        expected = self.repository.casefold()
        accepted = {f"https://github.com/{expected}", f"https://github.com/{expected}.git",
                    f"git@github.com:{expected}.git"}
        if remote.casefold() not in accepted:
            raise ValueError("Private checkout origin differs from exchange.repository")
        if command("git", "branch", "--show-current", cwd=self.path) != self.branch:
            raise ValueError(f"Switch the private checkout to {self.branch} before continuing")

    def sync(self) -> None:
        self.verify_private()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            command("gh", "repo", "clone", self.repository, str(self.path))
            head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=self.path,
                                  capture_output=True)
            if head.returncode:
                command("git", "symbolic-ref", "HEAD", f"refs/heads/{self.branch}", cwd=self.path)
        if self.path.is_symlink() or not (self.path / ".git").is_dir():
            raise ValueError("Private checkout must be a real Git checkout, not a symlink")
        self._verify_origin()
        if command("git", "status", "--porcelain", cwd=self.path):
            raise ValueError("Private checkout has local edits; commit or resolve them before syncing")
        command("git", "fetch", "origin", cwd=self.path)
        refs = command("git", "branch", "-r", "--list", f"origin/{self.branch}", cwd=self.path)
        if refs:
            command("git", "merge", "--ff-only", f"origin/{self.branch}", cwd=self.path)

    def publish(self, files: list[Path], message: str) -> bool:
        self.verify_private()
        self._verify_origin()
        relative = []
        for path in files:
            name = path.relative_to(self.path).as_posix()
            inside(self.path, name)
            relative.append(name)
        # A failed previous push may have left a local commit. Always push, even
        # when the current files have no changes, so that reruns recover it.
        if relative:
            command("git", "add", "--", *relative, cwd=self.path)
        staged = command("git", "diff", "--cached", "--name-only", cwd=self.path).splitlines()
        if set(staged) - set(relative):
            raise ValueError("Unexpected staged private files; refusing to publish unrelated data")
        changed = bool(staged)
        if changed:
            command("git", "commit", "-m", message, cwd=self.path)
        command("git", "push", "origin", f"HEAD:refs/heads/{self.branch}", cwd=self.path)
        return changed
