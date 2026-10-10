"""v0.2 cross-border acceptance tests (XB-AT-01 .. XB-AT-09).

Jurisdiction B talks to Jurisdiction A only through A's responding gateway
over HTTP (in-process transport in tests, real HTTP at runtime)."""
from __future__ import annotations

import json

import pytest

from scdpoc.crossborder import xcpd
from scdpoc.crossborder.translate import localise
from scdpoc.ehds.adapter import SimulatedNcpAdapter
from scdpoc.pdfa.extract import extract_ips
from scdpoc.pdfa.preflight import preflight

HCID_A = "urn:oid:2.999.1.9"


def _b(client):
    return client.app.state.jurisdiction_b


def test_xb_at01_xcpd_exact_match(client):
    r = client.post("/api/demo/xb/discover/b-okafor").json()
    assert r["result"]["queryResponseCode"] == "OK"
    assert r["result"]["patient"] == {"root": "2.999.1.1", "extension": "SYN-000101"}
    assert r["link"]["localId"] == "SYNB-2001" and r["link"]["homeCommunityId"] == HCID_A


def test_xb_at02_xcpd_no_match_discloses_nothing(client):
    r = client.post("/api/demo/xb/discover/b-keller").json()
    assert r["result"]["queryResponseCode"] == "NF" and r["result"]["patient"] is None
    assert "SYN-0001" not in r["exchange"]["response_xml"]
    assert client.post("/api/demo/xb/exchange/b-keller").status_code == 409


def test_xb_at02b_xcpd_requires_all_demographics_to_agree(client):
    d = xcpd.Demographics(family="Okafor", given=["Amara"], birth_date="1971-03-15", gender="female")
    result, _ = _b(client).gw.discover(d)
    assert result["queryResponseCode"] == "NF", "a different birth date must not match"


def test_xb_at03_gateway_releases_only_current_ips(client, published):
    client.post("/api/demo/xb/discover/b-okafor")
    r = client.post("/api/demo/xb/exchange/b-okafor").json()
    assert r["documents"], "IPS should be discoverable across the gateway"
    assert {d["mimeType"] for d in r["documents"]} == {"application/fhir+json"}
    assert {d["formatCode"] for d in r["documents"]} == {"http://hl7.org/fhir/uv/ips/StructureDefinition/Bundle-uv-ips"}
    assert {d["home"] for d in r["documents"]} == {HCID_A}


def test_xb_at04_envelope_never_crosses_the_border(client, published):
    env = published["publication"]["issuance"]["envelope"]
    data, _, ex = _b(client).gw.retrieve(HCID_A, env["repositoryUniqueId"], env["uniqueId"])
    assert data is None
    assert ex.errors[0]["code"] == "XDSDocumentUniqueIdError"
    assert "stay in the home community" in ex.errors[0]["context"]


def test_xb_at05_received_bytes_identical_and_verified(client, published):
    iss = published["publication"]["issuance"]
    client.post("/api/demo/xb/discover/b-okafor")
    r = client.post("/api/demo/xb/exchange/b-okafor").json()
    assert all(c["passed"] for c in r["verification"]), r["verification"]
    assert r["received"]["sha256"] == iss["ips"]["sha256"]
    envelope = client.get(f"/api/demo/package/{iss['packageId']}/envelope.pdf").content
    assert extract_ips(envelope).sha256 == r["received"]["sha256"], \
        "what B receives is byte-identical to the Associated File inside A's preserved envelope"


def test_xb_at06_superseded_versions_stay_home(client, published):
    d2 = client.post("/api/demo/compose/amara-okafor?revise=true").json()
    client.post(f"/api/demo/package/{d2['draftId']}").raise_for_status()
    client.post(f"/api/demo/publish/{d2['draftId']}").raise_for_status()
    client.post("/api/demo/xb/discover/b-okafor")
    r = client.post("/api/demo/xb/exchange/b-okafor").json()
    assert len(r["documents"]) == 1 and r["documents"][0]["status"] == "Approved"
    v1_ips = published["publication"]["issuance"]["ips"]
    data, _, ex = _b(client).gw.retrieve(HCID_A, v1_ips["repositoryUniqueId"], v1_ips["uniqueId"])
    assert data is None and ex.errors[0]["code"] == "XDSDocumentUniqueIdError"


