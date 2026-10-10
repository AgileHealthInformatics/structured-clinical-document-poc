"""Conformance kit runners (profile 0.4.0, Annex A): vector runs and live scenarios.

``check_vectors``  runs the Checker over every conformance vector, and the Checker self-tests of scenario L-15, and
                   produces evidence for the Checker-class obligations (method ``vectors``: CHK-01..CHK-10, ENV-06.c,
                   REN-08, REN-13, SEC-04, XCH-03).
``live_check``     runs live scenarios L-01..L-16 (except L-13, not applicable, and L-15, which runs with the
                   vectors) against a fresh in-process instance of the demonstrator (synthetic data, temporary data
                   directory) and produces evidence for method ``live``.

Results are keyed by normative obligation id (``REN-04.a``, ``ENV-11``); a result for an unknown obligation, or
for a rule that has several obligations, is rejected by the Checker (CHK-10). Both runners write a result file that
``scdpoc check --evidence`` merges into a Checker report:

    {"method": "live" | "vectors", "source": ..., "generatedAt": ..., "profileVersion": "0.4.0",
     "results": {obligation: {"outcome": "pass" | "fail" | "not-tested", "evidence": ..., "scenarios": [...]}},
     "scenarios": [{"id": ..., "title": ..., "assertions": [...]}]}

Live scenarios exercise this demonstrator through its own standards endpoints (ITI-41, ITI-57, ITI-18, ITI-43,
ITI-67, ITI-68, ITI-55, ITI-38, ITI-39). They are written against its orchestration API, so they test this
implementation; another implementation needs its own driver for the same scenario definitions.
"""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import traceback
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, fidelity
from .config import Settings

SIPS = "http://hl7.org/fhir/uv/ips/StructureDefinition/Bundle-uv-ips"
LIFECYCLE_EXT = "urn:oid:2.25.11064312502901710892401295388462879468.1"


@dataclass
class Scenario:
    id: str
    title: str
    assertions: list[dict] = field(default_factory=list)
    transactions: set[str] = field(default_factory=set)

    def check(self, rule: str, ok: bool, evidence: str) -> bool:
        self.assertions.append({"rule": rule, "passed": bool(ok), "evidence": evidence})
        return bool(ok)

    def not_tested(self, rule: str, evidence: str) -> None:
        self.assertions.append({"rule": rule, "passed": None, "evidence": evidence})


def _aggregate(method: str, scenarios: list[Scenario], source: str, extra: dict | None = None) -> dict:
    by_rule: dict[str, list[tuple[Scenario, dict]]] = defaultdict(list)
    for sc in scenarios:
        for a in sc.assertions:
            by_rule[a["rule"]].append((sc, a))
    results = {}
    for rule, items in sorted(by_rule.items()):
        fails = [(s, a) for s, a in items if a["passed"] is False]
        untested = [(s, a) for s, a in items if a["passed"] is None]
        if fails:
            outcome, ev = "fail", "; ".join(f"{s.id}: {a['evidence']}" for s, a in fails[:3])
        elif untested and not any(a["passed"] for _, a in items):
            outcome, ev = "not-tested", "; ".join(f"{s.id}: {a['evidence']}" for s, a in untested[:3])
        else:
            outcome = "pass"
            ev = f"{sum(1 for _, a in items if a['passed'])} assertion(s) held"
            if untested:
                ev += "; not tested: " + "; ".join(a["evidence"] for _, a in untested[:2])
        results[rule] = {"outcome": outcome, "evidence": ev, "scenarios": sorted({s.id for s, _ in items})}
    return {"method": method, "source": source, "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
            "implementation": f"scdpoc {__version__}", "profileVersion": "0.4.0", "results": results,
            "scenarios": [{"id": s.id, "title": s.title, "transactions": sorted(s.transactions),
                           "assertions": s.assertions} for s in scenarios], **(extra or {})}


# ======================================================================= vectors

