"""
ExecutionLedger — durable append-only audit log.

Every meaningful action (run, seal, verify, archon decision) appends one
JSON line to ``ledger/execution.jsonl``. The ledger is intentionally simple
so that it can be tailed by humans, parsed by agents, and diffed by CI.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # POSIX advisory file locking — guards cross-process interleaving.
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover — Windows/no-fcntl platforms
    fcntl = None  # type: ignore


# Process-wide lock so concurrent in-process writers (gate threads, MCP request
# handlers) cannot interleave a single append. Paired with an OS file lock
# (``fcntl.flock``) so separate processes are serialized too.
_APPEND_LOCK = threading.Lock()


class ExecutionLedger:
    """Append-only JSONL ledger of platform actions.

    Writes are serialized by a thread lock + an advisory OS file lock so a
    concurrent gate run and MCP request cannot produce a torn/interleaved line.
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, *, kind: str, actor: str | None = None, **fields: Any) -> dict:
        """Append a single event atomically. Returns the persisted record."""
        record = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "actor": actor or os.environ.get("USER") or "unknown",
        }
        record.update(fields)
        line = json.dumps(record, sort_keys=True) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _APPEND_LOCK:
            with self.path.open("a", encoding="utf-8") as fh:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    fh.write(line)
                    fh.flush()
                    os.fsync(fh.fileno())
                finally:
                    if fcntl is not None:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return record

    def read(self, *, kind: str | None = None, limit: int | None = None) -> list[dict]:
        """Read events, newest last. Optionally filter by ``kind``.

        Corrupt lines are *not* silently skipped — they are counted and the
        total is reported via :meth:`corrupt_line_count` and a one-line warning
        on stderr, so partial-write damage is visible rather than swallowed.
        """
        events, _corrupt = self._read_with_corruption(kind=kind, limit=limit)
        return events

    def verdicts(self, *, group_by: str = "actor") -> dict[str, dict[str, Any]]:
        """Fold the ledger into per-group accept/reject tallies.

        This is the data backbone of the swarm verdict board: one group (default
        the ``actor`` — i.e. the agent identity) per row, with how many of its
        sealed/verified claims were accepted vs rejected.

        An event counts as an *accept* when it carries a truthy outcome
        (``accepted`` / ``ok`` / ``passed`` is True) and a *reject* when that
        outcome is explicitly False. Events with no outcome signal (e.g. a bare
        ``run.start``) are ignored. ``read()`` returns events oldest->newest, so
        ``last_kind`` / ``last_ts`` reflect each group's most recent event.
        """
        groups: dict[str, dict[str, Any]] = {}
        for ev in self.read():
            outcome: bool | None = None
            for field in ("accepted", "ok", "passed"):
                if field in ev:
                    outcome = bool(ev[field])
                    break
            if outcome is None:
                continue
            key = str(ev.get(group_by, "unknown"))
            g = groups.setdefault(
                key, {"accepted": 0, "rejected": 0, "last_kind": None, "last_ts": None}
            )
            g["accepted" if outcome else "rejected"] += 1
            g["last_kind"] = ev.get("kind")
            g["last_ts"] = ev.get("ts")
        return groups

    def corrupt_line_count(self) -> int:
        """Return the number of un-parseable (corrupt) lines in the ledger."""
        _events, corrupt = self._read_with_corruption()
        return corrupt

    def _read_with_corruption(
        self, *, kind: str | None = None, limit: int | None = None
    ) -> tuple[list[dict], int]:
        if not self.path.exists():
            return [], 0
        events: list[dict] = []
        corrupt = 0
        with self.path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    corrupt += 1
                    continue
                if kind is not None and rec.get("kind") != kind:
                    continue
                events.append(rec)
        if corrupt:
            import sys

            print(
                f"[rigforge-ledger] WARNING: {corrupt} corrupt line(s) in "
                f"{self.path} (skipped, not counted as events)",
                file=sys.stderr,
            )
        if limit is not None:
            events = events[-limit:]
        return events, corrupt
