"""MHD (ITI-67 / ITI-68) and sIPS-aligned FHIR façade over the XDS actors.

The façade behaves as an MHD Document Responder grouped with an XDS Document
Consumer (the grouping is in-process in this release). It presents each XDS
DocumentEntry as a FHIR R4 DocumentReference, so the same issuance is
discoverable through SOAP/XDS and through FHIR/MHD (AT-07). Because the IPS
projection is registered as its own DocumentEntry with the IPS format code,
a consumer can retrieve ``application/fhir+json`` directly without unpacking
the PDF (AT-08, finding F-001).

``Patient/$summary`` provides an on-demand *current* summary, clearly tagged
as distinct from the immutable issued snapshots (finding F-007).

Identity (profile 0.3.0, MET-08/MET-10, review A-04): the DocumentReference ``id`` is assigned by this
server and has no relationship to the XDS entryUUID. The registry identifiers are carried as typed
identifier slices (MHD 4.2.4, ``IHE.MHD.EntryUUID.Identifier`` and ``IHE.MHD.UniqueIdIdentifier``) and are
searchable with the ``identifier`` search parameter. The server keeps the id-to-entryUUID mapping.
"""
from __future__ import annotations

import base64
import binascii
import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from ..xds.actors import Registry, Repository
from ..xds.model import ASSOC_RPLC, ASSOC_XFRM, STATUS_APPROVED, STATUS_DEPRECATED, DocumentEntry

FHIR_JSON = "application/fhir+json"
MHD_PROFILE = "https://profiles.ihe.net/ITI/MHD/StructureDefinition/IHE.MHD.Minimal.DocumentReference"


def _fhir_time(dtm: str) -> str:
    d = dtm.ljust(14, "0")
    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}T{d[8:10]}:{d[10:12]}:{d[12:14]}Z"


MHD_ID_TYPE = "https://profiles.ihe.net/ITI/MHD/CodeSystem/IHE.MHD.MHDIdentifierType"
# Profile 0.4.0 state mapping: MHD 4.2.4 allows only current|superseded, so the lifecycle state (and in particular
# withdrawal) is carried in this non-modifier extension (LIF-12).
LIFECYCLE_EXT = "urn:oid:2.25.11064312502901710892401295388462879468.1"


def _typed(code: str) -> dict:
    return {"coding": [{"system": MHD_ID_TYPE, "code": code}]}


class ResourceIds:
    """Server-assigned FHIR resource ids, kept in a mapping table next to the registry (MET-08)."""

    def __init__(self, db):
        self.db = db
        with db.tx() as c:
            c.execute("CREATE TABLE IF NOT EXISTS mhd_resource_id (id TEXT PRIMARY KEY, entry_uuid TEXT UNIQUE "
                      "NOT NULL)")

    def for_entry(self, entry_uuid: str) -> str:
        row = self.db.one("SELECT id FROM mhd_resource_id WHERE entry_uuid=?", (entry_uuid,))
        if row:
            return row[0]
        rid = secrets.token_hex(8)
        with self.db.tx() as c:
            c.execute("INSERT OR IGNORE INTO mhd_resource_id VALUES (?,?)", (rid, entry_uuid))
        return self.for_entry(entry_uuid)

    def entry(self, rid: str) -> str | None:
        row = self.db.one("SELECT entry_uuid FROM mhd_resource_id WHERE id=?", (rid,))
        return row[0] if row else None


def parse_xcn(xcn: str) -> dict:
    """XDS legalAuthenticator (XCN) to a contained Practitioner (MHD: authenticator <- legalAuthenticator)."""
    f = (xcn.split("^") + [""] * 10)[:10]
    pr: dict[str, Any] = {"resourceType": "Practitioner", "id": "legal-authenticator",
                          "name": [{"family": f[1], "given": [g for g in [f[2]] if g],
                                    "prefix": [p for p in [f[5]] if p]}]}
    if f[0]:
        oid = f[8].split("&")[1] if "&" in f[8] else ""
        pr["identifier"] = [{"system": f"urn:oid:{oid}" if oid else "urn:ietf:rfc:3986", "value": f[0]}]
    return pr


