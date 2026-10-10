"""Tests for the corrections made in response to the review of profile 0.1.0 (profile 0.2.0)."""
from __future__ import annotations

from pathlib import Path

import pytest

from scdpoc import fidelity
from scdpoc.ips.composer import compose_ips
from scdpoc.ips.view import build_view
from scdpoc.pdfa.extract import page_text
from scdpoc.render.pdf import render_pdf
from scdpoc.safety import load_fixture
from scdpoc.xds.model import ASSOC_RPLC, ASSOC_XFRM
from tests.conftest import mhd_doc

ROOT = Path(__file__).resolve().parents[2]
SIPS_FORMAT = "http://hl7.org/fhir/uv/ips/StructureDefinition/Bundle-uv-ips"


# ---------------------------------------------------------------- binding (review F-01, F-02, F-04, F-05)

def _assocs(client, uuid_):
    return client.app.state.service.registry.store.associations_for(uuid_)


def test_ips_entry_uses_sips_format_code(client, published):
    iss = published["publication"]["issuance"]
    assert iss["ips"]["formatCode"] == SIPS_FORMAT
    de = client.app.state.service.registry.store.get(iss["ips"]["entryUUID"])
    assert de.codes["formatCode"].scheme == "urn:ietf:rfc:3986"


def test_xfrm_envelope_is_source_ips_is_target(client, published):
    iss = published["publication"]["issuance"]
    x = [a for a in _assocs(client, iss["envelope"]["entryUUID"]) if a.type == ASSOC_XFRM]
    assert len(x) == 1
    assert x[0].source == iss["envelope"]["entryUUID"] and x[0].target == iss["ips"]["entryUUID"]


def test_issuance_is_one_submission(client, published):
    pub = published["publication"]
    assert len(pub["exchanges"]) == 1
    assert len(pub["metadata"]["submissionSets"]) == 1


def test_failed_submission_registers_and_stores_nothing(client):
    """Atomicity: if the registry would reject any part, no entry is visible and no object is stored."""
    svc = client.app.state.service
    d = client.post("/api/demo/compose/lukas-brenner").json()
    client.post(f"/api/demo/package/{d['draftId']}").raise_for_status()
    before = len(svc.registry.store.all_entries())
    count_objects = lambda: svc.repository.objects.db.one("SELECT COUNT(*) FROM repository_object")[0]  # noqa: E731
    objects_before = count_objects()
    real = svc._entry

    def broken(**kw):
        de = real(**kw)
        if kw["rep"] == "envelope":           # make the second entry invalid
            de.codes["formatCode"] = svc.reps["ips"][1]
        return de

    svc._entry = broken
    try:
        r = client.post(f"/api/demo/publish/{d['draftId']}")
    finally:
        svc._entry = real
    assert r.status_code == 502 and "nothing was registered" in r.json()["detail"]
    assert len(svc.registry.store.all_entries()) == before
    assert count_objects() == objects_before, "no bytes stored for a rejected submission"


def test_replacement_chain_on_ips_and_envelope_deprecated(client, published):
    v1 = published["publication"]["issuance"]
    d2 = client.post("/api/demo/compose/amara-okafor?revise=true").json()
    client.post(f"/api/demo/package/{d2['draftId']}").raise_for_status()
    v2 = client.post(f"/api/demo/publish/{d2['draftId']}").json()["issuance"]
    rplc = [a for a in _assocs(client, v2["ips"]["entryUUID"]) if a.type == ASSOC_RPLC]
    assert len(rplc) == 1 and rplc[0].target == v1["ips"]["entryUUID"], "new IPS replaces old IPS"
    store = client.app.state.service.registry.store
    assert store.get(v1["envelope"]["entryUUID"]).status.endswith("Deprecated"), \
        "old envelope deprecated as a transformation of the replaced IPS (ITI TF-3)"
    assert v2["replacementReason"] == "update"
    mhd = mhd_doc(client, v2["ips"]["entryUUID"])
    old = mhd_doc(client, v1["ips"]["entryUUID"])
    assert {"code": "replaces", "target": {"reference": f"DocumentReference/{old['id']}"}} in mhd["relatesTo"]


# ---------------------------------------------------------------- fidelity (review F-03)

def test_fidelity_is_independent_of_renderer(settings):
    """Dropping a qualifier in the renderer must be caught: the check does not reuse the view model."""
    d = compose_ips(load_fixture(settings, "amara-okafor"), settings)
    view = build_view(d.bundle)
    meds = next(s for s in view.sections if s.code == "10160-0")
    meds.rows = [[*r[:3], "", r[4]] for r in meds.rows]          # renderer "forgets" dosage
    text = page_text(render_pdf(view, "x.json"))
    res = fidelity.check(d.bundle, text)
    assert not res.passed
    assert {m["element"] for m in res.missing} == {
        "MedicationStatement.dosage.text", "MedicationStatement.dosage.route",
        "MedicationStatement.dosage.doseAndRate.doseQuantity", "MedicationStatement.dosage.timing.repeat.frequency",
        "MedicationStatement.dosage.additionalInstruction", "MedicationStatement.dosage.patientInstruction",
        "MedicationStatement.dosage.maxDosePerPeriod"}


def test_fidelity_detects_fact_in_wrong_section(settings):
    d = compose_ips(load_fixture(settings, "ines-duarte"), settings)
    view = build_view(d.bundle)
    probs = next(s for s in view.sections if s.code == "11450-4")
    meds = next(s for s in view.sections if s.code == "10160-0")
    meds.rows.append([*probs.rows[0][:2], "", "", ""])             # a problem code shown as a medicine row
    res = fidelity.check(d.bundle, page_text(render_pdf(view, "x.json")))
    assert not res.passed


@pytest.mark.parametrize("key", ["amara-okafor", "lukas-brenner", "ines-duarte"])
def test_issuer_rendition_meets_contract(settings, key):
    d = compose_ips(load_fixture(settings, key), settings)
    res = fidelity.check(d.bundle, page_text(render_pdf(build_view(d.bundle), "x.json")))
    assert res.passed, res.to_dict()


def test_provenance_roles_distinct(client, published):
    prov = published["publication"]["issuance"]["provenance"]
    assert prov["author"] == ["SCD-PoC summary generator"], "content author is the generating software"
    assert prov["attester"] == [], "a preserved snapshot carries no attestation claim (PROV-04)"
    assert prov["custodian"] == "Synthetic Health Organisation"
    assert prov["renderer"].startswith("scdpoc-render-")
