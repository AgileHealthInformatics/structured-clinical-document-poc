"""Profile 0.3.0, review 2 item A-01: the complete clinical fidelity contract.

Acceptance test from the review: change a medication dose, frequency, route, allergy severity,
uncertainty indicator or temporal qualifier in the source and verify that the corresponding visible
content changes correctly; omit any mandatory clinical detail from a rendered page and confirm the
checker fails the relevant test.
"""
from __future__ import annotations

import copy
import re

import pytest

from scdpoc import fidelity
from scdpoc.ips.composer import compose_ips
from scdpoc.ips.view import build_view
from scdpoc.pdfa.extract import page_text
from scdpoc.render.pdf import render_pdf
from scdpoc.safety import load_fixture


def _pages(bundle: dict) -> str:
    return page_text(render_pdf(build_view(bundle), "x.json"))


def _res(bundle: dict, rt: str, pred=lambda r: True) -> dict:
    return next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == rt and pred(e["resource"]))


def _ramipril(r):
    return r["medicationCodeableConcept"]["coding"][0]["code"] == "C09AA05"


def _set_dose(b):
    _res(b, "MedicationStatement", _ramipril)["dosage"][0]["doseAndRate"][0]["doseQuantity"]["value"] = 10


def _set_frequency(b):
    _res(b, "MedicationStatement", _ramipril)["dosage"][0]["timing"]["repeat"]["frequency"] = 3


def _set_route(b):
    _res(b, "MedicationStatement", _ramipril)["dosage"][0]["route"] = {
        "coding": [{"system": "http://standardterms.edqm.eu", "code": "20045000", "display": "Intravenous use"}]}


def _set_allergy_severity(b):
    _res(b, "AllergyIntolerance")["reaction"][0]["severity"] = "severe"


def _set_uncertainty(b):
    _res(b, "Condition")["verificationStatus"]["coding"][0]["code"] = "unconfirmed"


def _set_onset(b):
    _res(b, "Condition")["onsetDateTime"] = "2009-02-14"


def _set_effective(b):
    _res(b, "MedicationStatement", _ramipril)["effectivePeriod"]["start"] = "2016-01-31"


MUTATIONS = {
    # name: (mutation, contract element, new canonical text, old canonical text)
    "dose": (_set_dose, "MedicationStatement.dosage.doseAndRate.doseQuantity", "dose 10 mg", "dose 5 mg"),
    "frequency": (_set_frequency, "MedicationStatement.dosage.timing.repeat.frequency", "3 times per day", "once per day"),
    "route": (_set_route, "MedicationStatement.dosage.route", "Intravenous use", "Oral use"),
    "allergy severity": (_set_allergy_severity, "AllergyIntolerance.reaction.severity", "severe", "moderate"),
    "uncertainty": (_set_uncertainty, "Condition.verificationStatus", "unconfirmed", "confirmed"),
    "onset": (_set_onset, "Condition.onset", "2009-02-14", None),
    "medication start": (_set_effective, "MedicationStatement.effective", "2016-01-31", "2015-06-10"),
}


@pytest.fixture(scope="module")
def amara(settings_module):
    return compose_ips(load_fixture(settings_module, "amara-okafor"), settings_module).bundle


@pytest.fixture(scope="module")
def settings_module(tmp_path_factory):
    from scdpoc.config import Settings
    return Settings(data_dir=tmp_path_factory.mktemp("data"))


@pytest.mark.parametrize("name", list(MUTATIONS))
def test_source_change_changes_visible_content(amara, name):
    mutate, element, new, old = MUTATIONS[name]
    original_pages = _pages(amara)
    changed = copy.deepcopy(amara)
    mutate(changed)
    pages = _pages(changed)

    # 1. The re-rendered page satisfies the contract for the changed source.
    res = fidelity.check(changed, pages)
    assert res.passed, res.to_dict()
    # 2. The changed value is now visible, in its canonical form.
    assert fidelity._has(pages.lower(), new), (name, new)
    if old is not None:
        # The old value is no longer visible as that qualifier of that entry.
        count = lambda t: len(fidelity._pattern(old).findall(t.lower()))  # noqa: E731
        assert count(pages) == count(original_pages) - 1, (name, old)
    # 3. A stale rendition (pages of the unchanged source) fails against the changed source, on that element.
    stale = fidelity.check(changed, original_pages)
    assert not stale.passed
    assert element in {m["element"] for m in stale.missing + stale.misplaced}, (name, stale.to_dict())


