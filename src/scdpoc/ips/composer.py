"""Compose an HL7 FHIR IPS document Bundle from a synthetic source record.

The source format (fixtures/synthetic-patients/*.json) stands in for a local
clinical system extract. The composer is the only place that maps local data
to IPS, so an adopter replaces ``SourceRecord`` loading and this mapping with
their own extract while keeping everything downstream unchanged.
"""
from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..config import Settings
from .view import SECTION_ORDER, section_view, section_xhtml

IPS = "http://hl7.org/fhir/uv/ips/StructureDefinition/"
SNOMED = "http://snomed.info/sct"
LOINC = "http://loinc.org"
ATC = "http://www.whocc.no/atc"
UCUM = "http://unitsofmeasure.org"
SERIES_SYSTEM = "urn:oid:2.999.1.7"
ORG_SYSTEM = "urn:oid:2.999.1.8"


@dataclass(frozen=True)
class ComposedDocument:
    document_id: uuid.UUID
    series_id: str
    version: int
    bundle: dict[str, Any]
    json_bytes: bytes          # the exact bytes that are validated, embedded and exchanged

    @property
    def document_urn(self) -> str:
        return f"urn:uuid:{self.document_id}"


def serialise(bundle: dict[str, Any]) -> bytes:
    """Canonical serialisation used once, at issuance. Bytes are never regenerated."""
    return (json.dumps(bundle, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def apply_revision(source: dict[str, Any]) -> dict[str, Any]:
    """Return the source as amended by its ``revision`` block (replacement demo)."""
    rev = source.get("revision")
    if not rev:
        raise ValueError("this synthetic patient has no revision scenario")
    out = copy.deepcopy(source)
    stop = set(rev.get("stop_medications", []))
    out["medications"] = [m for m in out.get("medications", []) if m["atc"] not in stop]
    out["medications"] += rev.get("add_medications", [])
    out["results"] = out.get("results", []) + rev.get("add_results", [])
    out["problems"] = out.get("problems", []) + rev.get("add_problems", [])
    return out


def _cc(system: str, code: str, display: str, text: str | None = None) -> dict[str, Any]:
    cc: dict[str, Any] = {"coding": [{"system": system, "code": code, "display": display}]}
    if text:
        cc["text"] = text
    return cc


def _status_cc(system: str, code: str) -> dict[str, Any]:
    return {"coding": [{"system": system, "code": code}]}


def compose_ips(
    source: dict[str, Any],
    settings: Settings,
    *,
    document_id: uuid.UUID | None = None,
    issued: datetime | None = None,
    series_id: str | None = None,
    version: int = 1,
    replaces_document_urn: str | None = None,
) -> ComposedDocument:
    document_id = document_id or uuid.uuid4()
    issued = (issued or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    ts = issued.strftime("%Y-%m-%dT%H:%M:%SZ")
    series_id = series_id or str(uuid.uuid5(uuid.NAMESPACE_URL, f"scdpoc:series:{source['key']}"))
    ad = settings.affinity_domain
    lang = ad["document_entry"]["languageCode"]

    def full_url(kind: str, n: int = 0) -> str:
        return f"urn:uuid:{uuid.uuid5(document_id, f'{kind}/{n}')}"

    entries: list[dict[str, Any]] = []

    def add(kind: str, n: int, resource: dict[str, Any]) -> str:
        fu = full_url(kind, n)
        resource = {"resourceType": resource.pop("resourceType"), "id": fu.split(":")[-1], **resource}
        entries.append({"fullUrl": fu, "resource": resource})
        return fu

    p = source["patient"]
    patient_ref = add("Patient", 0, {
        "resourceType": "Patient",
        "meta": {"profile": [IPS + "Patient-uv-ips"]},
        "identifier": [{"system": ad["affinity_domain"]["patient_assigning_authority"]["fhir_system"],
                        "value": p["identifier"]}],
        "name": [{"text": " ".join([*p["given"], p["family"]]), "family": p["family"], "given": p["given"]}],
        "gender": p["gender"],
        "birthDate": p["birthDate"],
        **({"address": [{"country": p["country"]}]} if p.get("country") else {}),
    })
    org_ref = add("Organization", 0, {
        "resourceType": "Organization",
        "meta": {"profile": [IPS + "Organization-uv-ips"]},
        "identifier": [{"system": ORG_SYSTEM, "value": "SYN-ORG-1"}],
        "name": "Synthetic Health Organisation",
    })
    subject = {"reference": patient_ref}

    problems = [add("Condition", i, {
        "resourceType": "Condition",
        "meta": {"profile": [IPS + "Condition-uv-ips"]},
        "clinicalStatus": _status_cc("http://terminology.hl7.org/CodeSystem/condition-clinical",
                                     pr.get("clinicalStatus", "active")),
        "category": [_cc("http://terminology.hl7.org/CodeSystem/condition-category",
                         "problem-list-item", "Problem List Item")],
        "code": _cc(SNOMED, pr["snomed"], pr["display"]),
        "subject": subject,
        **({"onsetDateTime": pr["onset"]} if pr.get("onset") else {}),
    }) for i, pr in enumerate(source.get("problems", []))]

    allergies: list[str] = []
    if source.get("allergies_none_known"):
        allergies.append(add("AllergyIntolerance", 0, {
            "resourceType": "AllergyIntolerance",
            "meta": {"profile": [IPS + "AllergyIntolerance-uv-ips"]},
            "clinicalStatus": _status_cc("http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                                         "active"),
            "code": _cc(SNOMED, "716186003", "No known allergy (situation)", "No known allergy"),
            "patient": subject,
        }))
    for i, al in enumerate(source.get("allergies", []), start=len(allergies)):
        res: dict[str, Any] = {
            "resourceType": "AllergyIntolerance",
            "meta": {"profile": [IPS + "AllergyIntolerance-uv-ips"]},
            "clinicalStatus": _status_cc("http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                                         al.get("clinicalStatus", "active")),
            "verificationStatus": _status_cc(
                "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification", "confirmed"),
            "type": "allergy",
            "criticality": al.get("criticality", "unable-to-assess"),
            "code": _cc(SNOMED, al["snomed"], al["display"]),
            "patient": subject,
        }
        if al.get("reaction"):
            res["reaction"] = [{"manifestation": [_cc(SNOMED, al["reaction"]["snomed"],
                                                      al["reaction"]["display"])]}]
        allergies.append(add("AllergyIntolerance", i, res))

    meds = [add("MedicationStatement", i, {
        "resourceType": "MedicationStatement",
        "meta": {"profile": [IPS + "MedicationStatement-uv-ips"]},
        "status": m.get("status", "active"),
        "medicationCodeableConcept": _cc(ATC, m["atc"], m["display"]),
        "subject": subject,
        "effectivePeriod": {"start": m["start"]},
        "dosage": [{"text": m["dosage"]}],
    }) for i, m in enumerate(source.get("medications", []))]

    immunizations = [add("Immunization", i, {
        "resourceType": "Immunization",
        "meta": {"profile": [IPS + "Immunization-uv-ips"]},
        "status": "completed",
        "vaccineCode": _cc(ATC, im["atc"], im["display"]),
        "patient": subject,
        "occurrenceDateTime": im["date"],
    }) for i, im in enumerate(source.get("immunizations", []))]

    results = [add("Observation", i, {
        "resourceType": "Observation",
        "meta": {"profile": [IPS + "Observation-results-laboratory-pathology-uv-ips"]},
        "status": "final",
        "category": [_cc("http://terminology.hl7.org/CodeSystem/observation-category",
                         "laboratory", "Laboratory")],
        "code": _cc(LOINC, r["loinc"], r["display"]),
        "subject": subject,
        "effectiveDateTime": r["date"],
        "performer": [{"reference": org_ref}],
        "valueQuantity": {"value": r["value"], "unit": r["unit"], "system": UCUM, "code": r["ucum"]},
    }) for i, r in enumerate(source.get("results", []))]

    refs_by_section = {"11450-4": problems, "48765-2": allergies, "10160-0": meds,
                       "11369-6": immunizations, "30954-2": results}
    required = {"11450-4", "48765-2", "10160-0"}
    index = {e["fullUrl"]: e["resource"] for e in entries}
    sections = []
    for code, title in SECTION_ORDER:
        refs = refs_by_section[code]
        if not refs and code not in required:
            continue
        section: dict[str, Any] = {"title": title, "code": _cc(LOINC, code, title)}
        if refs:
            section["entry"] = [{"reference": r} for r in refs]
        else:
            section["emptyReason"] = _cc("http://terminology.hl7.org/CodeSystem/list-empty-reason",
                                         "nilknown", "Nil Known")
        section["text"] = {"status": "generated", "div": section_xhtml(section_view(section, index.get))}
        sections.append(section)

    composition: dict[str, Any] = {
        "resourceType": "Composition",
        "id": str(uuid.uuid5(document_id, "Composition/0")),
        "meta": {"profile": [IPS + "Composition-uv-ips"]},
        "language": lang,
        "identifier": {"system": SERIES_SYSTEM, "value": series_id},
        "status": "final",
        "type": _cc(LOINC, "60591-5", "Patient summary Document"),
        "subject": subject,
        "date": ts,
        "author": [{"reference": org_ref}],
        "title": "International Patient Summary",
        "confidentiality": ad["document_entry"]["confidentialityCode"]["code"],
        "custodian": {"reference": org_ref},
        "section": sections,
    }
    if replaces_document_urn:
        composition["relatesTo"] = [{"code": "replaces",
                                     "targetIdentifier": {"system": "urn:ietf:rfc:3986",
                                                          "value": replaces_document_urn}}]

    bundle = {
        "resourceType": "Bundle",
        "id": str(document_id),
        "meta": {"profile": [IPS + "Bundle-uv-ips"]},
        "language": lang,
        "identifier": {"system": "urn:ietf:rfc:3986", "value": f"urn:uuid:{document_id}"},
        "type": "document",
        "timestamp": ts,
        "entry": [{"fullUrl": f"urn:uuid:{composition['id']}", "resource": composition}, *entries],
    }
    return ComposedDocument(document_id=document_id, series_id=series_id, version=version,
                            bundle=bundle, json_bytes=serialise(bundle))
