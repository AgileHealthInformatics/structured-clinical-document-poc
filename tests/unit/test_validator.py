import copy

from scdpoc.ips.composer import compose_ips
from scdpoc.ips.validator import BuiltinIpsPreflight, validate_ips
from scdpoc.safety import load_fixture


def bundle(settings, key="amara-okafor"):
    return compose_ips(load_fixture(settings, key), settings).bundle


def rules(settings, b):
    return {i.rule for i in BuiltinIpsPreflight(settings).run(b).issues if i.severity == "error"}


def test_composition_must_be_first(settings):
    b = bundle(settings)
    b["entry"].append(b["entry"].pop(0))
    assert "DOC-3" in rules(settings, b)


def test_missing_required_section(settings):
    b = bundle(settings)
    comp = b["entry"][0]["resource"]
    comp["section"] = [s for s in comp["section"] if s["code"]["coding"][0]["code"] != "48765-2"]
    assert "IPS-S0" in rules(settings, b)


def test_empty_reason_with_entries_rejected(settings):
    b = bundle(settings)
    sec = b["entry"][0]["resource"]["section"][0]
    sec["emptyReason"] = {"coding": [{"code": "nilknown"}]}
    assert "cmp-2" in rules(settings, b)


def test_empty_required_section_needs_empty_reason(settings):
    src = load_fixture(settings, "lukas-brenner")
    src["medications"] = []
    b = compose_ips(src, settings).bundle
    med = next(s for s in b["entry"][0]["resource"]["section"] if s["code"]["coding"][0]["code"] == "10160-0")
    assert med["emptyReason"]["coding"][0]["code"] == "nilknown"
    assert not rules(settings, b)
    del med["emptyReason"]
    assert "ips-comp-1" in rules(settings, b)


def test_unresolved_reference(settings):
    b = bundle(settings)
    b["entry"][0]["resource"]["section"][0]["entry"].append({"reference": "urn:uuid:does-not-exist"})
    assert "DOC-5" in rules(settings, b)


def test_profile_required_element(settings):
    b = bundle(settings)
    patient = next(e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "Patient")
    del patient["birthDate"]
    assert "PROF-1" in rules(settings, b)


def test_choice_type_required(settings):
    b = bundle(settings)
    ms = next(e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "MedicationStatement")
    del ms["effectivePeriod"]
    assert "PROF-1" in rules(settings, b)


def test_narrative_script_rejected(settings):
    b = bundle(settings)
    sec = b["entry"][0]["resource"]["section"][0]
    sec["text"]["div"] = '<div xmlns="http://www.w3.org/1999/xhtml"><script>x()</script></div>'
    assert "txt-1" in rules(settings, b)


def test_non_synthetic_patient_rejected(settings):
    b = bundle(settings)
    patient = next(e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "Patient")
    patient["identifier"][0]["system"] = "https://fhir.nhs.uk/Id/nhs-number"
    assert "SAFE-1" in rules(settings, b)


def test_gate_blocks_when_hl7_validator_required(settings, monkeypatch):
    settings.require_hl7_validator = True
    d = compose_ips(load_fixture(settings, "amara-okafor"), settings)
    rep = validate_ips(copy.deepcopy(d.bundle), d.json_bytes, settings)
    assert rep.publishable is False and "required" in rep.gate
