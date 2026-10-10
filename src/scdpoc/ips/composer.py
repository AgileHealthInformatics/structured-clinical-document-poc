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
EDQM = "http://standardterms.edqm.eu"
V3_INTERP = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
DEVICE_SYSTEM = "urn:oid:2.999.1.11"
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


def apply_correction(source: dict[str, Any]) -> dict[str, Any]:
    """Return the source with its ``correction`` block applied (correction demo, profile LIF-07)."""
    cor = source.get("correction")
    if not cor:
        raise ValueError("this synthetic patient has no correction scenario")
    out = copy.deepcopy(source)
    fixes = {f["atc"]: f for f in cor.get("fix_medications", [])}
    for m in out.get("medications", []):
        if m["atc"] in fixes:
            m.update({k: v for k, v in fixes[m["atc"]].items() if k != "atc"})
    return out


def _quantity(q: dict[str, Any]) -> dict[str, Any]:
    return {"value": q["value"], "unit": q["unit"], "system": UCUM, "code": q["unit"]}


def _dosage(m: dict[str, Any]) -> dict[str, Any]:
    d: dict[str, Any] = {"text": m["dosage"]}
    if m.get("timing"):
        t = m["timing"]
        d["timing"] = {"repeat": {"frequency": t["frequency"], "period": t["period"], "periodUnit": t["periodUnit"]}}
    if m.get("asNeeded"):
        d["asNeededBoolean"] = True
    if m.get("route"):
        d["route"] = _cc(EDQM, m["route"]["edqm"], m["route"]["display"])
    if m.get("dose"):
        d["doseAndRate"] = [{"doseQuantity": _quantity(m["dose"])}]
    return _merge(d, m.get("fhir_dosage"))


