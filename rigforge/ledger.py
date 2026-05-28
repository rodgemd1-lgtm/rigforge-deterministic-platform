"""
ExecutionLedger — durable append-only audit log.

Every meaningful action (run, seal, verify, archon decision) appends one
JSON line to ``ledger/execution.jsonl``. The ledger is intentionally simple
so that it can be tailed by humans, parsed by agents, and diffed by CI.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ExecutionLedger:
    """Append-only JSONL ledger of platform actions."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, *, kind: str, actor: str | None = None, **fields: Any) -> dict:
        """Append a single event. Returns the persisted record."""
        record = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "actor": actor or os.environ.get("USER") or "unknown",
        }
        record.update(fields)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def read(self, *, kind: str | None = None, limit: int | None = None) -> list[dict]:
        """Read events, newest last. Optionally filter by ``kind``."""
        if not self.path.exists():
            return []
        events: list[dict] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if kind is not None and rec.get("kind") != kind:
                    continue
                events.append(rec)
        if limit is not None:
            events = events[-limit:]
        return events
