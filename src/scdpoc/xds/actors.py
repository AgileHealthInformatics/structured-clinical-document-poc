"""XDS.b Document Registry and Document Repository actors (demonstrator scope).

Implements the behaviour needed for the PoC's publish / query / retrieve /
replace story:

* Repository: ITI-41 Provide and Register Document Set-b (stores bytes
  write-once, computes hash/size, forwards metadata to the Registry - the
  ITI-42 hop is an in-process call in this release) and ITI-43 Retrieve.
* Registry: metadata validation against the Affinity Domain policy,
  RPLC and XFRM association handling, and ITI-18 stored queries
  (FindDocuments, GetDocuments, GetRelatedDocuments).

This is not a conformance-tested XDS implementation. It has not been tested
at an IHE Connectathon or with Gazelle; see docs/standards-baseline.md.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .model import (
    ASSOC_HAS_MEMBER,
    ASSOC_RPLC,
    ASSOC_XFRM,
    STATUS_APPROVED,
    STATUS_DEPRECATED,
    Code,
    DocumentEntry,
    Submission,
    XdsError,
)
from .store import AuditLog, ObjectStore, RegistryStore

TS = re.compile(r"^\d{4}(\d{2}(\d{2}(\d{2}(\d{2}(\d{2})?)?)?)?)?$")


class AffinityDomainPolicy:
    """Validates submissions against config/affinity-domain.yml."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        ad = cfg["affinity_domain"]
        self.assigning_authority = f"&{ad['patient_assigning_authority']['oid']}&ISO"
        self.prefix = ad["patient_assigning_authority"]["value_prefix"]
        self.source_id = ad["source_id"]
        self.fixed_codes = {k: Code.from_cfg(v) for k, v in cfg["document_entry"].items() if isinstance(v, dict)}
        self.language = cfg["document_entry"]["languageCode"]
        self.representations = {r["mimeType"]: Code.from_cfg(r["formatCode"])
                                for r in cfg["representations"].values()}

    def patient_cx(self, value: str) -> str:
        return f"{value}^^^{self.assigning_authority}"

    def check_patient(self, cx: str, ctx: str) -> None:
        value, _, aa = cx.partition("^^^")
        if aa != self.assigning_authority or not value.startswith(self.prefix):
            raise XdsError("XDSUnknownPatientId",
                           f"{ctx}: patient id {cx!r} is not in the synthetic affinity domain", ctx)

    def check_entry(self, de: DocumentEntry) -> None:
        ctx = f"DocumentEntry {de.entry_uuid}"
        for name, val in [("uniqueId", de.unique_id), ("patientId", de.patient_id), ("mimeType", de.mime_type),
                          ("creationTime", de.creation_time), ("languageCode", de.language),
                          ("sourcePatientId", de.source_patient_id)]:
            if not val:
                raise XdsError("XDSRegistryMetadataError", f"{ctx}: required attribute {name} missing", ctx)
        if not TS.match(de.creation_time):
            raise XdsError("XDSRegistryMetadataError", f"{ctx}: creationTime must be a DTM (YYYYMMDDhhmmss)", ctx)
        self.check_patient(de.patient_id, ctx)
        for key, expected in self.fixed_codes.items():
            got = de.codes.get(key)
            if not got:
                raise XdsError("XDSRegistryMetadataError", f"{ctx}: required code {key} missing", ctx)
            if (got.code, got.scheme) != (expected.code, expected.scheme):
                raise XdsError("XDSRegistryMetadataError",
                               f"{ctx}: {key} {got.code}^^{got.scheme} not allowed by affinity domain policy", ctx)
        fmt = de.codes.get("formatCode")
        expected_fmt = self.representations.get(de.mime_type)
        if not fmt or not expected_fmt or (fmt.code, fmt.scheme) != (expected_fmt.code, expected_fmt.scheme):
            raise XdsError("XDSRegistryMetadataError",
                           f"{ctx}: formatCode {fmt.code if fmt else None!r} is not the governed format code for "
                           f"mimeType {de.mime_type} (envelope and IPS must not share a format code)", ctx)


