"""Review 3 (profile 0.4.0) acceptance tests that are not covered by the Checker and live-scenario tests.

IMP-03  coverage matrix: altered values and swapped associations are detected for every resource type, and an
        element with disposition N is reported as not covered by automated verification.
IMP-05  time and author mappings at the MHD layer.
IMP-06  attested content digest: adding the attestation does not change it; changing content does.
IMP-04  receiver presentation of a summary that is no longer current.
"""
from __future__ import annotations

import copy
import json

import pytest

from scdpoc import fidelity
from scdpoc.config import Settings
from scdpoc.ips.attestation import add_attestation, attested_content_digest
from scdpoc.ips.composer import compose_ips
from scdpoc.ips.view import build_view
from scdpoc.pdfa.extract import page_text
from scdpoc.render.pdf import render_pdf
from scdpoc.safety import load_fixture
from tests.conftest import mhd_doc

FIXTURES = ["amara-okafor", "lukas-brenner", "ines-duarte"]


@pytest.fixture(scope="module")
def settings_module(tmp_path_factory):
    return Settings(data_dir=tmp_path_factory.mktemp("data"))


@pytest.fixture(scope="module")
def bundles(settings_module):
    return {k: compose_ips(load_fixture(settings_module, k), settings_module).bundle for k in FIXTURES}


def _render(view) -> str:
    return page_text(render_pdf(view, "x.json"))


# ------------------------------------------------------------------ IMP-03: generic mutations

def _swappable(bundle):
    """(section code, column, row i, row j) where two entries of one section show different values in a column."""
    view = build_view(bundle)
    out = []
    for sec in view.sections:
        for col in range(1, len(sec.columns)):
            rows = [r for r in sec.rows if r[col].strip()]
            for i in range(len(rows)):
                for j in range(i + 1, len(rows)):
                    if rows[i][col] != rows[j][col] and rows[i][col] not in rows[j][col] and \
                            rows[j][col] not in rows[i][col]:
                        out.append((sec.code, col, sec.rows.index(rows[i]), sec.rows.index(rows[j])))
                        break
                else:
                    continue
                break
    return out


@pytest.mark.parametrize("key", FIXTURES)
def test_swapped_associations_are_detected(bundles, key):
    """Values shown against the wrong entry (two entries' cells exchanged) fail the contract for that element."""
    bundle = bundles[key]
    cases = _swappable(bundle)
    assert cases, key
    sections = set()
    for code, col, i, j in cases:
        view = build_view(bundle)
        sec = next(s for s in view.sections if s.code == code)
        sec.rows[i][col], sec.rows[j][col] = sec.rows[j][col], sec.rows[i][col]
        res = fidelity.check(bundle, _render(view))
        assert not res.passed, (key, code, sec.columns[col])
        assert res.missing or res.misplaced
        sections.add(code)
    assert sections


@pytest.mark.parametrize("key", FIXTURES)
def test_altered_values_are_detected_for_every_resource_type(bundles, key):
    """For every contract fact of every entry type, a page showing an altered value fails, naming the element."""
    bundle = bundles[key]
    pages = _render(build_view(bundle))
    types = set()
    for f in fidelity.expected_facts(bundle):
        if f.scope == "document" or f.element in ("section.title", "section.text") or not f.text.strip():
            continue
        pat = fidelity._pattern(f.text)
        altered = pat.sub(lambda m: "≠" + m.group(0)[::-1] + "≠", pages.lower())
        res = fidelity.check(bundle, altered)
        reported = {(m.get("scope"), m.get("element")) for m in res.missing + res.misplaced}
        assert (f.scope, f.element) in reported, (key, f)
        types.add(f.element.split(".")[0])
    assert len(types) >= 4, types


def test_disposition_n_element_is_reported_unverified(bundles):
    b = copy.deepcopy(bundles["amara-okafor"])
    assert fidelity.unverified_elements(b) == []
    cond = next(e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "Condition")
    cond["stage"] = [{"summary": {"text": "Stage 1"}}]
    assert [(u["resource"], u["element"]) for u in fidelity.unverified_elements(b)] == [("Condition", "stage")]


def test_coverage_matrix_elements_are_rendered(bundles):
    """Elements added for the coverage matrix (IMP-03) appear in canonical form on the pages."""
    assert fidelity.check(bundles["amara-okafor"], _render(build_view(bundles["amara-okafor"]))).passed
    els = {f.element for f in fidelity.expected_facts(bundles["amara-okafor"])}
    assert {"MedicationStatement.dosage.maxDosePerPeriod", "MedicationStatement.dosage.patientInstruction",
            "Observation.referenceRange", "Immunization.protocolApplied.doseNumber"} <= els


# ------------------------------------------------------------------ IMP-06: attested content digest

def test_attestation_does_not_change_the_attested_content_digest(bundles):
    import uuid
    from datetime import UTC, datetime
    draft = bundles["amara-okafor"]
    digest = attested_content_digest(draft)
    t = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)
    for kind in ("person", "organisation"):
        final, raw = add_attestation(draft, {"name": "Dr Sam Synthetic", "id": "SYN-PRAC-1", "time": t,
                                             "kind": kind}, uuid.uuid4(), t)
        assert attested_content_digest(json.loads(raw)) == digest, kind
        assert final["entry"][0]["resource"]["attester"][0]["mode"] == ("legal" if kind == "person" else "official")
    changed = copy.deepcopy(draft)
    changed["entry"][0]["resource"]["section"][0]["title"] += " (edited)"
    assert attested_content_digest(changed) != digest


# ------------------------------------------------------------------ IMP-05 and IMP-04 through the API

def test_mhd_times_and_software_author(client, published):
    iss = published["publication"]["issuance"]
    dr = mhd_doc(client, iss["ips"]["entryUUID"])
    comp = client.get(f"/api/demo/drafts/{iss['packageId']}/ips.json").json()
    assert dr["content"][0]["attachment"]["creation"][:19] == comp["entry"][0]["resource"]["date"][:19]
    from scdpoc.conformance_kit import _same_instant
    assert _same_instant(dr["date"], iss["submissionTime"])
    assert _same_instant(iss["contentTime"], dr["content"][0]["attachment"]["creation"])
    assert "summary generator" in dr["author"][0]["display"]
    assert {e["url"]: e["valueCode"] for e in dr["extension"]}[
        "urn:oid:2.25.11064312502901710892401295388462879468.1"] == "issued"


def test_receiver_shows_withdrawn_status(client):
    client.post("/api/demo/compose/ines-duarte")
    d = client.post("/api/demo/compose/ines-duarte").json()
    client.post(f"/api/demo/package/{d['draftId']}")
    client.post(f"/api/demo/publish/{d['draftId']}")
    client.post("/api/demo/xb/discover/b-duarte")
    assert client.post("/api/demo/xb/exchange/b-duarte").json()["documents"]
    first = client.get("/api/demo/xb/render/b-duarte").json()
    assert first["homeStatus"]["status"] == "current"
    assert client.post("/api/demo/withdraw/ines-duarte").status_code == 200
    r = client.get("/api/demo/xb/render/b-duarte").json()
    assert r["homeStatus"]["status"] == "withdrawn"
    assert "zurückgezogen" in r["html"].lower() or "withdrawn" in r["html"].lower()
