"""Preservation events and independent fixity checking (profile 0.3.0: PRES-03, PRES-05, PRES-06).

Preservation events are recorded *separately* from the immutable issued artefacts, in an append-only,
hash-chained log. Each event has the minimum structure the profile requires:

    eventId, type, time, agent, artefact, digest, outcome, detail, evidence, prevHash, hash

``hash`` is the SHA-256 of the canonical JSON of the event without ``hash``; ``prevHash`` links each event
to its predecessor, so any edit, deletion or reordering of earlier events breaks the chain. The current
chain head is also written to ``anchor.json``. In the demonstrator the anchor sits next to the log; the
Protected Preservation option requires it to be held by an independent party (or replaced by an electronic
seal or trusted timestamp), which is a deployment decision - see the profile, PRES-05.

The fixity check (``scdpoc fixity``) reads only the object store, the issuance records and this log. It
needs no running server and does not trust registry metadata (whose SHA-1 hash must not be relied on to
detect deliberate alteration - PRES-02).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVENT_TYPES = {
    "issuance", "validation", "attestation", "replacement", "correction", "withdrawal",
    "notification-required", "fixity-check",
}
GENESIS = "0" * 64


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def record_digest(record: dict) -> str:
    """Digest of an issuance record, computed over its canonical JSON (PRES-05)."""
    return sha256(_canon(record))


@dataclass
class ChainCheck:
    intact: bool
    events: int
    head: str
    anchor: str | None
    anchorMatches: bool
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class EventLog:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "events.jsonl"
        self.anchor_path = root / "anchor.json"
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- write
    def append(self, type_: str, *, agent: str, artefact: dict, outcome: str = "success",
               digest: dict | None = None, detail: dict | None = None, evidence: list[str] | None = None) -> dict:
        if type_ not in EVENT_TYPES:
            raise ValueError(f"unknown preservation event type {type_}")
        if outcome not in ("success", "failure"):
            raise ValueError("outcome must be success or failure")
        with self._lock:
            events = self.events()
            prev = events[-1]["hash"] if events else GENESIS
            ev = {"eventId": f"urn:uuid:{uuid.uuid4()}", "type": type_,
                  "time": datetime.now(UTC).isoformat(timespec="seconds"), "agent": agent,
                  "artefact": artefact, "digest": digest, "outcome": outcome, "detail": detail or {},
                  "evidence": evidence or [], "prevHash": prev}
            ev["hash"] = sha256(_canon(ev))
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            tmp = self.anchor_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"head": ev["hash"], "events": len(events) + 1, "time": ev["time"]}) + "\n")
            tmp.replace(self.anchor_path)
            return ev

    # ----------------------------------------------------------------- read
    def events(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def for_document(self, document_id: str) -> list[dict]:
        return [e for e in self.events() if (e.get("artefact") or {}).get("documentId") == document_id]

    def anchor(self) -> dict | None:
        try:
            return json.loads(self.anchor_path.read_text())
        except (OSError, ValueError):
            return None

    def verify(self, anchor: str | None = None) -> ChainCheck:
        """Recompute every hash and link. ``anchor`` is an independently held chain head, if any."""
        problems: list[str] = []
        prev = GENESIS
        events = self.events()
        for i, ev in enumerate(events):
            body = {k: v for k, v in ev.items() if k != "hash"}
            if ev.get("prevHash") != prev:
                problems.append(f"event {i} ({ev.get('eventId')}): prevHash does not link to the previous event")
            if sha256(_canon(body)) != ev.get("hash"):
                problems.append(f"event {i} ({ev.get('eventId')}): content does not match its hash")
            prev = ev.get("hash", "")
        held = anchor or (self.anchor() or {}).get("head")
        matches = held is None or held == (events[-1]["hash"] if events else GENESIS)
        if not matches:
            problems.append("chain head does not match the anchor (events removed or replaced)")
        return ChainCheck(not problems, len(events), events[-1]["hash"] if events else GENESIS, held, matches,
                          problems)


# ------------------------------------------------------------------ fixity

@dataclass
class FixityResult:
    documentId: str
    artefact: str           # "envelope" | "ips"
    uniqueId: str
    expected: str
    actual: str | None
    passed: bool
    note: str = ""


def fixity_check(data_dir: Path, *, agent: str = "scdpoc fixity (independent check)",
                 log: EventLog | None = None) -> dict:
    """Compare every stored artefact of every issuance with the SHA-256 digests recorded at issuance, verify
    each issuance record against the digest recorded in the event log, record one fixity-check event per
    artefact, and verify the chain. Reads files only; needs no running service."""
    from .xds.store import Database
    log = log or EventLog(data_dir / "preservation")
    db = Database(data_dir / "scdpoc.sqlite3")
    objects = data_dir / "repository"
    issuance_events = {e["artefact"]["documentId"]: e for e in log.events() if e["type"] == "issuance"}
    results: list[FixityResult] = []
    record_problems: list[str] = []
    for rec_path in sorted((data_dir / "work" / "patients").glob("*/issuances.json")):
        for iss in json.loads(rec_path.read_text(encoding="utf-8")):
            doc = iss["documentUrn"]
            ev = issuance_events.get(doc)
            if ev is None:
                record_problems.append(f"{doc}: no issuance event in the preservation log")
            elif (ev.get("detail") or {}).get("issuanceRecordSha256") != record_digest(iss):
                record_problems.append(f"{doc}: issuance record differs from the digest recorded at issuance")
            for role, expected in (("envelope", iss["envelope"]["sha256"]), ("ips", iss["ips"]["sha256"])):
                uid = iss[role]["uniqueId"]
                row = db.one("SELECT path FROM repository_object WHERE unique_id=?", (uid,))
                actual = None
                note = ""
                if row is None:
                    note = "object not registered in the store"
                else:
                    p = objects / row[0]
                    if p.is_file():
                        actual = sha256(p.read_bytes())
                    else:
                        note = "object file missing"
                results.append(FixityResult(doc, role, uid, expected, actual, actual == expected, note))
    for r in results:
        log.append("fixity-check", agent=agent, outcome="success" if r.passed else "failure",
                   artefact={"documentId": r.documentId, "role": r.artefact, "uniqueId": r.uniqueId},
                   digest={"algorithm": "SHA-256", "expected": r.expected, "actual": r.actual},
                   detail={"note": r.note} if r.note else {})
    chain = log.verify()
    return {"checked": len(results), "failed": [r.__dict__ for r in results if not r.passed],
            "results": [r.__dict__ for r in results], "issuanceRecordProblems": record_problems,
            "chain": chain.to_dict(),
            "passed": all(r.passed for r in results) and not record_problems and chain.intact}


def main(args) -> int:
    from .config import Settings
    data_dir = Path(args.data_dir) if args.data_dir else Settings().data_dir
    out = fixity_check(data_dir)
    text = json.dumps(out, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text if not args.out else f"checked {out['checked']}, failed {len(out['failed'])}, "
          f"chain intact {out['chain']['intact']} -> {args.out}")
    return 0 if out["passed"] else 1