@dataclass
class Registry:
    store: RegistryStore
    policy: AffinityDomainPolicy
    audit: AuditLog

    def register(self, sub: Submission) -> list[str]:
        """ITI-42 semantics. Returns entry UUIDs deprecated by this submission."""
        ss = sub.submission_set
        if not ss.unique_id or not ss.submission_time or not ss.content_type.code:
            raise XdsError("XDSRegistryMetadataError", "SubmissionSet uniqueId, submissionTime and "
                                                       "contentTypeCode are required")
        if ss.source_id != self.policy.source_id:
            raise XdsError("XDSRegistryMetadataError", f"unknown SubmissionSet.sourceId {ss.source_id}")
        self.policy.check_patient(ss.patient_id, "SubmissionSet")
        if self.store.submission_set_unique_id_exists(ss.unique_id):
            raise XdsError("XDSDuplicateUniqueIdInRegistry", f"SubmissionSet uniqueId {ss.unique_id} exists")
        in_sub = {d.entry_uuid: d for d in sub.documents}
        for de in sub.documents:
            self.policy.check_entry(de)
            if de.patient_id != ss.patient_id:
                raise XdsError("XDSPatientIdDoesNotMatch", "DocumentEntry and SubmissionSet patientId differ",
                               de.entry_uuid)
            if self.store.unique_id_exists(de.unique_id):
                raise XdsError("XDSDuplicateUniqueIdInRegistry", f"uniqueId {de.unique_id} already registered",
                               de.entry_uuid)
            if not de.hash or de.size is None or not de.repository_unique_id:
                raise XdsError("XDSRegistryMetadataError", "hash, size and repositoryUniqueId are required at "
                                                           "registration", de.entry_uuid)
            de.status = STATUS_APPROVED
        members = {a.target for a in sub.associations if a.type == ASSOC_HAS_MEMBER and a.source == ss.entry_uuid}
        missing = set(in_sub) - members
        if missing:
            raise XdsError("XDSRegistryMetadataError", f"DocumentEntries not members of the SubmissionSet: {missing}")

        deprecate: list[str] = []
        for a in sub.associations:
            if a.type in (ASSOC_RPLC, ASSOC_XFRM):
                if a.source not in in_sub:
                    raise XdsError("XDSRegistryMetadataError", f"{a.type} source must be in this submission")
                target = in_sub.get(a.target) or self.store.get(a.target)
                if target is None:
                    raise XdsError("UnresolvedReferenceException", f"{a.type} target {a.target} not found")
                if target.patient_id != in_sub[a.source].patient_id:
                    raise XdsError("XDSPatientIdDoesNotMatch", f"{a.type} target belongs to another patient")
            if a.type == ASSOC_RPLC:
                target = self.store.get(a.target)
                if target is None or target.status != STATUS_APPROVED:
                    raise XdsError("XDSRegistryDeprecatedDocumentError",
                                   f"RPLC target {a.target} is not an Approved registry entry")
                deprecate.append(a.target)
                # Transformations of the replaced document are deprecated with it.
                for rel in self.store.associations_for(a.target):
                    if rel.type == ASSOC_XFRM and rel.target == a.target:
                        t = self.store.get(rel.source)
                        if t and t.status == STATUS_APPROVED:
                            deprecate.append(rel.source)
        self.store.add_submission(ss, sub.documents, sub.associations, deprecate)
        self.audit.record("registry", "ITI-42 register", submission_set=ss.unique_id,
                          documents=[d.unique_id for d in sub.documents], deprecated=deprecate)
        return deprecate

    # ---------------------------------------------------------- ITI-18
    def find_documents(self, patient_cx: str, statuses: list[str],
                       format_codes: list[str] | None = None) -> list[DocumentEntry]:
        self.policy.check_patient(patient_cx, "FindDocuments")
        return self.store.find(patient_cx, statuses, format_codes)

    def get_documents(self, entry_uuids: list[str] = (), unique_ids: list[str] = ()) -> list[DocumentEntry]:
        out = [self.store.get(u) for u in entry_uuids] + [self.store.get_by_unique_id(u) for u in unique_ids]
        return [d for d in out if d]

    def get_related(self, entry_uuid: str, types: list[str]):
        assocs = [a for a in self.store.associations_for(entry_uuid) if a.type in types]
        related = {a.source if a.target == entry_uuid else a.target for a in assocs}
        docs = [d for d in (self.store.get(u) for u in related | {entry_uuid}) if d]
        return docs, assocs


@dataclass
class Repository:
    unique_id: str
    objects: ObjectStore
    registry: Registry
    audit: AuditLog

    def provide_and_register(self, sub: Submission) -> list[str]:
        for de in sub.documents:
            data = sub.contents.get(de.entry_uuid)
            if data is None:
                raise XdsError("XDSMissingDocument", f"no document content for {de.entry_uuid}", de.entry_uuid)
            h, size = hashlib.sha1(data).hexdigest(), len(data)
            if de.hash and de.hash.lower() != h:
                raise XdsError("XDSNonIdenticalHash", "submitted hash does not match document content",
                               de.entry_uuid)
            if de.size is not None and de.size != size:
                raise XdsError("XDSRepositoryMetadataError", "submitted size does not match document content",
                               de.entry_uuid)
            if self.objects.exists(de.unique_id):
                raise XdsError("XDSDuplicateUniqueIdInRegistry", f"uniqueId {de.unique_id} already stored",
                               de.entry_uuid)
            de.hash, de.size, de.repository_unique_id = h, size, self.unique_id
        # Validate everything with the registry policy before writing bytes.
        for de in sub.documents:
            self.registry.policy.check_entry(de)
        for de in sub.documents:
            self.objects.put(de.unique_id, sub.contents[de.entry_uuid], de.mime_type)
        self.audit.record("repository", "ITI-41 store", documents=[d.unique_id for d in sub.documents])
        return self.registry.register(sub)

    def retrieve(self, repository_unique_id: str, document_unique_id: str) -> tuple[bytes, str] | None:
        if repository_unique_id != self.unique_id:
            return None
        found = self.objects.get(document_unique_id)
        self.audit.record("repository", "ITI-43 retrieve", document=document_unique_id, found=found is not None)
        return found


def status_values(raw: list[str]) -> list[str]:
    vals = parse_list(raw)
    allowed = {STATUS_APPROVED, STATUS_DEPRECATED}
    bad = [v for v in vals if v not in allowed]
    if bad:
        raise XdsError("XDSRegistryError", f"unsupported status value(s) {bad}")
    return vals


def parse_list(raw: list[str]) -> list[str]:
    """Parse stored-query parameter values such as ('a','b') or 'a'."""
    out: list[str] = []
    for v in raw:
        v = v.strip()
        if v.startswith("(") and v.endswith(")"):
            v = v[1:-1]
        for part in re.findall(r"'((?:[^']|'')*)'", v) or [v]:
            out.append(part.replace("''", "'"))
    return out