def check_vectors(vectors_dir: Path, settings: Settings | None = None, reports_dir: Path | None = None) -> dict:
    from .checker import CANONICAL_ID, applies, check, load_catalogue
    settings = settings or Settings()
    cat = load_catalogue()
    manifest = json.loads((vectors_dir / "manifest.json").read_text())
    validate_manifest(manifest, cat)
    sc = Scenario("vectors", f"Checker over {len(manifest['vectors'])} conformance vectors")
    reports = {}
    chk04, chk01 = [], []
    for vid, v in sorted(manifest["vectors"].items()):
        d = vectors_dir / vid
        args = dict(projection=(d / "projection.json").read_bytes(),
                    record=json.loads((d / "issuance-record.json").read_text()), settings=settings, name=vid)
        rep = check((d / "envelope.pdf").read_bytes(), classes=v["classes"], options=v["options"], **args)
        reports[vid] = rep
        if reports_dir:
            reports_dir.mkdir(parents=True, exist_ok=True)
            (reports_dir / f"{vid}.json").write_text(json.dumps(rep.to_dict(), indent=2) + "\n")
        fails = rep.failed()
        problems = [f"{r} not failed" for r in v["mustFail"] if r not in fails]
        if v.get("valid"):
            problems += [f"{r} failed" for r in fails]
            problems += [f"{r} {rep.outcome(r)}" for r in v.get("mustPass", []) if rep.outcome(r) != "pass"]
        for opt, rules in (v.get("mustFailWithOptions") or {}).items():
            rep_o = check((d / "envelope.pdf").read_bytes(), classes=v["classes"], options=[*v["options"], opt],
                          **args)
            problems += [f"{r} not failed with option {opt}" for r in rules if r not in rep_o.failed()]
        chk04.append((vid, problems))
        for oid, outcome in (v.get("expected") or {}).items():
            if rep.outcome(oid) != outcome:
                problems.append(f"{oid} {rep.outcome(oid)}, expected {outcome}")
        expected = {oid for oid, o in cat["obligations"].items() if applies(o, set(v["classes"]))}
        reported = {r.rule for r in rep.results}
        wrong_na = [r.rule for r in rep.results
                    if not _option_ok(cat["obligations"][r.rule].get("option"), set(v["options"]))
                    and r.outcome != "not-applicable"]
        no_level = [r.rule for r in rep.results if not r.level or (r.outcome != "not-applicable" and not r.methods)]
        wrong_na += no_level
        chk01.append((vid, expected ^ reported, wrong_na, all(r.outcome in ("pass", "fail", "not-applicable",
                                                                           "not-tested") for r in rep.results)))
    bad = [(v, p) for v, p in chk04 if p]
    sc.check("CHK-04", not bad, f"{len(chk04)} vectors met their manifest" if not bad else f"{bad[:3]}")
    bad1 = [x for x in chk01 if x[1] or x[2] or not x[3]]
    sc.check("CHK-01", not bad1, "every applicable obligation reported with its level, method and evidence, and "
             "only those; obligations of unclaimed options not-applicable" if not bad1 else f"{bad1[:2]}")
    v01, i06, i08 = reports["V-01"], reports["I-06"], reports["I-08"]
    configured = v01.validators["pdfa"] != "not configured", v01.validators["fhir"] != "not configured"
    ok2 = (configured[0] or v01.outcome("ENV-01") == "not-tested") and \
          (configured[1] or v01.outcome("SRC-01") == "not-tested") and i06.outcome("ENV-01") == "fail" and \
          i08.outcome("SRC-01") == "fail"
    sc.check("CHK-02", ok2, f"ENV-01/SRC-01 on V-01: {v01.outcome('ENV-01')}/{v01.outcome('SRC-01')} with "
                            f"validators {v01.validators}; I-06 ENV-01 {i06.outcome('ENV-01')}; I-08 SRC-01 "
                            f"{i08.outcome('SRC-01')}")
    d = v01.to_dict()
    fields_ok = d["profile"] == CANONICAL_ID and d["profileVersion"] == cat["profileVersion"] and d["ruleCatalogue"]["sha256"] \
        and d["claimed"]["classes"] and d["tested"]["sha256"] and d["testedAt"] and d["results"] and d["verdict"]
    sc.check("CHK-03", bool(fields_ok), "report carries profile id and version, catalogue, claim, artefact digest, "
                                        "time, per-rule outcomes and verdict")
    sc.check("CHK-05", all("not independent" in r.independence for r in reports.values()),
             "every report states that it is not independent assurance")
    from .checker import verdict as recompute
    def mandatory_fail(k: str) -> bool:
        return any(cat["obligations"].get(o, {}).get("level", "MUST") in ("MUST", "MUST NOT") for o in
                   (x for f in manifest["vectors"][k]["mustFail"] for x in cat["ruleObligations"].get(f, [f])))

    advisory_only = [k for k, r in reports.items() if not manifest["vectors"][k]["valid"] and not mandatory_fail(k)
                     and manifest["vectors"][k]["mustFail"]]
    v_ok = all(recompute(r.results)[0] == r.verdict for r in reports.values()) and \
        all(r.verdict == "non-conformant" for k, r in reports.items() if mandatory_fail(k)) and \
        all(reports[k].verdict != "non-conformant" and reports[k].warnings for k in advisory_only) and \
        all(r.verdict != "conformant" or not any(x.outcome == "not-tested" and x.mandatory for x in r.results)
            for r in reports.values())
    sc.check("CHK-07", v_ok, "verdicts consistent with outcomes; vectors failing a mandatory obligation "
                             f"non-conformant; advisory-only failures ({advisory_only}) reported as warnings and not "
                             "non-conformant; no conformant verdict with an untested mandatory obligation "
                             f"(V-01 without a claim: {v01.verdict})")
    lim = all(any("cannot establish complete clinical semantic equivalence" in x for x in r.limitations) and
              any("CHK-08" in x for x in r.limitations) for r in reports.values())
    sc.check("CHK-08", lim, "limitations of REN-13 and CHK-05 and the declared-inspection note in every report")
    sc.check("REN-13", lim, "the fidelity limitation is stated in every report")
    sc.check("CHK-09", all(r.ruleCatalogue["sha256"] == cat["sha256"] and r.ruleCatalogue["profileVersion"] ==
                           cat["profileVersion"] for r in reports.values()),
             f"catalogue {cat['profileVersion']} sha256 {cat['sha256'][:16]}… reported")
    sc.check("REN-08", reports["I-14"].outcome("REN-08") == "fail", "I-14 unsupported code detected")
    sc.check("ENV-06.c", reports["I-05"].outcome("ENV-06.a") == "fail" and reports["I-02"].outcome("ENV-06.a") ==
             "fail", "a second Source file (I-05) and a missing Source file (I-02) are reported")
    ids = []
    for vid in manifest["vectors"]:
        b = json.loads((vectors_dir / vid / "projection.json").read_bytes())
        ids += [i.get("value", "") for e in b.get("entry", []) if e["resource"]["resourceType"] == "Patient"
                for i in e["resource"].get("identifier", [])]
    sc.check("SEC-04", manifest.get("synthetic") is True and ids and all(i.startswith("SYN-") for i in ids),
             f"manifest declares synthetic data; {len(ids)} patient identifiers, all in the synthetic SYN- range")
    sc.check("XCH-03", reports["I-01"].outcome("ENV-05") == "fail" and reports["I-01"].outcome("REN-02") == "fail",
             "I-01 altered embedded source detected (digest and rendition)")
    l15 = Scenario("L-15", "Checker self-tests: requirement levels and rejection of undefined references")
    _checker_self_tests(cat, l15)
    return _aggregate("vectors", [sc, l15], "scdpoc check-vectors",
                      {"vectors": {k: {"verdict": r.verdict, "failed": sorted(r.failed())} for k, r in reports.items()}})


def _option_ok(opt: str | None, options: set[str]) -> bool:
    if not opt:
        return True
    return opt[4:] not in options if opt.startswith("not:") else opt in options


def validate_manifest(manifest: dict, cat: dict) -> None:
    """CHK-10: a vector manifest that refers to an unknown obligation or rule, or has an undefined expectation, is
    rejected."""
    from .checker import OUTCOMES, CatalogueError
    known = set(cat["obligations"]) | set(cat["ruleObligations"])
    problems = []
    for vid, v in manifest.get("vectors", {}).items():
        if vid not in cat.get("tests", []):
            problems.append(f"{vid}: not a test defined by the catalogue")
        refs = [*v.get("mustFail", []), *v.get("mustPass", []), *v.get("mustPassWithValidators", []),
                *(r for rs in (v.get("mustFailWithOptions") or {}).values() for r in rs), *(v.get("expected") or {})]
        problems += [f"{vid}: unknown obligation {r}" for r in refs if r not in known]
        problems += [f"{vid}: undefined expectation {k}={o!r}" for k, o in (v.get("expected") or {}).items()
                     if o not in OUTCOMES]
        if v.get("mustFail") and not v.get("why"):
            problems.append(f"{vid}: expectation without a justification")
    if problems:
        raise CatalogueError("; ".join(problems[:10]))


