from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .models import Finding

LEDGER_SCHEMA_VERSION = 1
MAX_LEDGER_BYTES = 4 * 1024 * 1024
DEFAULT_LEDGER_NAME = "repo-gardener-accepted.json"
_FINDING_ID = re.compile(r"^[a-z][a-z0-9-]*:[0-9a-f]{12}$")


class LedgerError(ValueError):
    """Raised when an accepted-findings ledger cannot be trusted."""


@dataclass(frozen=True)
class AcceptedEntry:
    id: str
    rule: str
    path: str
    note: str = ""

    def to_dict(self) -> dict[str, str]:
        data = {"id": self.id, "rule": self.rule, "path": self.path}
        if self.note:
            data["note"] = self.note
        return data


@dataclass(frozen=True)
class AcceptedLedger:
    entries: tuple[AcceptedEntry, ...] = ()
    source: str = ""

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(entry.id for entry in self.entries)

    def to_json(self) -> str:
        document = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "tool": "repo-gardener",
            "entries": [entry.to_dict() for entry in self.entries],
        }
        return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def load_ledger(path: Path) -> AcceptedLedger:
    try:
        if path.stat().st_size > MAX_LEDGER_BYTES:
            raise LedgerError(f"accepted-findings ledger is too large: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LedgerError(f"accepted-findings ledger does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerError(
            f"unable to read accepted-findings ledger {path}: {exc}"
        ) from exc
    return _parse_ledger(data, path)


def build_ledger(
    findings: list[Finding],
    previous: AcceptedLedger | None = None,
    source: str = "",
) -> AcceptedLedger:
    notes = {entry.id: entry.note for entry in (previous.entries if previous else ())}
    entries = {
        finding.id: AcceptedEntry(
            id=finding.id,
            rule=finding.rule,
            path=finding.path.replace("\\", "/"),
            note=notes.get(finding.id, ""),
        )
        for finding in findings
        if finding.id
    }
    return AcceptedLedger(
        entries=tuple(entries[key] for key in sorted(entries)),
        source=source,
    )


def partition_findings(
    findings: list[Finding],
    ledger: AcceptedLedger | None,
) -> tuple[list[Finding], list[Finding]]:
    if ledger is None:
        return list(findings), []
    accepted = ledger.ids
    remaining = [finding for finding in findings if finding.id not in accepted]
    suppressed = [finding for finding in findings if finding.id in accepted]
    return remaining, suppressed


def unmatched_entries(ledger: AcceptedLedger, findings: list[Finding]) -> list[str]:
    """Return accepted ids that no current finding reproduces."""
    current = {finding.id for finding in findings}
    return sorted(entry.id for entry in ledger.entries if entry.id not in current)


def ledger_digest(ledger: AcceptedLedger | None) -> str:
    payload = json.dumps(
        sorted(ledger.ids) if ledger else [],
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def ledger_metrics(
    ledger: AcceptedLedger, suppressed: int, unmatched: list[str]
) -> dict[str, Any]:
    return {
        "source": ledger.source,
        "entries": len(ledger.entries),
        "suppressed": suppressed,
        "unmatched": unmatched,
    }


def _parse_ledger(data: Any, path: Path) -> AcceptedLedger:
    if not isinstance(data, dict):
        raise LedgerError("accepted-findings ledger must be a JSON object")
    schema = data.get("schema_version")
    if type(schema) is not int or schema != LEDGER_SCHEMA_VERSION:
        raise LedgerError(
            f"unsupported accepted-findings ledger schema: {schema!r}; "
            f"expected {LEDGER_SCHEMA_VERSION}"
        )
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        raise LedgerError("accepted-findings ledger entries must be a list")
    entries: dict[str, AcceptedEntry] = {}
    for raw in raw_entries:
        entry = _parse_entry(raw)
        if entry.id in entries:
            raise LedgerError(
                f"accepted-findings ledger repeats finding id: {entry.id}"
            )
        entries[entry.id] = entry
    return AcceptedLedger(
        entries=tuple(entries[key] for key in sorted(entries)),
        source=path.as_posix(),
    )


def _parse_entry(raw: Any) -> AcceptedEntry:
    if not isinstance(raw, dict):
        raise LedgerError("accepted-findings ledger entry must be a JSON object")
    identifier = raw.get("id")
    if type(identifier) is not str or not _FINDING_ID.match(identifier):
        raise LedgerError(
            f"accepted-findings ledger entry has an invalid finding id: {identifier!r}"
        )
    return AcceptedEntry(
        id=identifier,
        rule=_optional_string(raw, "rule", identifier.split(":", 1)[0]),
        path=_optional_string(raw, "path", ""),
        note=_optional_string(raw, "note", ""),
    )


def _optional_string(raw: dict[str, Any], key: str, default: str) -> str:
    if key not in raw:
        return default
    value = raw[key]
    if type(value) is not str:
        raise LedgerError(f"accepted-findings ledger entry {key} must be a string")
    return value
