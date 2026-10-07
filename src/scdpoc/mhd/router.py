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
"""
from __future__ import annotations

import base64
import binascii
import json
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


def _id(entry_uuid: str) -> str:
    return entry_uuid.removeprefix("urn:uuid:")


# XDS codingScheme OID -> FHIR canonical system URI (MHD mapping)
OID_TO_SYSTEM = {
    "2.16.840.1.113883.6.1": "http://loinc.org",
    "1.3.6.1.4.1.19376.1.2.3": "http://ihe.net/fhir/ihe.formatcode.fhir/CodeSystem/formatcode",
    "2.16.840.1.113883.5.25": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
}


def _coding(code) -> dict:
    sys = OID_TO_SYSTEM.get(code.scheme) or (f"urn:oid:{code.scheme}" if code.scheme[:1].isdigit() else code.scheme)
    return {"system": sys, "code": code.code, "display": code.display}


def to_document_reference(de: DocumentEntry, base: str, related: list[tuple[str, str]],
                          fhir_patient_system: str) -> dict[str, Any]:
    value = de.patient_id.split("^^^")[0]
    dr: dict[str, Any] = {
        "resourceType": "DocumentReference",
        "id": _id(de.entry_uuid),
        "meta": {"profile": [MHD_PROFILE]},
        "masterIdentifier": {"system": "urn:ietf:rfc:3986", "value": f"urn:oid:{de.unique_id}"},
        "identifier": [{"use": "official", "system": "urn:ietf:rfc:3986", "value": de.entry_uuid}],
        "status": "current" if de.status == STATUS_APPROVED else "superseded",
        "type": {"coding": [_coding(de.codes["typeCode"])]},
        "category": [{"coding": [_coding(de.codes["classCode"])]}],
        "subject": {"identifier": {"system": fhir_patient_system, "value": value}, "display": value},
        "date": _fhir_time(de.creation_time),
        "author": [{"display": de.author_institution.split("^")[0] or "unknown"}],
        "description": de.title,
        "securityLabel": [{"coding": [_coding(de.codes["confidentialityCode"])]}],
        "content": [{
            "attachment": {
                "contentType": de.mime_type, "language": de.language,
                "url": f"{base}/fhir/Binary/{_id(de.entry_uuid)}", "size": de.size,
                "hash": base64.b64encode(binascii.unhexlify(de.hash)).decode() if de.hash else None,
                "title": de.title, "creation": _fhir_time(de.creation_time)},
            "format": _coding(de.codes["formatCode"])}],
        "context": {"facilityType": {"coding": [_coding(de.codes["healthcareFacilityTypeCode"])]},
                    "practiceSetting": {"coding": [_coding(de.codes["practiceSettingCode"])]}},
    }
    if related:
        dr["relatesTo"] = [{"code": code, "target": {"reference": f"DocumentReference/{_id(t)}"}}
                           for code, t in related]
    return dr


def build_router(registry: Registry, repository: Repository, settings,
                 on_demand: Callable[[str], dict[str, Any]]) -> APIRouter:
    router = APIRouter(prefix="/fhir", tags=["IHE MHD / sIPS (FHIR R4)"])
    ad = settings.affinity_domain["affinity_domain"]
    patient_system = ad["patient_assigning_authority"]["fhir_system"]

    def base(request: Request) -> str:
        return str(request.base_url).rstrip("/")

    def related(de: DocumentEntry) -> list[tuple[str, str]]:
        out = []
        for a in registry.store.associations_for(de.entry_uuid):
            if a.source == de.entry_uuid and a.type == ASSOC_RPLC:
                out.append(("replaces", a.target))
            elif a.source == de.entry_uuid and a.type == ASSOC_XFRM:
                out.append(("transforms", a.target))
        return out

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
                                 [("patient.identifier", "token"), ("status", "token"), ("format", "token")]]},
                {"type": "Binary", "interaction": [{"code": "read"}]},
                {"type": "Patient", "operation": [{"name": "summary",
                                                   "definition": "http://hl7.org/fhir/uv/ips/OperationDefinition/summary"}]},
            ]}]})

    @router.get("/DocumentReference", summary="ITI-67 Find Document References")
    def find(request: Request) -> Response:
        params = request.query_params
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
        b = base(request)
        entries = [{"fullUrl": f"{b}/fhir/DocumentReference/{_id(d.entry_uuid)}",
                    "resource": to_document_reference(d, b, related(d), patient_system),
                    "search": {"mode": "match"}} for d in docs]
        registry.audit.record("mhd", "ITI-67 find", patient=value, results=len(entries))
        return fhir({"resourceType": "Bundle", "type": "searchset", "total": len(entries), "entry": entries})

    @router.get("/DocumentReference/{rid}", summary="Read DocumentReference")
    def read(rid: str, request: Request) -> Response:
        de = registry.store.get(f"urn:uuid:{rid}")
        if not de:
            return outcome("not-found", f"DocumentReference/{rid} not found", 404)
        return fhir(to_document_reference(de, base(request), related(de), patient_system))

    @router.get("/Binary/{rid}", summary="ITI-68 Retrieve Document")
    def binary(rid: str, request: Request) -> Response:
        de = registry.store.get(f"urn:uuid:{rid}")
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