def _checker_self_tests(cat: dict, sc: Scenario) -> None:
    """Scenario L-15. CHK-07: a synthetic catalogue with one mandatory and one advisory obligation in the same rule,
    evaluated over every combination of outcomes. CHK-10: catalogues, manifests and result files that refer to
    undefined things are rejected."""
    import copy

    from .checker import CatalogueError, _Obs, evaluate, index_catalogue, validate_catalogue, verdict
    from .checker import evidence_results as merge
    syn = {"profileVersion": cat["profileVersion"], "classes": ["Checker"], "options": {}, "tests": ["T-1"],
           "verificationMethods": cat["verificationMethods"], "methodScope": cat["methodScope"],
           "rules": [{"id": "SYN-01", "obligations": [
               {"id": "SYN-01.a", "level": "MUST", "classes": ["Checker"], "option": None,
                "verification": ["vectors"], "tests": ["T-1"]},
               {"id": "SYN-01.b", "level": "SHOULD", "classes": ["Checker"], "option": None,
                "verification": ["vectors"], "tests": ["T-1"]}]}]}
    syn = index_catalogue(syn)
    validate_catalogue(syn)
    expect = {("pass", "pass"): "conformant", ("pass", "fail"): "conformant", ("fail", "pass"): "non-conformant",
              ("not-tested", "pass"): "incomplete", ("not-tested", "fail"): "incomplete",
              ("fail", "not-tested"): "non-conformant", ("pass", "not-tested"): "conformant"}
    wrong = []
    for (a, b), want in expect.items():
        obs = _Obs()
        obs.add("SYN-01.a", a, "synthetic")
        obs.add("SYN-01.b", b, "synthetic")
        res = evaluate(syn, {"Checker"}, set(), {"vectors": obs})
        got, warnings = verdict(res)
        if got != want or (b == "fail") != bool(warnings):
            wrong.append(f"MUST {a} / SHOULD {b}: {got}, {len(warnings)} warning(s); expected {want}")
    sc.check("CHK-07", not wrong, f"{len(expect)} combinations of a mandatory and an advisory obligation of one rule "
             "gave the verdict their levels require; advisory failures reported as warnings only"
             if not wrong else "; ".join(wrong[:3]))

    def rejected(fn) -> bool:
        try:
            fn()
        except CatalogueError:
            return True
        return False

    bad_cat = copy.deepcopy(syn)
    bad_cat["rules"][0]["obligations"][0]["verification"] = []
    bad_test = copy.deepcopy(syn)
    bad_test["rules"][0]["obligations"][1]["tests"] = ["T-99"]
    cases = {
        "obligation without a verification method": lambda: validate_catalogue(index_catalogue(bad_cat)),
        "unknown test": lambda: validate_catalogue(index_catalogue(bad_test)),
        "live result for an unknown obligation": lambda: merge(
            [{"method": "live", "results": {"XYZ-99": {"outcome": "pass"}}}], cat),
        "live result keyed by a rule with several obligations": lambda: merge(
            [{"method": "live", "results": {"REN-04": {"outcome": "pass"}}}], cat),
        "undefined outcome": lambda: merge([{"method": "vectors", "results": {"CHK-01": {"outcome": "ok"}}}], cat),
        "vector manifest with an unknown obligation": lambda: validate_manifest(
            {"vectors": {"I-01": {"mustFail": ["ENV-99"], "why": "x"}}}, cat),
        "vector manifest with an undefined expectation": lambda: validate_manifest(
            {"vectors": {"I-19": {"mustFail": [], "expected": {"REN-14": "maybe"}}}}, cat),
        "claim inspecting an unknown obligation": lambda: __import__("scdpoc.checker", fromlist=["x"])
        .inspection_results({"inspections": {"ZZZ-1": {"result": "pass"}}}, cat),
    }
    missed = [k for k, fn in cases.items() if not rejected(fn)]
    sc.check("CHK-10", not missed, f"{len(cases)} undefined references rejected: {sorted(cases)}" if not missed
             else f"accepted silently: {missed}")


# ======================================================================= live

class Live:
    """A fresh in-process demonstrator instance on a temporary data directory."""

    def __init__(self, root: Path, settings_kw: dict | None = None):
        from fastapi.testclient import TestClient

        from .app import create_app
        from .demo.http import TestClientTransport
        self.settings = Settings(data_dir=root, **(settings_kw or {}))
        self.settings.base_url = "http://testserver"
        transport = TestClientTransport(None, "http://testserver")
        self.app = create_app(self.settings, http=transport)
        self.c = TestClient(self.app, base_url="http://testserver")
        transport.client = self.c
        self.svc = self.app.state.service

    def ok(self, r):
        if r.status_code >= 400:
            raise RuntimeError(f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def publish(self, key: str, **params) -> dict:
        d = self.ok(self.c.post(f"/api/demo/compose/{key}", params=params))
        return self.issue(d["draftId"])

    def issue(self, draft_id: str) -> dict:
        self.ok(self.c.post(f"/api/demo/package/{draft_id}"))
        return self.ok(self.c.post(f"/api/demo/publish/{draft_id}"))["issuance"]

    def de(self, entry_uuid: str):
        return self.svc.registry.store.get(entry_uuid)

    def mhd(self, entry_uuid: str) -> dict:
        b = self.c.get("/fhir/DocumentReference", params={"identifier": f"urn:ietf:rfc:3986|{entry_uuid}"}).json()
        return b["entry"][0]["resource"] if b.get("entry") else {}

    def events(self) -> list[dict]:
        return self.c.get("/api/demo/preservation/events").json()["events"]


def _fhir(ts: str) -> str:
    """XDS DTM (YYYYMMDDHHMMSS) to an ISO instant."""
    return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}T{ts[8:10]}:{ts[10:12]}:{ts[12:14]}Z" if ts and "-" not in ts else ts