# XDS codingScheme OID -> FHIR canonical system URI (MHD mapping)
OID_TO_SYSTEM = {
    "2.16.840.1.113883.6.1": "http://loinc.org",
    "2.16.840.1.113883.5.25": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
}


def _coding(code) -> dict:
    sys = OID_TO_SYSTEM.get(code.scheme) or (f"urn:oid:{code.scheme}" if code.scheme[:1].isdigit() else code.scheme)
    return {"system": sys, "code": code.code, "display": code.display}


def to_document_reference(de: DocumentEntry, rid: str, base: str, related: list[tuple[str, str]],
                          fhir_patient_system: str, indexed: str | None = None,
                          lifecycle_state: str | None = None) -> dict[str, Any]:
    """``related`` holds (code, server-assigned id of the target). ``indexed`` is the submission time of the
    SubmissionSet that registered the entry: DocumentReference.date is when the reference was created (FHIR R4),
    while the clinical content time is content.attachment.creation (MHD 4.2.4 mapping of creationTime)."""
    value = de.patient_id.split("^^^")[0]
    uid = f"urn:oid:{de.unique_id}"
    dr: dict[str, Any] = {
        "resourceType": "DocumentReference",
        "id": rid,
        "meta": {"profile": [MHD_PROFILE]},
        "masterIdentifier": {"type": _typed("uniqueId"), "system": "urn:ietf:rfc:3986", "value": uid},
        "identifier": [
            {"type": _typed("entryUUID"), "system": "urn:ietf:rfc:3986", "value": de.entry_uuid},
            {"type": _typed("uniqueId"), "system": "urn:ietf:rfc:3986", "value": uid},
        ],
        "status": "current" if de.status == STATUS_APPROVED else "superseded",
        "type": {"coding": [_coding(de.codes["typeCode"])]},
        "category": [{"coding": [_coding(de.codes["classCode"])]}],
        "subject": {"identifier": {"system": fhir_patient_system, "value": value}, "display": value},
        "date": _fhir_time(indexed or de.creation_time),
        "author": [{"display": de.author_institution.split("^")[0] or "unknown"}],
        "description": de.title,
        "securityLabel": [{"coding": [_coding(de.codes["confidentialityCode"])]}],
        "content": [{
            "attachment": {
                "contentType": de.mime_type, "language": de.language,
                "url": f"{base}/fhir/Binary/{rid}", "size": de.size,
                "hash": base64.b64encode(binascii.unhexlify(de.hash)).decode() if de.hash else None,
                "title": de.title, "creation": _fhir_time(de.creation_time)},
            "format": _coding(de.codes["formatCode"])}],
        "context": {"facilityType": {"coding": [_coding(de.codes["healthcareFacilityTypeCode"])]},
                    "practiceSetting": {"coding": [_coding(de.codes["practiceSettingCode"])]}},
    }
    if de.author_person:
        xcn = de.author_person.split("^")
        dr["author"][0]["display"] += f" - {xcn[1] if len(xcn) > 1 and xcn[1] else xcn[0]}"
    if lifecycle_state:
        dr["extension"] = [{"url": LIFECYCLE_EXT, "valueCode": lifecycle_state}]
    if de.legal_authenticator:
        dr["contained"] = [parse_xcn(de.legal_authenticator)]
        dr["authenticator"] = {"reference": "#legal-authenticator"}
    if related:
        dr["relatesTo"] = [{"code": code, "target": {"reference": f"DocumentReference/{t}"}} for code, t in related]
    return dr