def _merge(resource: dict[str, Any], extra: dict[str, Any] | None) -> dict[str, Any]:
    """Merge FHIR fragments from the synthetic fixture (``fhir`` / ``fhir_dosage``); a None value removes the key.
    Lets fixtures exercise the coverage matrix without a mapping rule per element."""
    for k, v in (extra or {}).items():
        if v is None:
            resource.pop(k, None)
        else:
            resource[k] = v
    return resource


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
    attestation: dict[str, Any] | None = None,
    status: str = "final",
) -> ComposedDocument:
    """``attestation`` - None for a machine-generated preserved snapshot; for an attested issuance,
    {"name": ..., "time": datetime} describing a recorded attestation action (profile PROV-02)."""
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
    # Provenance roles (profile PROV-01): the content is composed by software (Device author, owned by the
    # source organisation); the organisation is custodian; an attester is present only for an attested issuance.
    device_ref = add("Device", 0, {
        "resourceType": "Device",
        "meta": {"profile": [IPS + "Device-uv-ips"]},
        "identifier": [{"system": DEVICE_SYSTEM, "value": "scdpoc-summary-generator"}],
        "deviceName": [{"name": "SCD-PoC summary generator", "type": "manufacturer-name"}],
        "owner": {"reference": org_ref},
    })
    pract_ref = None
    if attestation:
        pract_ref = add("Practitioner", 0, {
            "resourceType": "Practitioner",
            "meta": {"profile": [IPS + "Practitioner-uv-ips"]},
            "identifier": [{"system": ORG_SYSTEM + ".1", "value": attestation.get("id", "SYN-PRAC-1")}],
            "name": [{"text": attestation["name"], "family": attestation["name"].split()[-1]}],
        })
    subject = {"reference": patient_ref}

    problems = [add("Condition", i, {
        "resourceType": "Condition",
        "meta": {"profile": [IPS + "Condition-uv-ips"]},
        "clinicalStatus": _status_cc("http://terminology.hl7.org/CodeSystem/condition-clinical",
                                     pr.get("clinicalStatus", "active")),
        **({"verificationStatus": _status_cc("http://terminology.hl7.org/CodeSystem/condition-ver-status",
                                             pr["verification"])} if pr.get("verification") else {}),
        "category": [_cc("http://terminology.hl7.org/CodeSystem/condition-category",
                         "problem-list-item", "Problem List Item")],
        **({"severity": _cc(SNOMED, pr["severity"]["snomed"], pr["severity"]["display"])} if pr.get("severity") else {}),
        "code": _cc(SNOMED, pr["snomed"], pr["display"]),
        "subject": subject,
        **({"onsetDateTime": pr["onset"]} if pr.get("onset") else {}),
        **(pr.get("fhir") or {}),
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
                "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification", al.get("verification", "confirmed")),
            "type": "allergy",
            "criticality": al.get("criticality", "unable-to-assess"),
            "code": _cc(SNOMED, al["snomed"], al["display"]),
            "patient": subject,
        }
        if al.get("reaction"):
            res["reaction"] = [{"manifestation": [_cc(SNOMED, al["reaction"]["snomed"], al["reaction"]["display"])],
                                **({"severity": al["reaction"]["severity"]} if al["reaction"].get("severity") else {})}]
        allergies.append(add("AllergyIntolerance", i, _merge(res, al.get("fhir"))))

    meds: list[str] = []
    for i, m in enumerate(source.get("medications", [])):
        if m.get("kind") == "request":
            meds.append(add("MedicationRequest", i, {
                "resourceType": "MedicationRequest",
                "meta": {"profile": [IPS + "MedicationRequest-uv-ips"]},
                "status": m.get("status", "active"),
                "intent": "order",
                "medicationCodeableConcept": _cc(ATC, m["atc"], m["display"]),
                "subject": subject,
                "authoredOn": m["start"],
                "dosageInstruction": [_dosage(m)],
                **(m.get("fhir") or {}),
            }))
        else:
            meds.append(add("MedicationStatement", i, {
                "resourceType": "MedicationStatement",
                "meta": {"profile": [IPS + "MedicationStatement-uv-ips"]},
                "status": m.get("status", "active"),
                "medicationCodeableConcept": _cc(ATC, m["atc"], m["display"]),
                "subject": subject,
                "effectivePeriod": {"start": m["start"]},
                "dosage": [_dosage(m)],
                **(m.get("fhir") or {}),
            }))

    immunizations = [add("Immunization", i, {
        "resourceType": "Immunization",
        "meta": {"profile": [IPS + "Immunization-uv-ips"]},
        "status": "completed",
        "vaccineCode": _cc(ATC, im["atc"], im["display"]),
        "patient": subject,
        "occurrenceDateTime": im["date"],
        **(im.get("fhir") or {}),
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
        **({"interpretation": [_cc(V3_INTERP, r["interpretation"]["code"], r["interpretation"]["display"])]}
           if r.get("interpretation") else {}),
        **(r.get("fhir") or {}),
    }) for i, r in enumerate(source.get("results", []))]

    refs_by_section = {"11450-4": problems, "48765-2": allergies, "10160-0": meds,
                       "11369-6": immunizations, "30954-2": results}
    required = {"11450-4", "48765-2", "10160-0"}
    index = {e["fullUrl"]: e["resource"] for e in entries}
    sections = []
    for code, title in SECTION_ORDER:
        if code not in refs_by_section:
            continue
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
    if source.get("plan_of_care"):
        # Narrative-only section: the narrative is the clinical content (profile REN-02).
        from html import escape as _esc
        sections.append({"title": "Plan of care", "code": _cc(LOINC, "18776-5", "Plan of care note"),
                         "text": {"status": "additional", "div": '<div xmlns="http://www.w3.org/1999/xhtml"><p>'
                                  + _esc(source["plan_of_care"]) + "</p></div>"}})

    composition: dict[str, Any] = {
        "resourceType": "Composition",
        "id": str(uuid.uuid5(document_id, "Composition/0")),
        "meta": {"profile": [IPS + "Composition-uv-ips"]},
        "language": lang,
        "identifier": {"system": SERIES_SYSTEM, "value": series_id},
        "status": status,
        "type": _cc(LOINC, "60591-5", "Patient summary Document"),
        "subject": subject,
        "date": ts,
        "author": [{"reference": device_ref}],
        "title": "International Patient Summary",
        "confidentiality": ad["document_entry"]["confidentialityCode"]["code"],
        "custodian": {"reference": org_ref},
        "section": sections,
    }
    if attestation:
        at = attestation["time"].astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        composition["attester"] = [{"mode": "legal", "time": at, "party": {"reference": pract_ref}}]
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