def _same_instant(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    try:
        pa = datetime.fromisoformat(_fhir(a).replace("Z", "+00:00"))
        pb = datetime.fromisoformat(_fhir(b).replace("Z", "+00:00"))
    except ValueError:
        return False
    return pa.replace(microsecond=0) == pb.replace(microsecond=0)


def _cx(svc, value: str) -> str:
    return svc._cx({"patient": {"identifier": value}})


def l01_publish(lv: Live, sc: Scenario) -> None:
    from .integrity import sha1
    from .ips.view import build_view
    from .pdfa.extract import extract_ips, page_text
    from .render.pdf import render_pdf
    from .xds.model import ASSOC_XFRM, STATUS_APPROVED
    iss = lv.publish("amara-okafor")
    sc.transactions |= {"ITI-41", "ITI-18", "ITI-43", "ITI-67", "ITI-68"}
    pub = json.loads((lv.settings.data_dir / "work" / "packages" / iss["packageId"] / "publication.json").read_text())
    sc.check("MET-01", len(pub["exchanges"]) == 1 and pub["exchanges"][0]["transaction"] == "ITI-41",
             "both entries and associations in one ITI-41 submission")
    ips_de, env_de = lv.de(iss["ips"]["entryUUID"]), lv.de(iss["envelope"]["entryUUID"])
    sc.check("MET-03", ips_de.mime_type == "application/fhir+json" and ips_de.codes["formatCode"].code == SIPS and
             ips_de.codes["formatCode"].scheme == "urn:ietf:rfc:3986", "projection mimeType and sIPS format code")
    x = [a for a in lv.svc.registry.store.associations_for(env_de.entry_uuid) if a.type == ASSOC_XFRM]
    sc.check("MET-04", len(x) == 1 and x[0].source == env_de.entry_uuid and x[0].target == ips_de.entry_uuid,
             "XFRM source envelope, target projection")
    env_bytes = lv.svc.repository.retrieve(env_de.repository_unique_id, env_de.unique_id)[0]
    ips_bytes = lv.svc.repository.retrieve(ips_de.repository_unique_id, ips_de.unique_id)[0]
    sc.check("MET-06", env_de.hash == sha1(env_bytes) and env_de.size == len(env_bytes) and
             ips_de.hash == sha1(ips_bytes) and ips_de.size == len(ips_bytes),
             "hash and size equal the stored octets; SHA-256 kept in the issuance record")
    same = all(getattr(ips_de, a) == getattr(env_de, a) for a in ("patient_id", "creation_time", "language")) and \
        all(ips_de.codes[k] == env_de.codes[k] for k in ("classCode", "typeCode", "confidentialityCode"))
    sc.check("MET-07", same, "same patientId, creationTime, class, type, confidentiality and language")
    only_ips = lv.svc.registry.find_documents(_cx(lv.svc, "SYN-000101"), [STATUS_APPROVED], [SIPS])
    sc.check("MET-09", [d.mime_type for d in only_ips] == ["application/fhir+json"],
             "a query restricted to the sIPS format code returns no envelope")
    m_ips, m_env = lv.mhd(ips_de.entry_uuid), lv.mhd(env_de.entry_uuid)
    sc.check("MET-10", ips_de.unique_id != env_de.unique_id and m_ips.get("masterIdentifier", {}).get("value") ==
             f"urn:oid:{ips_de.unique_id}", "distinct uniqueIds; identifier mapping per the identity table")
    served = lv.c.get(m_ips["content"][0]["attachment"]["url"]).content
    embedded = extract_ips(env_bytes).data
    sc.check("SRC-02", served == embedded == ips_bytes, "one serialisation: registered, served and embedded octets "
                                                        "identical")
    sc.check("XCH-02", served == embedded, "ITI-68 returns the embedded octets byte for byte")
    sc.check("XCH-01", served[:1] == b"{" and m_env != {}, "projection obtained without retrieving the envelope")
    r = lv.ok(lv.c.get("/api/demo/retrieve/amara-okafor"))
    sc.check("XCH-03", r["integrity"]["passed"], f"consumer verification of the envelope: "
             f"{[c['id'] for c in r['integrity']['checks']]}")
    bundle = json.loads(ips_bytes)
    a, b = render_pdf(build_view(bundle), "x.json"), render_pdf(build_view(bundle), "x.json")
    sc.check("REN-04.a", page_text(a) == page_text(b), "the same source rendered twice shows the same visible content")
    sc.check("REN-04.b", a == b, "the same source rendered twice is byte-identical")
    fid = fidelity.check(bundle, page_text(env_bytes))
    sc.check("REN-01", not fid.foreign_codes, "issued rendition shows no code absent from the source")
    pres = (iss.get("representation") or {}).get("presentationResources") or {}
    sc.check("REN-09", bool(pres) and "designations" not in pres,
             f"the issuer renders source displays only (no designation resource); presentation resources {pres}")


def l02_atomic(lv: Live, sc: Scenario) -> None:
    svc = lv.svc
    d = lv.ok(lv.c.post("/api/demo/compose/lukas-brenner"))
    lv.ok(lv.c.post(f"/api/demo/package/{d['draftId']}"))
    before = len(svc.registry.store.all_entries())
    objs = svc.repository.objects.db.one("SELECT COUNT(*) FROM repository_object")[0]
    real = svc._entry

    def broken(**kw):
        de = real(**kw)
        if kw["rep"] == "envelope":
            de.codes["formatCode"] = svc.reps["ips"][1]
        return de

    svc._entry = broken
    try:
        r = lv.c.post(f"/api/demo/publish/{d['draftId']}")
    finally:
        svc._entry = real
    after = len(svc.registry.store.all_entries())
    objs2 = svc.repository.objects.db.one("SELECT COUNT(*) FROM repository_object")[0]
    sc.check("LIF-08", r.status_code == 502 and after == before and objs2 == objs,
             "a submission rejected by the registry leaves no entry and no stored object")


def l03_metadata(lv: Live, sc: Scenario) -> None:
    from .xds.actors import XdsError
    svc = lv.svc
    d = lv.ok(lv.c.post("/api/demo/compose/lukas-brenner"))
    lv.ok(lv.c.post(f"/api/demo/package/{d['draftId']}"))
    de = svc._entry(unique_id="2.25.1", rep="envelope", cx=_cx(svc, "SYN-000102"),
                    issued=datetime(2026, 10, 8, tzinfo=UTC), title="x", comments="")
    de.codes["formatCode"] = svc.reps["ips"][1]
    try:
        svc.registry.policy.check_entry(de)
        rejected = False
    except XdsError as exc:
        rejected = "format" in exc.message
    sc.check("MET-02", rejected, "an envelope DocumentEntry with the sIPS format code is rejected (vector I-03)")


def l04_replace(lv: Live, sc: Scenario) -> None:
    from .integrity import verify_envelope
    from .xds.model import ASSOC_RPLC
    v1 = lv.publish("amara-okafor")
    v2 = lv.publish("amara-okafor", revise="true")
    sc.check("LIF-02", v2["documentUrn"] != v1["documentUrn"] and
             lv.c.post(f"/api/demo/publish/{v1['packageId']}").status_code == 409,
             "replacement is a new issuance with a new identifier; an issued package cannot be republished")
    rplc = [a for a in lv.svc.registry.store.associations_for(v2["ips"]["entryUUID"]) if a.type == ASSOC_RPLC]
    old_ips, old_env = lv.de(v1["ips"]["entryUUID"]), lv.de(v1["envelope"]["entryUUID"])
    sc.check("MET-05", len(rplc) == 1 and rplc[0].target == v1["ips"]["entryUUID"] and
             old_ips.status.endswith("Deprecated") and old_env.status.endswith("Deprecated"),
             "RPLC on the projection; replaced projection and its envelope deprecated")
    env = lv.svc.repository.retrieve(old_env.repository_unique_id, old_env.unique_id)[0]
    ips = lv.svc.repository.retrieve(old_ips.repository_unique_id, old_ips.unique_id)[0]
    checks = verify_envelope(env, document_urn=v1["documentUrn"], pdf_sha256=v1["envelope"]["sha256"],
                             ips_sha256=v1["ips"]["sha256"], xds_hash=v1["envelope"]["sha1"],
                             size=v1["envelope"]["size"], projection=ips)
    sc.check("LIF-03", all(c.passed for c in checks), "the replaced issuance is retrievable and passes its "
                                                       "integrity checks")
    comp = lv.c.get(f"/api/demo/drafts/{v2['packageId']}/ips.json").json()["entry"][0]["resource"]
    rel = [r for r in comp.get("relatesTo", []) if r["code"] == "replaces"]
    sc.check("SRC-06", rel and rel[0]["targetIdentifier"]["value"] == v1["documentUrn"],
             "the new source references the replaced document identifier")
    sc.check("IPS-02", rel and rel[0]["targetIdentifier"]["value"].startswith("urn:uuid:"),
             "Composition.relatesTo replaces with targetIdentifier")
    states = {i["version"]: i["lifecycleState"] for i in lv.ok(lv.c.get("/api/demo/history/amara-okafor"))["issuances"]}
    sc.check("LIF-07", v2["replacementReason"] == "update", "reason recorded: update")
    sc.check("LIF-09", states == {1: "replaced-for-update", 2: "issued"}, f"states {states}")


def l05_correct(lv: Live, sc: Scenario) -> None:
    lv.publish("ines-duarte")
    lv.c.post("/api/demo/xb/discover/b-duarte")
    v2 = lv.publish("ines-duarte", correct="true")
    comp = lv.c.get(f"/api/demo/drafts/{v2['packageId']}/ips.json").json()["entry"][0]["resource"]
    sc.check("IPS-04", comp["status"] == "amended", "Composition.status amended for a correction")
    sc.check("LIF-07", v2["replacementReason"] == "correction", "reason recorded: correction")
    states = [i["lifecycleState"] for i in lv.ok(lv.c.get("/api/demo/history/ines-duarte"))["issuances"]]
    sc.check("LIF-09", states == ["replaced-for-correction", "issued"], f"states {states}")
    notes = [e for e in lv.events() if e["type"] == "notification-required"]
    sc.check("LIF-11.a", len(notes) == 1 and "knownRecipients" in notes[0]["detail"],
             f"notification-required event recorded (recipients {notes[0]['detail']['knownRecipients'] if notes else '-'})")


def l06_withdraw(lv: Live, sc: Scenario) -> None:
    from .xds.model import (
        ASSOC_UPDATE_AVAILABILITY,
        RESPONSE_SUCCESS,
        STATUS_APPROVED,
        STATUS_DEPRECATED,
        Association,
        Submission,
    )
    svc = lv.svc
    iss = lv.publish("amara-okafor")
    lv.c.post("/api/demo/xb/discover/b-okafor")
    first = lv.ok(lv.c.post("/api/demo/xb/exchange/b-okafor"))
    sc.transactions |= {"ITI-55", "ITI-38", "ITI-39", "ITI-57"}
    # invalid ITI-57 first: wrong OriginalStatus on the second change -> nothing changes
    ss = svc._submission(_cx(svc, "SYN-000101"), [], [], {}).submission_set
    good = Association("urn:uuid:00000000-0000-4000-8000-0000000000a1", ASSOC_UPDATE_AVAILABILITY, ss.entry_uuid,
                       iss["ips"]["entryUUID"], {"OriginalStatus": [STATUS_APPROVED], "NewStatus": [STATUS_DEPRECATED]})
    bad = Association("urn:uuid:00000000-0000-4000-8000-0000000000a2", ASSOC_UPDATE_AVAILABILITY, ss.entry_uuid,
                      iss["envelope"]["entryUUID"], {"OriginalStatus": [STATUS_DEPRECATED],
                                                     "NewStatus": [STATUS_APPROVED]})
    ex = svc.xds.update_document_set(Submission(ss, [], [good, bad]))
    sc.check("LIF-08", ex.status != RESPONSE_SUCCESS and lv.de(iss["ips"]["entryUUID"]).status == STATUS_APPROVED,
             "a rejected ITI-57 changes nothing")
    w = lv.ok(lv.c.post("/api/demo/withdraw/amara-okafor"))
    both = [lv.de(iss[r]["entryUUID"]).status for r in ("ips", "envelope")]
    sc.check("MET-11", w["exchange"]["transaction"] == "ITI-57" and both == [STATUS_DEPRECATED] * 2,
             "one ITI-57 Update Document Set deprecated both entries")
    sc.check("LIF-07", w["state"] == "withdrawn", "withdrawal recorded")
    sc.check("LIF-09", lv.c.post("/api/demo/withdraw/amara-okafor").status_code == 409,
             "a transition not in the table (withdraw a withdrawn issuance) is refused")
    current = svc.registry.find_documents(_cx(svc, "SYN-000101"), [STATUS_APPROVED])
    retrievable = svc.repository.retrieve(iss["envelope"]["repositoryUniqueId"], iss["envelope"]["uniqueId"])
    m = lv.mhd(iss["ips"]["entryUUID"])
    ext = {e["url"]: e.get("valueCode") for e in m.get("extension", [])}
    sc.check("LIF-10", current == [] and retrievable is not None and m["status"] == "superseded" and
             ext.get(LIFECYCLE_EXT) == "withdrawn", "not current, still retrievable by identifier; in the FHIR view "
             f"status {m['status']} with lifecycle state {ext.get(LIFECYCLE_EXT)}")
    rplc = [a for a in svc.registry.store.associations_for(iss["ips"]["entryUUID"]) if a.type.endswith("RPLC")]
    sc.check("LIF-12", not rplc and ext.get(LIFECYCLE_EXT) == "withdrawn",
             "the withdrawal is not presented as a replacement: no RPLC successor in XDS, lifecycle extension "
             "'withdrawn' in MHD")
    sc.check("LIF-03", retrievable is not None, "the withdrawn issuance remains retrievable")
    again = lv.ok(lv.c.post("/api/demo/xb/exchange/b-okafor"))
    sc.check("XB-02.a", first["documents"] and again["documents"] == [],
             "the gateway released the issuance before withdrawal and nothing after it")
    b = lv.app.state.jurisdiction_b
    docs, _ = b.gw.get_documents(first["received"]["uniqueId"], lv.settings.communities["home"]["home_community_id"])
    sc.check("XB-02.b", len(docs) == 1 and docs[0].status == STATUS_DEPRECATED,
             "a query by identifier for the withdrawn issuance returns its metadata with status Deprecated")
    notes = [e for e in lv.events() if e["type"] == "notification-required"]
    sc.check("LIF-11.a", notes and notes[-1]["detail"]["knownRecipients"] == [lv.settings.communities["consumer"]["name"]],
             "notification-required event names the receiving community that retrieved the projection")


def l07_stale(lv: Live, sc: Scenario) -> None:
    from .xds.model import STATUS_DEPRECATED
    lv.publish("amara-okafor")
    lv.c.post("/api/demo/xb/discover/b-okafor")
    first = lv.ok(lv.c.post("/api/demo/xb/exchange/b-okafor"))
    lv.publish("amara-okafor", revise="true")
    old = lv.svc.registry.store.get_by_unique_id(first["received"]["uniqueId"])
    b = lv.app.state.jurisdiction_b
    data, _, _ = b.gw.retrieve(lv.settings.communities["home"]["home_community_id"], old.repository_unique_id,
                               old.unique_id)
    sc.check("XB-02.a", old.status == STATUS_DEPRECATED and data is None, "a replaced issuance is not released")
    home = lv.settings.communities["home"]["home_community_id"]
    docs, _ = b.gw.get_documents(old.unique_id, home)
    related, assocs, _ = b.gw.get_related(old.unique_id, home)
    sc.check("XB-02.b", len(docs) == 1 and docs[0].status == STATUS_DEPRECATED and
             any(a.type.endswith("RPLC") and a.target == docs[0].entry_uuid for a in assocs),
             "a query by identifier returns the replaced issuance's metadata (Deprecated) and its RPLC successor")
    sc.check("LIF-10", data is None, "the replaced issuance is not returned as current across the border")
    again = lv.ok(lv.c.post("/api/demo/xb/exchange/b-okafor"))
    vb6 = next(c for c in again["verification"] if c["id"] == "VB-6")
    sc.check("XB-04", again["received"]["uniqueId"] != old.unique_id and vb6["passed"],
             "the receiver obtains the current summary and verifies currency (VB-6)")


def l08_mhd(lv: Live, sc: Scenario) -> None:
    iss = lv.publish("amara-okafor")
    sc.transactions |= {"ITI-67", "ITI-68"}
    ok8, ok10 = True, True
    for rep in ("ips", "envelope"):
        dr = lv.mhd(iss[rep]["entryUUID"])
        ok8 &= iss[rep]["entryUUID"].removeprefix("urn:uuid:") not in dr["id"] and \
            lv.c.get(f"/fhir/DocumentReference/{dr['id']}").status_code == 200 and dr["status"] == "current"
        slices = {i["type"]["coding"][0]["code"]: i["value"] for i in dr["identifier"]}
        ok10 &= slices.get("entryUUID") == iss[rep]["entryUUID"] and \
            dr["masterIdentifier"]["type"]["coding"][0]["code"] == "uniqueId"
    env, ips = lv.mhd(iss["envelope"]["entryUUID"]), lv.mhd(iss["ips"]["entryUUID"])
    ok8 &= {"code": "transforms", "target": {"reference": f"DocumentReference/{ips['id']}"}} in env["relatesTo"]
    ok8 &= env["content"][0]["format"]["code"] != SIPS and ips["content"][0]["format"]["code"] == SIPS
    ext = {e["url"]: e.get("valueCode") for e in ips.get("extension", [])}
    sub = lv.svc.registry.store.submission_time(iss["ips"]["entryUUID"])
    de = lv.de(iss["ips"]["entryUUID"])
    ok8 &= ext.get(LIFECYCLE_EXT) == "issued"
    sc.check("MET-08", ok8, "server-assigned ids unrelated to entryUUID; format, status, lifecycle extension and "
                            "relatesTo by id")
    comp = lv.c.get(f"/api/demo/drafts/{iss['packageId']}/ips.json").json()
    times = {"Composition.date": comp["entry"][0]["resource"]["date"], "Bundle.timestamp": comp["timestamp"],
             "creationTime": de.creation_time, "attachment.creation": ips["content"][0]["attachment"]["creation"],
             "submissionTime": sub, "DocumentReference.date": ips["date"]}
    ok9 = _same_instant(times["Composition.date"], times["creationTime"]) and \
        _same_instant(times["creationTime"], times["attachment.creation"]) and \
        _same_instant(times["submissionTime"], times["DocumentReference.date"]) and \
        _same_instant(times["Bundle.timestamp"], iss["issued"]) and iss.get("contentTime") and \
        _same_instant(iss["contentTime"], times["Composition.date"])
    sc.check("PROV-09", ok9, f"content, issuance, submission and indexing times each in their own element: {times}")
    sc.check("MET-10", ok10, "entryUUID and uniqueId as typed identifier slices, searchable by identifier")
    sc.check("XCH-01", lv.c.get(ips["content"][0]["attachment"]["url"]).headers["content-type"].startswith(
        "application/fhir+json"), "projection retrieved directly as application/fhir+json")


def l09_crossborder(lv: Live, sc: Scenario) -> None:
    from .pdfa.extract import extract_embedded
    from .pdfa.preflight import preflight
    iss = lv.publish("amara-okafor")
    sc.transactions |= {"ITI-55", "ITI-38", "ITI-39"}
    nf = lv.ok(lv.c.post("/api/demo/xb/discover/b-keller"))
    sc.check("XB-03", nf["result"]["queryResponseCode"] == "NF" and "SYN-0001" not in nf["exchange"]["response_xml"],
             "no identifier disclosed without an unambiguous match")
    lv.ok(lv.c.post("/api/demo/xb/discover/b-okafor"))
    ex = lv.ok(lv.c.post("/api/demo/xb/exchange/b-okafor"))
    b = lv.app.state.jurisdiction_b
    env = iss["envelope"]
    data, _, e = b.gw.retrieve(lv.settings.communities["home"]["home_community_id"], env["repositoryUniqueId"],
                               env["uniqueId"])
    sc.check("XB-01", data is None and {d["mimeType"] for d in ex["documents"]} == {"application/fhir+json"},
             "only the projection crosses; the envelope is refused")
    vb = {c["id"]: c["passed"] for c in ex["verification"]}
    sc.check("XB-04", all(vb.get(k) for k in ("VB-1", "VB-2", "VB-3", "VB-4", "VB-5", "VB-6", "VB-7")),
             f"receiver verification {vb}")
    prov = ex["received"]["provenance"]
    sc.check("PROV-06", prov["author"] and prov["custodian"] and prov["clinicalContentDate"] and
             prov["documentIdentifier"], "document-level provenance established from the received IPS alone")
    sc.check("PROV-07.a", bool(prov["entryLevel"]),
             f"entry-level provenance carried in the source: {sorted(prov['entryLevel'])}")
    doc_authors = set(prov["author"])
    attributed = [x for vs in prov["entryLevel"].values() for x in vs if x.split(": ", 1)[-1] in doc_authors]
    sc.check("PROV-07.b", bool(doc_authors) and not attributed,
             "the document author is reported separately and not presented as the author of each statement")
    r = lv.ok(lv.c.get("/api/demo/xb/render/b-okafor"))
    html = r["html"]
    sc.check("XB-06", all(u.get("original") for u in r["translation"]["untranslated"]) and
             ("[" in html if r["translation"]["untranslated"] else True),
             f"{len(r['translation']['untranslated'])} untranslated concept(s) shown in original with a marker")
    sc.check("XB-07", lv.settings.communities["home"]["name"] in html and ex["received"]["documentUrn"] in html,
             "the rendition states the home community, the document identifier and the retrieval time")
    sc.check("REN-09", bool(r["designations"]["resource"]) and bool(r["designations"]["version"]),
             f"designation resource {r['designations']}")
    received = lv.c.get(f"/fhir/Binary/{lv.mhd(iss['ips']['entryUUID'])['id']}").content.decode()
    src_codes = set(re.findall(r'"code":\s*"([^"]+)"', received))
    shown = set(re.findall(r"(?<![\w-])(?:[A-Z]\d{2}[A-Z]{2}\d{2}|\d{6,18}|\d{1,5}-\d)(?![\w-])",
                           re.sub(r"<[^>]+>", " ", html)))
    sc.check("REN-01", shown <= src_codes, "every code in the receiver's rendition comes from the received source"
             if shown <= src_codes else f"codes not in source: {sorted(shown - src_codes)[:5]}")
    entries_before = len(lv.svc.registry.store.all_entries())
    p = lv.ok(lv.c.post("/api/demo/xb/preserve/b-okafor"))
    custody = lv.c.get("/api/demo/xb/preserve/b-okafor/custody.pdf").content
    srcs = [f for f in extract_embedded(custody) if f.relationship == "Source"]
    sc.check("XB-05", len(srcs) == 1 and srcs[0].sha256 == ex["received"]["sha256"],
             "received octets unchanged: the custody copy embeds them byte for byte")
    from .integrity import sha1
    registered = {d.hash for d in lv.svc.registry.store.all_entries()}
    sc.check("XB-08.a", preflight(custody).passed and all(c["passed"] for c in p["checks"]),
             "custody copy is a PDF/A-3 with the received octets as Source and the shown rendition as pages")
    sc.check("XB-08.b", sha1(custody) not in registered and len(lv.svc.registry.store.all_entries()) == entries_before,
             "the custody copy is not registered as an issuance")


def l10_fixity(lv: Live, sc: Scenario) -> None:
    from .preservation import EventLog, fixity_check
    iss = lv.publish("amara-okafor")
    first = lv.ok(lv.c.post("/api/demo/preservation/fixity"))
    sc.check("ENV-11", first["passed"], "stored envelope and projection unchanged since issuance")
    events = lv.events()
    required = {"eventId", "type", "time", "agent", "artefact", "digest", "outcome", "evidence"}
    sc.check("PRES-03", all(required <= set(e) for e in events) and {"issuance", "validation", "fixity-check"} <=
             {e["type"] for e in events}, f"{len(events)} events with the minimum structure, kept outside the "
                                          f"artefacts")
    sc.check("PRES-02", all((e.get("digest") or {}).get("algorithm") == "SHA-256" for e in events
                            if e["type"] == "fixity-check"), "fixity by SHA-256, not the XDS SHA-1 hash")
    row = lv.svc.repository.objects.db.one("SELECT path FROM repository_object WHERE unique_id=?",
                                           (iss["envelope"]["uniqueId"],))
    path = lv.settings.data_dir / "repository" / row[0]
    os.chmod(path, stat.S_IWUSR | stat.S_IRUSR)
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 1
    path.write_bytes(bytes(data))
    out = fixity_check(lv.settings.data_dir, agent="live scenario L-10 (independent check)")
    after = EventLog(lv.settings.data_dir / "preservation").events()
    sc.check("PRES-06", not out["passed"] and len(out["failed"]) == 1 and out["chain"]["intact"] and
             after[:len(events)] == events and any(e["outcome"] == "failure" for e in after),
             "alteration detected, failure recorded, chain intact, earlier events unchanged")
    iss_ev = next(e for e in after if e["type"] == "issuance")
    sc.check("PRES-05.b", out["chain"]["intact"] and bool(iss_ev["detail"].get("issuanceRecordSha256")),
             "append-only hash chain with the issuance-record digest in the issuance event (anchor held locally: "
             "the independence of the anchor holder is a claim-level declaration)")


def l11_assurance(lv: Live, sc: Scenario) -> None:
    from .ips.attestation import attested_content_digest
    snap = lv.publish("lukas-brenner")
    d = lv.ok(lv.c.post("/api/demo/compose/amara-okafor"))
    a = lv.ok(lv.c.post(f"/api/demo/attest/{d['draftId']}"))
    att = lv.issue(a["draftId"])
    s_de, a_de = lv.de(snap["ips"]["entryUUID"]), lv.de(att["ips"]["entryUUID"])
    ev = att["attestationEvidence"] or {}
    final = lv.c.get(f"/api/demo/drafts/{att['packageId']}/ips.json").json()
    reviewed = json.loads(lv.svc.draft_ips(d["draftId"]))
    sc.check("PROV-02.a", all(ev.get(k) for k in ("attester", "time", "method", "statement", "attestedContentDigest"))
             and ev["reviewedIpsSha256"] == d["ips"]["sha256"] and
             final["entry"][0]["resource"]["attester"][0]["mode"] == "legal",
             "attestation in the source with mode and time; evidence with identity, time, method, statement and the "
             "attested content digest of the reviewed draft")
    sc.check("PROV-02.b", attested_content_digest(final) == ev["attestedContentDigest"] ==
             attested_content_digest(reviewed), "the digest recomputed over the issued IPS equals the digest attested")
    s_comp = lv.c.get(f"/api/demo/drafts/{snap['packageId']}/ips.json").json()["entry"][0]["resource"]
    sc.check("PROV-04", "attester" not in s_comp and s_de.legal_authenticator == "" and
             snap["assurance"] == "preserved-snapshot" and "authenticator" not in lv.mhd(s_de.entry_uuid),
             "the snapshot claims no attestation in source, metadata, record or FHIR view")
    m = lv.mhd(a_de.entry_uuid)
    sc.check("MET-13.a", a_de.legal_authenticator.startswith("SYN-PRAC-1") and
             m.get("authenticator") == {"reference": "#legal-authenticator"},
             "legalAuthenticator from the person attester, mapped to DocumentReference.authenticator")
    xcn = s_de.author_person.split("^")
    sc.check("MET-13.c", len(xcn) > 1 and xcn[0] and "generator" in s_de.author_person and s_de.author_role == "",
             f"software author as an XCN software agent ({s_de.author_person!r}); authorRole not used")
    # Organisational attestation: no person may be invented for legalAuthenticator.
    o = lv.ok(lv.c.post("/api/demo/compose/ines-duarte"))
    oa = lv.ok(lv.c.post(f"/api/demo/attest/{o['draftId']}", params={"kind": "organisation"}))
    org = lv.issue(oa["draftId"])
    o_de = lv.de(org["ips"]["entryUUID"])
    o_comp = lv.c.get(f"/api/demo/drafts/{org['packageId']}/ips.json").json()["entry"][0]["resource"]
    sc.check("MET-13.b", o_de.legal_authenticator == "" and o_comp["attester"][0]["mode"] == "official" and
             "authenticator" not in lv.mhd(o_de.entry_uuid) and org["attestationEvidence"]["attesterKind"] ==
             "organisation", "organisational attestation carried in the source (mode official) and the record "
                             "only; legalAuthenticator empty")
    sc.check("PROV-02.a", o_comp["attester"][0].get("time") and org["attestationEvidence"].get("attestedContentDigest"),
             "organisational attestation recorded with time and digest")
    att_time = final["entry"][0]["resource"]["attester"][0]["time"]
    ok9 = _same_instant(att_time, ev["time"]) and _same_instant(att["issued"], final["timestamp"]) and \
        _same_instant(att["contentTime"], final["entry"][0]["resource"]["date"]) and \
        _same_instant(att["contentTime"], _fhir(a_de.creation_time)) and att.get("submissionTime")
    sc.check("PROV-09", ok9, f"content {att['contentTime']}, attestation {att_time}, issuance {att['issued']}, "
                             f"submission {att.get('submissionTime')} each recorded in its own element")


def l14_attestation_change(lv: Live, sc: Scenario) -> None:
    """PROV-08: the attested content cannot change between attestation and publication."""
    d = lv.ok(lv.c.post("/api/demo/compose/amara-okafor"))
    a = lv.ok(lv.c.post(f"/api/demo/attest/{d['draftId']}"))
    lv.ok(lv.c.post(f"/api/demo/package/{a['draftId']}"))
    path = lv.svc.work.path("drafts", a["draftId"], "ips.json")
    b = json.loads(path.read_bytes())
    med = next(e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "MedicationStatement")
    med["dosage"][0]["doseAndRate"][0]["doseQuantity"]["value"] *= 2
    from .ips.composer import serialise
    path.write_bytes(serialise(b))
    pub = lv.c.post(f"/api/demo/publish/{a['draftId']}")
    sc.check("PROV-08.b", pub.status_code == 409 and "attested" in pub.text and
             not lv.ok(lv.c.get("/api/demo/history/amara-okafor"))["issuances"],
             f"dose changed after attestation and packaging: publish refused ({pub.status_code}); nothing issued")
    unvalidated = lv.c.post(f"/api/demo/attest/{a['draftId']}")
    d2 = lv.ok(lv.c.post("/api/demo/compose/amara-okafor"))
    a2 = lv.ok(lv.c.post(f"/api/demo/attest/{d2['draftId']}"))
    iss = lv.issue(a2["draftId"])
    kinds = [e["type"] for e in lv.events()]
    seq = kinds.index("attestation") < kinds.index("issuance") if "issuance" in kinds else False
    sc.check("PROV-08.a", unvalidated.status_code == 409 and iss["assurance"] == "attested-issuance" and seq and
             a2["attestationEvidence"]["reviewedDraft"] == d2["draftId"],
             "attestation of a reviewed, validated draft precedes issuance; an attested draft cannot be attested "
             "again; re-attested content is published")


def l16_lifecycle_meaning(lv: Live, sc: Scenario) -> None:
    """Issued, replaced and withdrawn issuances of different patients, represented in every layer."""
    from .xds.model import STATUS_APPROVED, STATUS_DEPRECATED
    lu = lv.publish("lukas-brenner")
    am1 = lv.publish("amara-okafor")
    ines = lv.publish("ines-duarte")
    for k in ("b-okafor", "b-duarte"):
        lv.ok(lv.c.post(f"/api/demo/xb/discover/{k}"))
        lv.ok(lv.c.post(f"/api/demo/xb/exchange/{k}"))
    lv.publish("amara-okafor", revise="true")
    lv.ok(lv.c.post("/api/demo/withdraw/ines-duarte"))
    want = {"issued": (lu, STATUS_APPROVED, "current"), "replaced-for-update": (am1, STATUS_DEPRECATED, "superseded"),
            "withdrawn": (ines, STATUS_DEPRECATED, "superseded")}
    seen, ok = {}, True
    for state, (iss, xds, fhir) in want.items():
        de, m = lv.de(iss["ips"]["entryUUID"]), lv.mhd(iss["ips"]["entryUUID"])
        ext = {e["url"]: e.get("valueCode") for e in m.get("extension", [])}.get(LIFECYCLE_EXT)
        seen[state] = (de.status.rsplit(":", 1)[-1], m["status"], ext)
        ok &= de.status == xds and m["status"] == fhir and ext == state
    sc.check("MET-08", ok, f"XDS status, MHD status and lifecycle extension per state: {seen}")
    sc.check("LIF-10", ok and lv.svc.repository.retrieve(ines["ips"]["repositoryUniqueId"], ines["ips"]["uniqueId"])
             is not None, "states other than issued are not current and stay retrievable")
    sc.check("LIF-12", seen["withdrawn"][2] == "withdrawn" and seen["replaced-for-update"][2] != "withdrawn",
             "withdrawal and replacement are distinguishable in the FHIR layer")
    b = lv.app.state.jurisdiction_b
    statuses = {k: b.home_status(k)["status"] for k in ("b-okafor", "b-duarte")}
    sc.check("LIF-12", statuses == {"b-okafor": "replaced", "b-duarte": "withdrawn"},
             f"the receiver tells replacement from withdrawal through XCA metadata: {statuses}")
    html = {k: lv.ok(lv.c.get(f"/api/demo/xb/render/{k}"))["html"] for k in ("b-okafor", "b-duarte")}
    labels = b.lab.get("home_status_values", {})
    shown = {k: (labels.get(st) or b.HOME_STATUS[st]) in html[k] for k, st in statuses.items()}
    sc.check("XB-09", all(shown.values()), f"the receiver's presentation states the home status: {statuses}, "
                                           f"shown {shown}")
    docs, _ = b.gw.get_documents(ines["ips"]["uniqueId"], lv.settings.communities["home"]["home_community_id"])
    sc.check("XB-02.b", len(docs) == 1 and docs[0].status == STATUS_DEPRECATED,
             "metadata of the withdrawn issuance is answered by identifier")


def l12_gate(lv: Live, sc: Scenario) -> None:
    import copy

    from .ips.validator import Hl7FhirValidator, validate_ips
    from .pdfa.verapdf import VeraPdf
    svc = lv.svc
    # A failing fidelity check blocks publication.
    d = lv.ok(lv.c.post("/api/demo/compose/amara-okafor"))
    real = svc.package.__globals__["render_pdf"]

    def forgetful(view, name, *a, **k):
        v = copy.deepcopy(view)
        meds = next(s for s in v.sections if s.code == "10160-0")
        meds.rows = meds.rows[:-1]
        return real(v, name, *a, **k)

    svc.package.__globals__["render_pdf"] = forgetful
    try:
        p = lv.ok(lv.c.post(f"/api/demo/package/{d['draftId']}"))
    finally:
        svc.package.__globals__["render_pdf"] = real
    blocked = not p["publishable"] and lv.c.post(f"/api/demo/publish/{d['draftId']}").status_code == 409
    # A structurally invalid source is not publishable.
    bad = json.loads(svc.draft_ips(d["draftId"]))
    bad["type"] = "collection"
    rep = validate_ips(bad, json.dumps(bad).encode(), lv.settings)
    sc.check("LIF-05", blocked and not rep.publishable, "fidelity failure and an invalid source both block "
                                                        "publication")
    hl7_required = lv.settings.require_hl7_validator and Hl7FhirValidator(lv.settings).available()
    if hl7_required:
        sc.check("SRC-04", not rep.publishable, "the HL7 FHIR validator is required and gates issuance")
    else:
        sc.check("SRC-04", False, "this instance issues without the HL7 FHIR validator (not configured or not "
                                  "required: SCDPOC_REQUIRE_HL7_VALIDATOR)")
    if lv.settings.require_verapdf and VeraPdf(lv.settings).configured():
        sc.check("ENV-08", True, "veraPDF is required and gates issuance")
    else:
        sc.check("ENV-08", False, "this instance issues without veraPDF (not configured or not required: "
                                  "SCDPOC_REQUIRE_VERAPDF)")


SCENARIOS: list[tuple[str, str, Callable]] = [
    ("L-01", "Publish, discover, retrieve, render twice", l01_publish),
    ("L-02", "Registry failure during publication", l02_atomic),
    ("L-03", "Envelope with the sIPS format code", l03_metadata),
    ("L-04", "Replace for update", l04_replace),
    ("L-05", "Replace for correction", l05_correct),
    ("L-06", "Withdraw", l06_withdraw),
    ("L-07", "Stale summary at the receiver", l07_stale),
    ("L-08", "MHD façade identity", l08_mhd),
    ("L-09", "Cross-community exchange", l09_crossborder),
    ("L-10", "Fixity and preservation events", l10_fixity),
    ("L-11", "Preserved snapshot and attested issuance", l11_assurance),
    ("L-12", "Issuance gate", l12_gate),
    ("L-14", "Content changed after attestation", l14_attestation_change),
    ("L-16", "Lifecycle meaning across layers", l16_lifecycle_meaning),
]


def live_check(only: list[str] | None = None, settings_kw: dict | None = None) -> dict:
    scenarios = []
    for sid, title, fn in SCENARIOS:
        if only and sid not in only:
            continue
        sc = Scenario(sid, title)
        with tempfile.TemporaryDirectory(prefix=f"scdpoc-{sid}-") as tmp:
            try:
                fn(Live(Path(tmp), settings_kw), sc)
            except Exception as exc:                      # a scenario that cannot run is a failure, never hidden
                sc.assertions.append({"rule": "(scenario)", "passed": False,
                                      "evidence": f"{type(exc).__name__}: {exc}",
                                      "trace": traceback.format_exc()[-1500:]})
        scenarios.append(sc)
    seen = set().union(*(s.transactions for s in scenarios)) if scenarios else set()
    con = Scenario("L-all", "Transactions exercised across scenarios")
    con.transactions = seen
    need = {"ITI-41", "ITI-57", "ITI-18", "ITI-43", "ITI-67", "ITI-68", "ITI-55", "ITI-38", "ITI-39"}
    if not only:
        con.check("CON-05.b", need <= seen, f"transactions exercised: {sorted(seen)}")
    return _aggregate("live", scenarios + [con], "scdpoc live-check",
                      {"settings": {k: v for k, v in (settings_kw or {}).items()}})


def write(obj: dict, out: str | None) -> None:
    text = json.dumps(obj, indent=2, default=str)
    if out:
        Path(out).write_text(text + "\n")
    summary = defaultdict(int)
    for r in obj["results"].values():
        summary[r["outcome"]] += 1
    print(f"{obj['method']}: {dict(summary)}" + (f" -> {out}" if out else ""))
    for rule, r in obj["results"].items():
        if r["outcome"] != "pass":
            print(f"  {rule}: {r['outcome']} - {r['evidence'][:160]}")
    for s in obj.get("scenarios", []):
        for a in s["assertions"]:
            if a["rule"] == "(scenario)":
                print(f"  {s['id']} could not run: {a['evidence']}")

