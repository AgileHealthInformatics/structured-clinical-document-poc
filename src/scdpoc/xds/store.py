"""Persistence for the XDS actors.

* Registry metadata: SQLite (stdlib, zero-dependency, inspectable). The
  ``RegistryStore`` interface is small so an adopter can swap in PostgreSQL
  (see docs/extending.md and ADR-006).
* Repository objects: write-once files on a local volume. Objects are never
  overwritten; replacement is a new object plus an RPLC association.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import asdict
from datetime import UTC
from pathlib import Path

from .model import Association, Code, DocumentEntry, SubmissionSet

SCHEMA = """
CREATE TABLE IF NOT EXISTS document_entry (
  entry_uuid TEXT PRIMARY KEY,
  unique_id TEXT UNIQUE NOT NULL,
  patient_id TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  format_code TEXT NOT NULL,
  status TEXT NOT NULL,
  creation_time TEXT NOT NULL,
  repository_unique_id TEXT NOT NULL,
  submission_set TEXT NOT NULL,
  json TEXT NOT NULL,
  registered_seq INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS de_patient ON document_entry(patient_id, status);
CREATE TABLE IF NOT EXISTS submission_set (
  entry_uuid TEXT PRIMARY KEY, unique_id TEXT UNIQUE NOT NULL, patient_id TEXT NOT NULL, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS association (
  entry_uuid TEXT PRIMARY KEY, type TEXT NOT NULL, source TEXT NOT NULL, target TEXT NOT NULL, json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS assoc_source ON association(source);
CREATE INDEX IF NOT EXISTS assoc_target ON association(target);
CREATE TABLE IF NOT EXISTS repository_object (
  unique_id TEXT PRIMARY KEY, path TEXT NOT NULL, mime_type TEXT NOT NULL, size INTEGER NOT NULL,
  sha1 TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL,
  detail TEXT NOT NULL);
"""


def de_to_json(de: DocumentEntry) -> str:
    return json.dumps(asdict(de))


def de_from_json(s: str) -> DocumentEntry:
    d = json.loads(s)
    d["codes"] = {k: Code(**v) for k, v in d["codes"].items()}
    return DocumentEntry(**d)


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def tx(self):
        return _Tx(self)

    def all(self, sql: str, args=()) -> list[tuple]:
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args=()) -> tuple | None:
        rows = self.all(sql, args)
        return rows[0] if rows else None


class _Tx:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        self.db._lock.acquire()
        self.db.conn.execute("BEGIN IMMEDIATE")
        return self.db.conn

    def __exit__(self, exc_type, *_):
        try:
            self.db.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db._lock.release()


class RegistryStore:
    def __init__(self, db: Database):
        self.db = db

    def add_submission(self, ss: SubmissionSet, docs: list[DocumentEntry], assocs: list[Association],
                       deprecate: list[str]) -> None:
        with self.db.tx() as c:
            seq = c.execute("SELECT COALESCE(MAX(registered_seq),0) FROM document_entry").fetchone()[0]
            c.execute("INSERT INTO submission_set VALUES (?,?,?,?)",
                      (ss.entry_uuid, ss.unique_id, ss.patient_id, json.dumps(asdict(ss))))
            for i, de in enumerate(docs, start=1):
                c.execute("INSERT INTO document_entry VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (de.entry_uuid, de.unique_id, de.patient_id, de.mime_type,
                           de.codes["formatCode"].code, de.status, de.creation_time, de.repository_unique_id,
                           ss.entry_uuid, de_to_json(de), seq + i))
            for a in assocs:
                c.execute("INSERT INTO association VALUES (?,?,?,?,?)",
                          (a.entry_uuid, a.type, a.source, a.target, json.dumps(asdict(a))))
            for uuid_ in deprecate:
                row = c.execute("SELECT json FROM document_entry WHERE entry_uuid=?", (uuid_,)).fetchone()
                de = de_from_json(row[0])
                de.status = "urn:oasis:names:tc:ebxml-regrep:StatusType:Deprecated"
                c.execute("UPDATE document_entry SET status=?, json=? WHERE entry_uuid=?",
                          (de.status, de_to_json(de), uuid_))

    def update_status(self, ss: SubmissionSet, assocs: list[Association], changes: dict[str, str]) -> None:
        """ITI-57 Update Availability Status: record the SubmissionSet and associations and change the status of
        the target DocumentEntries, in one transaction (all or nothing)."""
        with self.db.tx() as c:
            c.execute("INSERT INTO submission_set VALUES (?,?,?,?)",
                      (ss.entry_uuid, ss.unique_id, ss.patient_id, json.dumps(asdict(ss))))
            for a in assocs:
                c.execute("INSERT INTO association VALUES (?,?,?,?,?)",
                          (a.entry_uuid, a.type, a.source, a.target, json.dumps(asdict(a))))
            for uuid_, status in changes.items():
                row = c.execute("SELECT json FROM document_entry WHERE entry_uuid=?", (uuid_,)).fetchone()
                de = de_from_json(row[0])
                de.status = status
                c.execute("UPDATE document_entry SET status=?, json=? WHERE entry_uuid=?",
                          (de.status, de_to_json(de), uuid_))

    def get(self, entry_uuid: str) -> DocumentEntry | None:
        row = self.db.one("SELECT json FROM document_entry WHERE entry_uuid=?", (entry_uuid,))
        return de_from_json(row[0]) if row else None

    def submission_time(self, entry_uuid: str) -> str | None:
        """submissionTime of the SubmissionSet that registered this entry (MHD DocumentReference.date)."""
        row = self.db.one("SELECT s.json FROM document_entry d JOIN submission_set s ON s.entry_uuid = d.submission_set "
                          "WHERE d.entry_uuid=?", (entry_uuid,))
        return json.loads(row[0]).get("submission_time") if row else None

    def get_by_unique_id(self, unique_id: str) -> DocumentEntry | None:
        row = self.db.one("SELECT json FROM document_entry WHERE unique_id=?", (unique_id,))
        return de_from_json(row[0]) if row else None

    def unique_id_exists(self, unique_id: str) -> bool:
        return self.get_by_unique_id(unique_id) is not None

    def submission_set_unique_id_exists(self, unique_id: str) -> bool:
        return self.db.one("SELECT 1 FROM submission_set WHERE unique_id=?", (unique_id,)) is not None

    def find(self, patient_id: str, statuses: list[str], format_codes: list[str] | None = None) -> list[DocumentEntry]:
        sql = (f"SELECT json FROM document_entry WHERE patient_id=? AND status IN "
               f"({','.join('?' * len(statuses))})")
        args: list = [patient_id, *statuses]
        if format_codes:
            sql += f" AND format_code IN ({','.join('?' * len(format_codes))})"
            args += format_codes
        sql += " ORDER BY registered_seq DESC"
        return [de_from_json(r[0]) for r in self.db.all(sql, args)]

    def associations_for(self, entry_uuid: str) -> list[Association]:
        rows = self.db.all("SELECT json FROM association WHERE source=? OR target=?", (entry_uuid, entry_uuid))
        return [Association(**json.loads(r[0])) for r in rows]

    def all_entries(self) -> list[DocumentEntry]:
        return [de_from_json(r[0]) for r in self.db.all("SELECT json FROM document_entry ORDER BY registered_seq DESC")]


class ObjectStore:
    """Write-once file store. ``put`` refuses to overwrite an existing object."""

    def __init__(self, db: Database, root: Path):
        self.db = db
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, unique_id: str, data: bytes, mime_type: str) -> tuple[str, str]:
        sha1 = hashlib.sha1(data).hexdigest()
        sha256 = hashlib.sha256(data).hexdigest()
        name = hashlib.sha256(unique_id.encode()).hexdigest() + ".bin"
        path = self.root / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)   # O_EXCL: never overwrite
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        with self.db.tx() as c:
            c.execute("INSERT INTO repository_object VALUES (?,?,?,?,?,?)",
                      (unique_id, name, mime_type, len(data), sha1, sha256))
        return sha1, sha256

    def get(self, unique_id: str) -> tuple[bytes, str] | None:
        row = self.db.one("SELECT path, mime_type FROM repository_object WHERE unique_id=?", (unique_id,))
        if not row:
            return None
        return (self.root / row[0]).read_bytes(), row[1]

    def exists(self, unique_id: str) -> bool:
        return self.db.one("SELECT 1 FROM repository_object WHERE unique_id=?", (unique_id,)) is not None


class AuditLog:
    def __init__(self, db: Database):
        self.db = db

    def record(self, actor: str, event: str, **detail) -> None:
        from datetime import datetime
        with self.db.tx() as c:
            c.execute("INSERT INTO audit (at, actor, event, detail) VALUES (?,?,?,?)",
                      (datetime.now(UTC).isoformat(timespec="seconds"), actor, event,
                       json.dumps(detail, default=str)))

    def recent(self, limit: int = 100) -> list[dict]:
        rows = self.db.all("SELECT seq, at, actor, event, detail FROM audit ORDER BY seq DESC LIMIT ?", (limit,))
        return [{"seq": r[0], "at": r[1], "actor": r[2], "event": r[3], "detail": json.loads(r[4])} for r in rows]
