"""EHDS export adapter boundary (ADR-004).

Everything EHDS-specific sits behind ``ExportAdapter``. The preservation
model, the XDS sharing model and the IPS authoring logic do not depend on
any adapter, so when the detailed EEHRxF / MyHealth@EU requirements are
settled an adopter implements a new adapter here instead of re-engineering
the rest of the system.

Shipped adapters:
* ``ReadinessPreviewAdapter`` - non-normative, configuration-driven report.
* ``MyHealthEuAdapter``       - deliberate placeholder; raises with an
  explanation. It exists to show where an NCPeH integration would plug in.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from ..config import Settings


@dataclass
class ItemResult:
    id: str
    area: str
    requirement: str
    status: str
    evidence: str = ""
    note: str = ""


@dataclass
class ExportResult:
    adapter: str
    normative: bool
    disclaimer: str
    register_version: str
    items: list[ItemResult] = field(default_factory=list)
    payload: dict[str, Any] | None = None   # a projected export payload, when an adapter produces one

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.items:
            out[i.status] = out.get(i.status, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["summary"] = self.summary()
        return d


class ExportAdapter(Protocol):
    name: str

    def export(self, ips_bytes: bytes) -> ExportResult: ...


def _resources(bundle: dict, rt: str) -> list[dict]:
    return [e["resource"] for e in bundle.get("entry", []) if e["resource"]["resourceType"] == rt]


def _section(bundle: dict, loinc: str) -> dict | None:
    comp = bundle["entry"][0]["resource"]
    for s in comp.get("section", []):
        if any(c.get("code") == loinc for c in s.get("code", {}).get("coding", [])):
            return s
    return None


class ReadinessPreviewAdapter:
    """Evaluates config/ehds-readiness.yml against an issued IPS. Never claims conformance."""

    name = "ehds-readiness-preview"

    def __init__(self, settings: Settings):
        self.register = settings.ehds_register

    def export(self, ips_bytes: bytes) -> ExportResult:
        bundle = json.loads(ips_bytes)
        meta = self.register["meta"]
        res = ExportResult(self.name, normative=False, disclaimer=meta["disclaimer"].strip(),
                           register_version=meta["register_version"])
        for item in self.register["items"]:
            check = item.get("check")
            base = dict(id=item["id"], area=item["area"], requirement=item["requirement"],
                        note=(item.get("note") or "").strip())
            if not check:
                res.items.append(ItemResult(status=item.get("status", "unresolved"),
                                            evidence="declared in register (no automated check)", **base))
                continue
            ok, evidence = self._evaluate(bundle, check)
            status = "demonstrated" if ok else ("optional-absent" if item.get("optional") else "gap")
            res.items.append(ItemResult(status=status, evidence=evidence, **base))
        return res

    @staticmethod
    def _evaluate(bundle: dict, check: dict) -> tuple[bool, str]:
        t = check["type"]
        if t == "section_present":
            s = _section(bundle, check["loinc"])
            if not s:
                return False, f"section {check['loinc']} not present"
            if s.get("entry"):
                return True, f"section {check['loinc']} with {len(s['entry'])} entr{'y' if len(s['entry']) == 1 else 'ies'}"
            if s.get("emptyReason"):
                return True, f"section {check['loinc']} present with explicit emptyReason"
            return False, f"section {check['loinc']} has neither entries nor emptyReason"
        if t == "elements_present":
            rs = _resources(bundle, check["resource"])
            if not rs:
                return False, f"no {check['resource']} resource"
            missing = [p for p in check["paths"] if not rs[0].get(p)]
            return (not missing, "all present" if not missing else f"missing: {', '.join(missing)}")
        if t == "coding_system":
            rs = _resources(bundle, check["resource"])
            if not rs:
                return False, f"no {check['resource']} resources in this document"
            coded = [r for r in rs if any(c.get("system") == check["system"]
                                          for c in (r.get(check["element"]) or {}).get("coding", []))]
            return (len(coded) == len(rs), f"{len(coded)}/{len(rs)} {check['resource']} coded with {check['system']}")
        return False, f"unknown check type {t}"


class MyHealthEuAdapter:
    """Placeholder for a future NCPeH / MyHealth@EU integration. Intentionally not implemented."""

    name = "myhealth-eu"

    def export(self, ips_bytes: bytes) -> ExportResult:
        raise NotImplementedError(
            "MyHealth@EU / NCPeH exchange is out of scope for this demonstrator. Implement this adapter "
            "against the adopted EEHRxF specification and your national contact point's onboarding "
            "requirements; see docs/extending.md.")


class SimulatedNcpAdapter:
    """Simulated National Contact Point (country of affiliation) pivot check - v0.2.

    Runs over the IPS that Jurisdiction A's gateway will release. It does not
    transform the document: the FHIR IPS *is* the pivot in this simulation. It
    checks that every clinical code is in the synthetic agreed catalogue
    (config/crossborder/catalogue.yml) and reports the result. It is not
    MyHealth@EU, uses no real catalogue, and establishes no cross-border trust.
    """

    name = "simulated-ncp-a"

    def __init__(self, settings: Settings):
        self.catalogue = settings.catalogue

    def export(self, ips_bytes: bytes) -> ExportResult:
        bundle = json.loads(ips_bytes)
        systems = self.catalogue["systems"]
        res = ExportResult(self.name, normative=False, register_version=self.catalogue["meta"]["version"],
                           disclaimer="Simulated NCP pivot check against a synthetic catalogue. Not MyHealth@EU; "
                                      "no real catalogue, transcoding service or trust framework is involved.")
        n = 0
        for e in bundle.get("entry", [])[1:]:
            r = e["resource"]
            for key in ("code", "medicationCodeableConcept", "vaccineCode"):
                for c in (r.get(key) or {}).get("coding", []):
                    if c.get("system") not in systems:
                        continue
                    n += 1
                    ok = c.get("code") in systems[c["system"]]
                    res.items.append(ItemResult(
                        id=f"CAT-{n:02d}", area="Catalogue", requirement=f"{r['resourceType']}.{key} in agreed catalogue",
                        status="demonstrated" if ok else "gap",
                        evidence=f"{c['system']}|{c.get('code')} ({c.get('display', '')})"
                                 + ("" if ok else " - not in agreed catalogue")))
        res.items.append(ItemResult(id="PIVOT-01", area="Format", requirement="Pivot document released unchanged",
                                    status="demonstrated",
                                    evidence="FHIR IPS bytes released as issued; no transformation in this simulation"))
        res.payload = {"pivotBytes": len(ips_bytes)}
        return res


ADAPTERS = {"preview": ReadinessPreviewAdapter, "simulated-ncp-a": SimulatedNcpAdapter}