def test_xb_at07_local_rendition_honest_about_translation(client, published):
    client.post("/api/demo/xb/discover/b-okafor")
    client.post("/api/demo/xb/exchange/b-okafor")
    r = client.get("/api/demo/xb/render/b-okafor").json()
    assert r["language"] == "de-DE"
    t = r["translation"]
    assert [u["code"] for u in t["untranslated"]] == ["14682-9"]
    assert "nicht übersetzt" in r["html"], "untranslated codes must be flagged, not hidden"
    assert "Hypertonie" in r["html"] and 'lang="de-DE"' in r["html"]
    assert t["free_text_passed_through"], "free-text dosage is passed through and reported"


def test_xb_at08_custody_copy(client, published):
    client.post("/api/demo/xb/discover/b-okafor")
    ex = client.post("/api/demo/xb/exchange/b-okafor").json()
    p = client.post("/api/demo/xb/preserve/b-okafor").json()
    assert all(c["passed"] for c in p["checks"]), p["checks"]
    pdf = client.get("/api/demo/xb/preserve/b-okafor/custody.pdf").content
    assert preflight(pdf).passed
    assert extract_ips(pdf).sha256 == ex["received"]["sha256"]
    assert client.post("/api/demo/xb/preserve/b-okafor").json()["envelopeSha256"] == p["envelopeSha256"], \
        "custody copy is write-once"


def test_xb_at09_simulated_ncp_and_register(client, published, settings):
    ips = published["draft"]["bundle"].encode()
    res = SimulatedNcpAdapter(settings).export(ips).to_dict()
    assert res["normative"] is False and "Not MyHealth@EU" in res["disclaimer"]
    assert res["summary"].get("gap", 0) == 0
    e = client.get("/api/demo/ehds-preview/amara-okafor").json()
    statuses = {i["id"]: i["status"] for i in e["items"]}
    assert statuses["XB-02"] == "simulated" and statuses["XB-03"] == "simulated"
    assert statuses["XB-01"] == "out-of-scope", "real NCPeH connectivity is still not claimed"


def test_catalogue_gap_is_reported(settings, published):
    bundle = json.loads(published["draft"]["bundle"])
    for e in bundle["entry"]:
        if e["resource"]["resourceType"] == "Condition":
            e["resource"]["code"]["coding"][0]["code"] = "999999999"
            break
    res = SimulatedNcpAdapter(settings).export(json.dumps(bundle).encode()).to_dict()
    assert res["summary"]["gap"] == 1


@pytest.mark.parametrize("key", ["amara-okafor", "lukas-brenner", "ines-duarte"])
def test_localise_never_mutates_received_bundle(settings, key):
    from scdpoc.ips.composer import compose_ips
    from scdpoc.safety import load_fixture
    d = compose_ips(load_fixture(settings, key), settings)
    before = json.dumps(d.bundle, sort_keys=True)
    localise(d.bundle, settings.designations)
    assert json.dumps(d.bundle, sort_keys=True) == before


def test_xcpd_roundtrip():
    d = xcpd.Demographics("Brenner", ["Lukas"], "1994-09-02", "male", "2.999.2.1", "SYNB-2002")
    req = xcpd.build_request(d, sender_oid="2.999.2.10", receiver_oid="2.999.1.10")
    parsed, _ = xcpd.parse_request(req)
    assert parsed == d
    resp = xcpd.build_response(req, {"root": "2.999.1.1", "extension": "SYN-000102", "family": "Brenner",
                                     "given": ["Lukas"], "gender": "male", "birthDate": "1994-09-02"},
                               sender_oid="2.999.1.10", receiver_oid="2.999.2.10",
                               home_community_id="urn:oid:2.999.1.9")
    out = xcpd.parse_response(resp)
    assert out["queryResponseCode"] == "OK" and out["patient"]["extension"] == "SYN-000102"


def test_gateway_rejects_wrong_action(client):
    from lxml import etree

    from scdpoc.xds.model import ITI18
    from scdpoc.xds.soap import envelope, to_bytes
    body = to_bytes(envelope(ITI18, etree.Element("x")))
    r = client.post("/gateway/xca", content=body, headers={"Content-Type": "application/soap+xml"})
    assert r.status_code == 400 and b"Unsupported action" in r.content
