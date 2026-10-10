"""Review 2 items A-03 (rigorous Checker obligations), A-07 (dependency lock), A-08 (options) and A-10 (requirement
coverage of vectors and live scenarios), as revised for profile 0.4.0 (review 3: normative obligations, actor
bindings, dependency statuses, lifecycle meaning)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scdpoc import checker
from scdpoc.checker import CANONICAL_ID, check, issuance_record_from_demo, load_catalogue, load_dependencies
from scdpoc.ips.validator import EngineResult, Hl7FhirValidator
from scdpoc.pdfa.verapdf import VeraPdf, VeraPdfResult
from tests.conftest import mhd_doc

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "conformance" / "vectors"
MANIFEST = json.loads((VECTORS / "manifest.json").read_text())


def _vector(vid: str, **kw):
    d = VECTORS / vid
    v = MANIFEST["vectors"][vid]
    kw.setdefault("classes", v["classes"])
    kw.setdefault("options", v["options"])
    return check((d / "envelope.pdf").read_bytes(), projection=(d / "projection.json").read_bytes(),
                 record=json.loads((d / "issuance-record.json").read_text()), name=vid, **kw)


@pytest.fixture()
def validators_pass(monkeypatch):
    """Stand-ins for veraPDF and the HL7 FHIR validator that pass (their real runs are in CI)."""
    monkeypatch.setattr(VeraPdf, "configured", lambda self: True)
    monkeypatch.setattr(VeraPdf, "validate", lambda self, b: VeraPdfResult("passed", "stand-in: compliant"))
    monkeypatch.setattr(Hl7FhirValidator, "available", lambda self: True)
    monkeypatch.setattr(Hl7FhirValidator, "run", lambda self, b: EngineResult("hl7-fhir-validator", "passed"))


def _claim(**over) -> dict:
    deps = load_dependencies()
    claim = {
        "profile": CANONICAL_ID, "profileVersion": "0.4.0", "classes": ["Envelope"], "options": [],
        "actorBindings": {},
        "bindings": {"content": "IPS 2.0.1", "exchange": "IHE"},
        "dependencies": {k: {"version": checker._lock_version(d), "status": "exact"} for k, d in deps.items()
                         if not k.startswith("__")},
        "validators": {"hl7-validator": "7.0.1", "verapdf": "1.30.3"},
        "transactions": [],
        "statement": "Envelope conformance to the IPS Preservation Envelope Profile 0.4.0 only.",
        "declarations": {"SEC-02": {"identification": "n/a: synthetic", "authentication": "n/a", "consent": "n/a",
                                    "audit": "n/a", "lawfulBasis": "n/a"},
                         "SEC-03": {"clinicalRiskManagement": "test fixture (synthetic)"}},
        "evidenceManifest": {"release": "test", "ruleCatalogueSha256": load_catalogue()["sha256"],
                             "vectors": "conformance/vectors/manifest.json", "reports": ["V-01.json"]},
    }
    claim.update(over)
    return claim


# ------------------------------------------------------------------ the catalogue

def test_catalogue_matches_checker_and_spec():
    cat = load_catalogue()
    assert cat["profile"] == CANONICAL_ID and cat["profileVersion"] == checker.PROFILE_VERSION
    ids = [r["id"] for r in cat["rules"]]
    assert len(ids) == len(set(ids)) >= 109
    obs = cat["obligations"]
    assert len(obs) >= 128 and all(o["level"] in checker.MANDATORY | checker.ADVISORY for o in obs.values())
    assert all(o["verification"] for o in obs.values()), "every obligation has a verification method (A-10)"
    assert all(set(o["verification"]) <= set(cat["verificationMethods"]) for o in obs.values())
    tested_ids = {t for o in obs.values() for t in o["tests"]}
    assert {v for v in tested_ids if v[:2] in ("V-", "I-")} - {"I-03"} <= set(MANIFEST["vectors"])
    from scdpoc.conformance_kit import SCENARIOS
    assert {t for t in tested_ids if t.startswith("L-")} - {"L-13", "L-15"} <= {s[0] for s in SCENARIOS}


def test_dependency_lock_is_exact():
    deps = load_dependencies()
    assert deps["formatcode"]["version"] == "1.5.0"
    assert deps["mhd"]["version"] == "4.2.4" and deps["ips"]["identifier"] == "hl7.fhir.uv.ips#2.0.1"
    assert deps["hl7-validator"]["version"] == "7.0.1" and "/releases/download/7.0.1/" in deps["hl7-validator"][
        "identifier"]
    assert deps["xds-mu"]["version"] == "Revision 1.14"
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "releases/latest" not in ci, "CI must not select validator versions at run time"


# ------------------------------------------------------------------ A-03: every applicable rule, and a verdict

def test_report_lists_every_applicable_rule_and_only_those():
    rep = _vector("V-01")
    cat = load_catalogue()
    expected = {oid for oid, o in cat["obligations"].items() if checker.applies(o, {"Envelope"})}
    assert {r.rule for r in rep.results} == expected
    assert rep.outcome("PROV-02.a") == rep.outcome("PROV-02.b") == "not-applicable", "option AI not claimed"
    assert all(r.level and r.ruleId for r in rep.results)
    d = rep.to_dict()
    assert d["ruleCatalogue"]["sha256"] == cat["sha256"] and d["claimed"] == {"classes": ["Envelope"], "options": [],
                                                                                "actorBindings": None}
    assert all(r.outcome in checker.OUTCOMES for r in rep.results)


def test_disabled_validators_withhold_conformance():
    """Review A-03 acceptance, part 1: run with veraPDF and the HL7 validator disabled."""
    rep = _vector("V-01", claim=_claim())
    assert rep.outcome("ENV-01") == "not-tested" and rep.outcome("SRC-01") == "not-tested"
    assert not rep.failed()
    assert rep.verdict == "incomplete", "no conformance verdict while mandatory rules are untested"


def test_every_mandatory_rule_evaluated_gives_conformant(validators_pass):
    """Review A-03 acceptance, part 2: with every mandatory applicable rule successfully evaluated."""
    rep = _vector("V-01", claim=_claim())
    untested = [r.rule for r in rep.results if r.outcome == "not-tested"]
    assert not untested and not rep.failed(), untested
    assert rep.verdict == "conformant"


def test_attested_vector_conformant_with_option_ai(validators_pass):
    insp = {"PROV-02.c": {"result": "pass", "inspector": "QA (synthetic)", "date": "2026-10-09",
                          "method": "review of the attestation action"}}
    rep = _vector("V-02", claim=_claim(options=["AI"], inspections=insp))
    assert rep.outcome("PROV-02.a") == rep.outcome("PROV-02.b") == rep.outcome("IPS-07") == "pass"
    assert rep.verdict == "conformant"


def test_attestation_changed_after_review_fails(validators_pass):
    rep = _vector("I-18", claim=_claim(options=["AI"]))
    assert rep.outcome("PROV-02.b") == "fail" and rep.outcome("IPS-07") == "fail" and rep.verdict == "non-conformant"


def test_unclaimed_option_never_fails_a_claim(validators_pass):
    """A-08: a publisher without on-demand support is not failed for it; OD rules are not applicable."""
    rep = check(None, classes=["Responder"], options=["MHD"], claim=_claim(classes=["Responder"], options=["MHD"]))
    od = [r for r in rep.results if load_catalogue()["obligations"][r.rule].get("option") == "OD"]
    assert od and all(r.outcome == "not-applicable" for r in od)


def test_claim_rules(validators_pass):
    assert _vector("V-01", claim=_claim(statement="EHDS compliant patient summary service")).outcome("CON-03") == "fail"
    deps = _claim()["dependencies"]
    # IMP-07: a deviation is never eligible for a conformant verdict, whatever reason is recorded.
    bad = _claim(dependencies={**deps, "mhd": {"version": "4.2.5-comment", "status": "unsupported-deviation"}},
                 exceptions=[{"key": "mhd", "reason": "ballot testing"}])
    rep = _vector("V-01", claim=bad)
    assert rep.outcome("CON-02.a") == "pass" and rep.outcome("CON-02.b") == "fail" and rep.verdict == "non-conformant"
    mislabelled = _claim(dependencies={**deps, "mhd": {"version": "4.2.5-comment", "status": "exact"}})
    assert _vector("V-01", claim=mislabelled).outcome("CON-02.a") == "fail"
    assert _vector("V-01", claim=_claim(dependencies={**deps, "formatcode": "1.6.0"})).outcome("CON-02.b") == "fail"
    pub = check(None, classes=["Publisher"], claim=_claim(
        classes=["Publisher"], transactions=["ITI-41"],
        actorBindings={"Publisher": ["XDS.b Document Source", "XDS Metadata Update Document Administrator"]}))
    assert pub.outcome("CON-05.b") == "fail", "ITI-57 is required by a declared binding of a Publisher"


def test_permitted_alternative_is_eligible(validators_pass, monkeypatch):
    deps = dict(load_dependencies())
    deps["__permitted__"] = [{"key": "mhd", "version": "4.2.3", "reason": "test"}]
    monkeypatch.setattr(checker, "load_dependencies", lambda: deps)
    claim = _claim(dependencies={**_claim()["dependencies"], "mhd": {"version": "4.2.3",
                                                                      "status": "permitted-alternative"}})
    rep = _vector("V-01", claim=claim)
    assert rep.outcome("CON-02.a") == rep.outcome("CON-02.b") == "pass"


def test_mhd_only_receiver_is_not_required_to_do_xca():
    """IMP-02 acceptance: a Receiver declaring only the MHD Document Consumer binding."""
    claim = _claim(classes=["Receiver"], actorBindings={"Receiver": ["MHD Document Consumer"]},
                   transactions=["ITI-67", "ITI-68"])
    rep = check(None, classes=["Receiver"], claim=claim)
    assert rep.outcome("CON-05.a") == "pass" and rep.method_outcome("CON-05.b", "claim") == "pass"
    none = check(None, classes=["Receiver"], claim=_claim(classes=["Receiver"], actorBindings={}))
    assert none.outcome("CON-05.a") == "fail", "a class offering alternatives needs at least one binding"
    mhd = check(None, classes=["Responder"], options=[], claim=_claim(
        classes=["Responder"], actorBindings={"Responder": ["MHD Document Responder"]},
        transactions=["ITI-67", "ITI-68"]))
    assert mhd.outcome("CON-05.a") == "fail", "the MHD binding and option MHD are declared together"


def test_mixed_level_rule_is_evaluated_per_obligation():
    """IMP-01 acceptance: a rule with a mandatory and an advisory obligation. The advisory failure is a warning and
    does not decide the verdict; the mandatory failure does."""
    ev = {"method": "live", "source": "test", "results": {"REN-04.a": {"outcome": "pass"},
                                                          "REN-04.b": {"outcome": "fail", "evidence": "bytes"}}}
    rep = check(None, classes=["Issuer"], evidence=[ev])
    assert rep.outcome("REN-04.a") == "pass" and rep.outcome("REN-04.b") == "fail"
    assert any(w.startswith("REN-04.b (SHOULD") for w in rep.warnings)
    assert not any(r.outcome == "fail" and r.mandatory for r in rep.results)
    assert rep.verdict == "incomplete"
    ev["results"]["REN-04.a"] = {"outcome": "fail"}
    assert check(None, classes=["Issuer"], evidence=[ev]).verdict == "non-conformant"


def test_undefined_references_are_rejected():
    """CHK-10."""
    with pytest.raises(checker.CatalogueError):
        check(None, classes=["Issuer"], evidence=[{"method": "live", "results": {"REN-04": {"outcome": "pass"}}}])
    with pytest.raises(checker.CatalogueError):
        check(None, classes=["Issuer"], evidence=[{"method": "live", "results": {"NOPE-1": {"outcome": "pass"}}}])
    with pytest.raises(checker.CatalogueError):
        check(None, classes=["Nobody"])
    from scdpoc.conformance_kit import validate_manifest
    with pytest.raises(checker.CatalogueError):
        validate_manifest({"vectors": {"I-01": {"mustFail": ["ENV-05"]}}}, load_catalogue())  # no justification
    validate_manifest(MANIFEST, load_catalogue())


def test_inspection_is_reported_as_declared(validators_pass):
    claim = _claim(classes=["Issuer"], inspections={"REN-06": {"result": "pass", "inspector": "QA (synthetic)",
                                                               "date": "2026-10-08", "method": "code review"}})
    rep = check(None, classes=["Issuer"], claim=claim)
    r = next(r for r in rep.results if r.rule == "REN-06")
    assert r.outcome == "pass" and "declared inspection" in r.evidence and "not verified" in r.evidence


# ------------------------------------------------------------------ CHK-04 and A-10: vectors

@pytest.mark.parametrize("vid", sorted(MANIFEST["vectors"]))
def test_chk04_vectors(vid):
    v = MANIFEST["vectors"][vid]
    rep = _vector(vid)
    fails = rep.failed()
    assert set(v["mustFail"]) <= fails, (vid, fails)
    if v["valid"]:
        assert not fails, (vid, fails)
        assert all(rep.outcome(r) == "pass" for r in v["mustPass"]), [(r, rep.outcome(r)) for r in v["mustPass"]]
    for opt, rules in (v.get("mustFailWithOptions") or {}).items():
        assert set(rules) <= _vector(vid, options=[*v["options"], opt]).failed()


def test_valid_vectors_pass_validator_rules_when_configured(validators_pass):
    for vid in ("V-01", "V-02"):
        rep = _vector(vid)
        assert all(rep.outcome(r) == "pass" for r in MANIFEST["vectors"][vid]["mustPassWithValidators"])


def test_vectors_are_reproducible(tmp_path):
    from scdpoc.vectors import build
    m = build(tmp_path)
    for vid, v in m["vectors"].items():
        assert v["envelopeSha256"] == MANIFEST["vectors"][vid]["envelopeSha256"], vid


def test_check_vectors_evidence():
    from scdpoc.conformance_kit import check_vectors
    out = check_vectors(VECTORS)
    assert all(r["outcome"] == "pass" for r in out["results"].values()), out["results"]
    assert {"CHK-01", "CHK-02", "CHK-03", "CHK-04", "CHK-05", "CHK-07", "CHK-08", "CHK-09", "CHK-10", "ENV-06.c",
            "SEC-04"} <= set(out["results"])
    assert any(s["id"] == "L-15" for s in out["scenarios"])


# ------------------------------------------------------------------ live scenarios

@pytest.fixture(scope="module")
def live():
    from scdpoc.conformance_kit import live_check
    return live_check()


def test_live_scenarios_run_and_cover_their_rules(live):
    ran = [s for s in live["scenarios"] if s["id"].startswith("L-")]
    assert len(ran) >= 14
    broken = [(s["id"], a["evidence"]) for s in ran for a in s["assertions"] if a["rule"] == "(scenario)"]
    assert not broken, broken
    cat = load_catalogue()
    for oid, o in cat["obligations"].items():
        if "live" in o["verification"] and o.get("option") not in ("OD", "PP"):
            assert oid in live["results"], f"{oid} has no live assertion"


def test_live_failures_are_the_known_gaps(live):
    """Without validators configured, the issuer gate is not complete: SRC-04 and ENV-08 fail honestly."""
    failed = {k for k, v in live["results"].items() if v["outcome"] == "fail"}
    assert failed == {"SRC-04", "ENV-08"}, failed


def test_full_report_for_the_demonstrator_claim(live):
    """The demonstrator's own claim, evaluated with artefact, claim, live and vector evidence. Its verdict is
    non-conformant, for documented reasons: no external validators gate issuance here (SRC-04, ENV-08) and
    terminology versions are not recorded (PRES-01), and the security declarations a real service needs are not
    made (SEC-01..SEC-03)."""
    from scdpoc.conformance_kit import check_vectors
    claim = json.loads((ROOT / "conformance" / "claims" / "demonstrator.json").read_text())
    rep = _vector("V-01", classes=claim["classes"], options=claim["options"], claim=claim,
                  evidence=[live, check_vectors(VECTORS)])
    assert rep.verdict == "non-conformant"
    assert {"SRC-04", "ENV-08", "PRES-01", "SEC-01.a", "SEC-02", "SEC-03"} <= rep.failed()
    assert rep.outcome("CON-02.a") == rep.outcome("CON-02.b") == rep.outcome("CON-05.a") == \
        rep.outcome("CON-05.b") == "pass"
    assert rep.outcome("MET-08") == "pass" and rep.outcome("PRES-06") == "pass"
    assert rep.outcome("PRES-05.a") == "not-applicable" and rep.outcome("PRES-05.b") == "pass"
    assert rep.outcome("XB-09") == rep.outcome("LIF-12") == rep.outcome("PROV-08.b") == "pass"


def test_checker_on_published_envelope(client, published):
    iss = published["publication"]["issuance"]
    pdf = client.get(f"/api/demo/package/{iss['packageId']}/envelope.pdf").content
    proj = client.get(mhd_doc(client, iss["ips"]["entryUUID"])["content"][0]["attachment"]["url"]).content
    rep = check(pdf, projection=proj, record=issuance_record_from_demo(iss), classes=["Envelope", "Issuer"])
    assert rep.failed() == {"SRC-04", "ENV-08", "PRES-01"}, [(r.rule, r.evidence) for r in rep.results
                                                              if r.outcome == "fail"]
    assert rep.method_outcome("REN-02") == "pass" and rep.method_outcome("PROV-04") == "pass"
    assert hashlib.sha256(pdf).hexdigest() == rep.tested["sha256"]