def derived_state(registry: Registry, de: DocumentEntry) -> str:
    """Lifecycle state from registry metadata alone (state mapping, XDS layer): Approved is issued; Deprecated with
    an RPLC successor is replaced; Deprecated without one is withdrawn. An envelope takes the state of its IPS."""
    if de.status == STATUS_APPROVED:
        return "issued"
    target = de.entry_uuid
    for a in registry.store.associations_for(de.entry_uuid):
        if a.type == ASSOC_XFRM and a.source == de.entry_uuid:
            target = a.target
    succ = [a for a in registry.store.associations_for(target) if a.type == ASSOC_RPLC and a.target == target]
    return "replaced-for-update" if succ else "withdrawn"


def build_router(registry: Registry, repository: Repository, settings,
                 on_demand: Callable[[str], dict[str, Any]],
                 lifecycle: Callable[[str], str | None] | None = None) -> APIRouter:
    """``lifecycle(entryUUID)`` returns the issuer's recorded lifecycle state, which distinguishes a correction from
    an update; without it the state is derived from registry metadata."""
    router = APIRouter(prefix="/fhir", tags=["IHE MHD / sIPS (FHIR R4)"])
    ad = settings.affinity_domain["affinity_domain"]
    patient_system = ad["patient_assigning_authority"]["fhir_system"]

    ids = ResourceIds(registry.store.db)

    def base(request: Request) -> str:
        return str(request.base_url).rstrip("/")

    def related(de: DocumentEntry) -> list[tuple[str, str]]:
        out = []
        for a in registry.store.associations_for(de.entry_uuid):
            if a.source == de.entry_uuid and a.type == ASSOC_RPLC:
                out.append(("replaces", ids.for_entry(a.target)))
            elif a.source == de.entry_uuid and a.type == ASSOC_XFRM:
                out.append(("transforms", ids.for_entry(a.target)))
        return out

    def docref(de: DocumentEntry, b: str) -> dict:
        state = (lifecycle(de.entry_uuid) if lifecycle else None) or derived_state(registry, de)
        return to_document_reference(de, ids.for_entry(de.entry_uuid), b, related(de), patient_system,
                                     indexed=registry.store.submission_time(de.entry_uuid), lifecycle_state=state)

    def by_rid(rid: str) -> DocumentEntry | None:
        entry = ids.entry(rid)
        return registry.store.get(entry) if entry else None

    def by_identifier(token: str) -> DocumentEntry | None:
        system, _, value = token.rpartition("|")
        if system and system != "urn:ietf:rfc:3986":
            return None
        if value.startswith("urn:uuid:"):
            return registry.store.get(value)
        if value.startswith("urn:oid:"):
            return registry.store.get_by_unique_id(value.removeprefix("urn:oid:"))
        return None

    def fhir(obj: dict, status: int = 200) -> Response:
        return Response(json.dumps(obj, indent=2), status_code=status, media_type=FHIR_JSON)

    def outcome(code: str, msg: str, status: int) -> Response:
        return fhir({"resourceType": "OperationOutcome",
                     "issue": [{"severity": "error", "code": code, "diagnostics": msg}]}, status)

    @router.get("/metadata", summary="CapabilityStatement")
    def metadata(request: Request) -> Response:
        return fhir({
            "resourceType": "CapabilityStatement", "status": "active", "kind": "instance", "fhirVersion": "4.0.1",
            "date": "2026-10-07", "format": ["json"],
            "implementation": {"description": "SCD-PoC MHD Document Responder façade (demonstrator, synthetic data only)",
                               "url": base(request) + "/fhir"},
            "rest": [{"mode": "server", "resource": [
                {"type": "DocumentReference", "interaction": [{"code": "read"}, {"code": "search-type"}],
                 "searchParam": [{"name": n, "type": t} for n, t in
                                 [("patient.identifier", "token"), ("identifier", "token"), ("_id", "token"),
                                  ("status", "token"), ("format", "token")]]},
                {"type": "Binary", "interaction": [{"code": "read"}]},
                {"type": "Patient", "operation": [{"name": "summary",
                                                   "definition": "http://hl7.org/fhir/uv/ips/OperationDefinition/summary"}]},
            ]}]})

    @router.get("/DocumentReference", summary="ITI-67 Find Document References")
    def find(request: Request) -> Response:
        params = request.query_params
        b = base(request)
        if params.get("identifier") or params.get("_id"):
            # Find by registry identifier (entryUUID or uniqueId slice) or by server-assigned id.
            de = by_identifier(params["identifier"]) if params.get("identifier") else by_rid(params["_id"])
            docs = [de] if de else []
            entries = [{"fullUrl": f"{b}/fhir/DocumentReference/{ids.for_entry(d.entry_uuid)}",
                        "resource": docref(d, b), "search": {"mode": "match"}} for d in docs]
            registry.audit.record("mhd", "ITI-67 find by identifier", results=len(entries))
            return fhir({"resourceType": "Bundle", "type": "searchset", "total": len(entries), "entry": entries})
        pid = params.get("patient.identifier")
        if not pid or "|" not in pid:
            return outcome("required", "patient.identifier=system|value is required (ITI-67)", 400)
        system, value = pid.split("|", 1)
        if system != patient_system:
            return fhir({"resourceType": "Bundle", "type": "searchset", "total": 0, "entry": []})
        status_map = {"current": STATUS_APPROVED, "superseded": STATUS_DEPRECATED}
        statuses = [status_map[s] for s in params.get("status", "current").split(",") if s in status_map]
        formats = None
        if params.get("format"):
            formats = [f.split("|", 1)[-1] for f in params.get("format").split(",")]
        try:
            docs = registry.find_documents(f"{value}^^^&{ad['patient_assigning_authority']['oid']}&ISO",
                                           statuses or [STATUS_APPROVED], formats)
        except Exception as exc:
            return outcome("processing", str(exc), 400)
        entries = [{"fullUrl": f"{b}/fhir/DocumentReference/{ids.for_entry(d.entry_uuid)}",
                    "resource": docref(d, b), "search": {"mode": "match"}} for d in docs]
        registry.audit.record("mhd", "ITI-67 find", patient=value, results=len(entries))
        return fhir({"resourceType": "Bundle", "type": "searchset", "total": len(entries), "entry": entries})

    @router.get("/DocumentReference/{rid}", summary="Read DocumentReference")
    def read(rid: str, request: Request) -> Response:
        de = by_rid(rid)
        if not de:
            return outcome("not-found", f"DocumentReference/{rid} not found", 404)
        return fhir(docref(de, base(request)))

    @router.get("/Binary/{rid}", summary="ITI-68 Retrieve Document")
    def binary(rid: str, request: Request) -> Response:
        de = by_rid(rid)
        if not de:
            return outcome("not-found", f"Binary/{rid} not found", 404)
        got = repository.retrieve(de.repository_unique_id, de.unique_id)
        if not got:
            return outcome("not-found", "document content not available", 404)
        data, mime = got
        registry.audit.record("mhd", "ITI-68 retrieve", document=de.unique_id, mime=mime)
        accept = request.headers.get("accept", "")
        if FHIR_JSON in accept and mime != FHIR_JSON:
            return fhir({"resourceType": "Binary", "id": rid, "contentType": mime,
                         "data": base64.b64encode(data).decode()})
        return Response(data, media_type=mime)

    @router.get("/Patient/$summary", summary="sIPS on-demand current summary (IPS $summary)")
    def summary(identifier: str) -> Response:
        if "|" not in identifier:
            return outcome("required", "identifier=system|value is required", 400)
        system, value = identifier.split("|", 1)
        if system != patient_system:
            return outcome("not-found", "unknown patient identifier system", 404)
        try:
            bundle = on_demand(value)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return fhir(bundle)

    return router


def on_demand_tag() -> dict:
    return {"system": "urn:scdpoc:tags", "code": "on-demand",
            "display": "Generated on demand from current source data - not a preserved, issued document"}


def now_utc() -> datetime:
    return datetime.now(UTC)