def test_boundary_matching_rejects_partial_tokens():
    assert not fidelity._has("dose 25 mg", "5 mg")
    assert not fidelity._has("active · unconfirmed", "confirmed")
    assert not fidelity._has("inactive", "active")
    assert fidelity._has("dose 5\nmg", "5 mg"), "line wrapping is tolerated"
    assert fidelity._has("status active · confirmed", "confirmed")


@pytest.mark.parametrize("key", ["amara-okafor", "lukas-brenner", "ines-duarte"])
def test_omitting_any_contract_detail_fails(settings_module, key):
    """For every populated contract element, a page that omits it fails with that element reported."""
    bundle = compose_ips(load_fixture(settings_module, key), settings_module).bundle
    pages = _pages(bundle)
    assert fidelity.check(bundle, pages).passed
    facts = fidelity.expected_facts(bundle)
    tested = 0
    for f in facts:
        if f.element == "section.title" or not f.text.strip():
            continue
        # Remove every boundary-respecting occurrence of the fact text (the page no longer shows it).
        pat = fidelity._pattern(f.text)
        low = pages.lower()
        spans = [m.span() for m in pat.finditer(low)]
        assert spans, (key, f)
        omitted = "".join(pages[a:b] if i % 2 == 0 else "" for i, (a, b) in
                          enumerate(_complement(spans, len(pages))))
        res = fidelity.check(bundle, omitted)
        assert not res.passed, (key, f)
        reported = {(m.get("scope"), m.get("element")) for m in res.missing + res.misplaced}
        assert (f.scope, f.element) in reported, (key, f, reported)
        tested += 1
    assert tested >= 20


def _complement(spans, n):
    """Interleave kept and removed segments: kept, removed, kept, removed, ..., kept."""
    out, cur = [], 0
    for a, b in spans:
        out += [(cur, a), (a, b)]
        cur = b
    out.append((cur, n))
    return out


def test_dosage_contract_covers_structured_elements(amara):
    els = {f.element for f in fidelity.expected_facts(amara) if f.element.startswith("MedicationStatement.dosage")}
    assert els >= {"MedicationStatement.dosage.text", "MedicationStatement.dosage.route",
                   "MedicationStatement.dosage.doseAndRate.doseQuantity", "MedicationStatement.dosage.timing.repeat.frequency"}


def test_medication_request_contract(settings_module):
    b = compose_ips(load_fixture(settings_module, "lukas-brenner"), settings_module).bundle
    els = {f.element for f in fidelity.expected_facts(b) if f.element.startswith("MedicationRequest")}
    assert {"MedicationRequest.intent", "MedicationRequest.dosageInstruction.text",
            "MedicationRequest.authoredOn"} <= els
    assert fidelity.check(b, _pages(b)).passed


def test_nested_and_narrative_only_sections(amara):
    """Recursive Composition sections: a nested section's entries are contract facts in their own scope, and a
    narrative-only section's narrative must be shown (REN-12)."""
    b = copy.deepcopy(amara)
    comp = b["entry"][0]["resource"]
    meds = next(s for s in comp["section"] if s["code"]["coding"][0]["code"] == "10160-0")
    ramipril_ref = meds["entry"][0]
    meds["entry"] = meds["entry"][1:]
    meds["section"] = [{"title": "Cardiovascular medicines",
                        "code": {"coding": [{"system": "http://loinc.org", "code": "10160-0"}]},
                        "text": {"status": "generated", "div": "<div xmlns=\"http://www.w3.org/1999/xhtml\">x</div>"},
                        "entry": [ramipril_ref]}]
    facts = fidelity.expected_facts(b)
    nested = [f for f in facts if f.scope == "10160-0/10160-0"]
    assert any(f.element == "MedicationStatement.dosage.timing.repeat.frequency" for f in nested)
    pages = _pages(b)
    assert fidelity.check(b, pages).passed
    # Dropping the nested entry's dosage from the pages is caught in the nested scope.
    damaged = re.sub(r"once per day", "", pages)
    res = fidelity.check(b, damaged)
    assert ("10160-0/10160-0", "MedicationStatement.dosage.timing.repeat.frequency") in {(m["scope"], m["element"])
                                                                          for m in res.missing + res.misplaced}

    plan = next(s for s in comp["section"] if s["code"]["coding"][0]["code"] == "18776-5")
    narrative = fidelity._plain(plan["text"]["div"])
    assert any(f.element == "section.text" and f.text == narrative for f in facts)
    res = fidelity.check(b, pages.replace(narrative.split()[0], "", 1))
    assert not res.passed


def test_result_states_limitation(amara):
    d = fidelity.check(amara, _pages(amara)).to_dict()
    assert "cannot establish complete clinical semantic equivalence" in d["limitation"]
    assert set(d["facets"]) == {"coverageAndAccuracy", "context", "unsupportedAssertions"}
