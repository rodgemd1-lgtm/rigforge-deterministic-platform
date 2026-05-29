"""
RIGForge Git Agent — read-only git-state module for agent use.

Exposes safe, deterministic git introspection:
  git_status()       — current branch, dirty flag, counts, remote URL (host only)
  git_log(n)         — last N commit summaries (hash, author, subject, timestamp)
  git_changed_files()— files changed in the working tree vs HEAD

All operations are **read-only** and produce no side effects on the repository.
Secrets (tokens embedded in remote URLs) are scrubbed before returning.

Used by:
  - rigforge smoke          — phase-0 local smoke check
  - MCP tool gev.git_status — git resource for agent clients
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from typing import Any


# ── helpers ────────────────────────────────────────────────────────────


def _git_available() -> bool:
    return shutil.which("git") is not None


def _run(args: list[str], cwd: str | None = None) -> tuple[int, str]:
    """Run a git sub-command. Returns (returncode, stdout)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
        )
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def _scrub_url(url: str) -> str:
    """Remove credentials from a remote URL so it is safe to log or expose."""
    return re.sub(r"(?<=://)([^@]+)@", "", url)


# ── public data classes ────────────────────────────────────────────────


@dataclass
class GitStatus:
    """Snapshot of the current git repository state."""

    available: bool
    in_repo: bool
    branch: str | None
    commit_hash: str | None
    dirty: bool
    untracked: int
    staged: int
    unstaged: int
    remote_url: str | None  # credentials scrubbed
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GitCommit:
    """One commit summary entry."""

    hash: str
    author: str
    timestamp: str
    subject: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── public API ─────────────────────────────────────────────────────────


def git_status(cwd: str | None = None) -> GitStatus:
    """Return a safe snapshot of the current git state.

    Never raises; returns ``available=False`` or ``in_repo=False`` when git
    is unavailable or the directory is not a repository.
    """
    if not _git_available():
        return GitStatus(
            available=False, in_repo=False, branch=None, commit_hash=None,
            dirty=False, untracked=0, staged=0, unstaged=0, remote_url=None,
            error="git not found in PATH",
        )

    rc, _ = _run(["rev-parse", "--is-inside-work-tree"], cwd=cwd)
    if rc != 0:
        return GitStatus(
            available=True, in_repo=False, branch=None, commit_hash=None,
            dirty=False, untracked=0, staged=0, unstaged=0, remote_url=None,
            error="not inside a git repository",
        )

    # branch
    _, branch = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    branch = branch or None

    # commit hash (short)
    _, commit_hash = _run(["rev-parse", "--short", "HEAD"], cwd=cwd)
    commit_hash = commit_hash or None

    # porcelain status
    _, porcelain = _run(["status", "--porcelain=v1"], cwd=cwd)
    staged = unstaged = untracked = 0
    for line in porcelain.splitlines():
        if len(line) < 2:
            continue
        xy = line[:2]
        if xy[0] == "?":
            untracked += 1
        else:
            if xy[0] != " ":
                staged += 1
            if xy[1] != " ":
                unstaged += 1
    dirty = bool(staged or unstaged or untracked)

    # remote URL (credentials scrubbed)
    _, raw_url = _run(["remote", "get-url", "origin"], cwd=cwd)
    remote_url = _scrub_url(raw_url) if raw_url else None

    return GitStatus(
        available=True,
        in_repo=True,
        branch=branch,
        commit_hash=commit_hash,
        dirty=dirty,
        untracked=untracked,
        staged=staged,
        unstaged=unstaged,
        remote_url=remote_url,
    )


def git_log(n: int = 5, cwd: str | None = None) -> list[GitCommit]:
    """Return the last *n* commit summaries.

    Returns an empty list when git is unavailable or not in a repository.
    """
    if not _git_available():
        return []

    rc, output = _run(
        ["log", f"-{n}", "--pretty=format:%h\t%ae\t%aI\t%s"],
        cwd=cwd,
    )
    if rc != 0 or not output:
        return []

    commits = []
    for line in output.splitlines():
        parts = line.split("\t", 3)
        if len(parts) == 4:
            commits.append(GitCommit(
                hash=parts[0],
                author=parts[1],
                timestamp=parts[2],
                subject=parts[3],
            ))
    return commits


def git_changed_files(cwd: str | None = None) -> list[str]:
    """Return the list of files that differ from HEAD (staged + unstaged).

    Untracked files are not included. Returns an empty list when there is
    nothing changed or git is unavailable.
    """
    if not _git_available():
        return []

    rc, output = _run(["diff", "--name-only", "HEAD"], cwd=cwd)
    if rc != 0:
        return []
    return [f for f in output.splitlines() if f]
