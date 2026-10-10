"""Attestation sequence and attested content digest (profile 0.4.0: PROV-08, IPS-07, PROV-02).

The attester reviews a validated draft. The digest of what was reviewed - the *attested content digest* - is
defined so that adding the attestation does not change it: it is the SHA-256 of the canonical JSON (members
sorted, no insignificant white space, UTF-8) of the Bundle without ``Bundle.id``, ``Bundle.identifier``,
``Bundle.timestamp``, ``Composition.attester`` and the entries referenced only by an attester. The final IPS is
the reviewed draft plus the attestation and nothing else, so anyone can recompute the digest from the issued
document and compare it with the attestation evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from .composer import IPS, ORG_SYSTEM, serialise


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _attester_only_entries(bundle: dict) -> set[str]:
    comp = bundle["entry"][0]["resource"]
    parties = {(a.get("party") or {}).get("reference") for a in comp.get("attester", [])} - {None}
    if not parties:
        return set()
    rest = copy.deepcopy(bundle)
    rest["entry"][0]["resource"].pop("attester", None)
    rest["entry"] = [e for e in rest["entry"] if e.get("fullUrl") not in parties]
    text = json.dumps(rest)
    return {p for p in parties if f'"{p}"' not in text}


def attested_content(bundle: dict) -> dict:
    b = copy.deepcopy(bundle)
    drop = _attester_only_entries(b)
    for k in ("id", "identifier", "timestamp"):
        b.pop(k, None)
    b["entry"][0]["resource"].pop("attester", None)
    b["entry"] = [e for e in b["entry"] if e.get("fullUrl") not in drop]
    return b


def attested_content_digest(bundle: dict) -> str:
    return hashlib.sha256(canonical_json(attested_content(bundle))).hexdigest()


def add_attestation(draft: dict, attestation: dict, document_id: uuid.UUID,
                    issued: datetime) -> tuple[dict, bytes]:
    """Step 4 of the sequence: the final source is the reviewed draft plus the attestation, with its own
    document identifier and issuance time. ``attestation``: {"name", "id", "time", "kind": "person"|"organisation"}."""
    b = copy.deepcopy(draft)
    ts = issued.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    b["id"] = str(document_id)
    b["identifier"] = {"system": "urn:ietf:rfc:3986", "value": f"urn:uuid:{document_id}"}
    b["timestamp"] = ts
    comp = b["entry"][0]["resource"]
    at = attestation["time"].astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if attestation.get("kind") == "organisation":
        party = comp["custodian"]["reference"]
        comp["attester"] = [{"mode": "official", "time": at, "party": {"reference": party}}]
    else:
        fu = f"urn:uuid:{uuid.uuid5(document_id, 'Practitioner/0')}"
        b["entry"].append({"fullUrl": fu, "resource": {
            "resourceType": "Practitioner", "id": fu.split(":")[-1],
            "meta": {"profile": [IPS + "Practitioner-uv-ips"]},
            "identifier": [{"system": ORG_SYSTEM + ".1", "value": attestation.get("id", "SYN-PRAC-1")}],
            "name": [{"text": attestation["name"], "family": attestation["name"].split()[-1]}]}})
        comp["attester"] = [{"mode": "legal", "time": at, "party": {"reference": fu}}]
    return b, serialise(b)
