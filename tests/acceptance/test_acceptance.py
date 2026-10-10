"""Acceptance tests AT-01 .. AT-12 from the reference design (section 9).

Tests that need external validators (HL7 FHIR validator, veraPDF) are marked
and skip unless configured; CI configures both.
"""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scdpoc.ehds.adapter import MyHealthEuAdapter, ReadinessPreviewAdapter
from scdpoc.ips.composer import compose_ips
from scdpoc.ips.validator import BuiltinIpsPreflight, Hl7FhirValidator
from scdpoc.ips.view import build_view
from scdpoc.pdfa.extract import extract_ips
from scdpoc.pdfa.packager import attachment_name_for, build_envelope
from scdpoc.pdfa.preflight import preflight
from scdpoc.pdfa.verapdf import VeraPdf
from scdpoc.render.pdf import render_pdf
from scdpoc.safety import list_fixtures, load_fixture, scan_for_real_identifiers

ROOT = Path(__file__).resolve().parents[2]
ISSUED = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
KEYS = ["amara-okafor", "lukas-brenner", "ines-duarte"]


def build(settings, key):
    doc_id = uuid.uuid5(uuid.NAMESPACE_URL, f"test:{key}")
    d = compose_ips(load_fixture(settings, key), settings, document_id=doc_id, issued=ISSUED)
    view = build_view(d.bundle)
    pages = render_pdf(view, attachment_name_for(str(doc_id)))
    env = build_envelope(pages, d.json_bytes, document_id=str(doc_id), issued=ISSUED, title=view.title,
                         author=view.custodian, subject="test")
    return d, pages, env


@pytest.mark.parametrize("key", KEYS)
def test_at01_ips_structural_validation_preflight(settings, key):
    d, _, _ = build(settings, key)
    res = BuiltinIpsPreflight(settings).run(d.bundle)
    assert res.errors == 0, [i.__dict__ for i in res.issues]


@pytest.mark.hl7validator
@pytest.mark.parametrize("key", KEYS)
def test_at01_ips_hl7_validator(settings, key):
    v = Hl7FhirValidator(settings)
    if not v.available():
        pytest.skip("HL7 FHIR validator not configured (SCDPOC_HL7_VALIDATOR_JAR)")
    d, _, _ = build(settings, key)
    res = v.run(d.json_bytes)
    assert res.status == "passed", [i.__dict__ for i in res.issues if i.severity in ("error", "fatal")]


@pytest.mark.parametrize("key", KEYS)
def test_at02_fhir_document_semantics(settings, key):
    d, _, _ = build(settings, key)
    b = d.bundle
    assert b["type"] == "document"
    assert b["identifier"]["value"] == f"urn:uuid:{d.document_id}"
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", b["timestamp"])
    comp = b["entry"][0]["resource"]
    assert comp["resourceType"] == "Composition"
    assert comp["type"]["coding"][0]["code"] == "60591-5"
    assert {s["code"]["coding"][0]["code"] for s in comp["section"]} >= {"11450-4", "48765-2", "10160-0"}


@pytest.mark.parametrize("key", KEYS)
def test_at03_deterministic_rendering(settings, key):
    d1, pages1, env1 = build(settings, key)
    d2, pages2, env2 = build(settings, key)
    assert d1.json_bytes == d2.json_bytes
    assert pages1 == pages2, "same IPS + renderer version must give byte-identical pages"
    assert env1.pdf_bytes == env2.pdf_bytes, "envelope generation must be reproducible"


@pytest.mark.parametrize("key", KEYS)
def test_at04_embedded_payload_identity(settings, key):
    d, _, env = build(settings, key)
    f = extract_ips(env.pdf_bytes)
    assert f.data == d.json_bytes
    assert f.relationship == "Source" and f.mime_type == "application/fhir+json"
    assert f.listed_in_af and f.listed_in_name_tree


@pytest.mark.parametrize("key", KEYS)
def test_at05_pdfa_preflight(settings, key):
    _, _, env = build(settings, key)
    res = preflight(env.pdf_bytes)
    assert res.passed, [c.__dict__ for c in res.checks if not c.passed]


@pytest.mark.verapdf
@pytest.mark.parametrize("key", KEYS)
def test_at05_pdfa_verapdf(settings, key):
    v = VeraPdf(settings)
    if not v.configured():
        pytest.skip("veraPDF not configured (SCDPOC_VERAPDF_CLI / SCDPOC_VERAPDF_URL)")
    _, _, env = build(settings, key)
    res = v.validate(env.pdf_bytes)
    assert res.status == "passed", res.failed_rules


def test_at06_xds_publish_query_retrieve(client, published):
    iss = published["publication"]["issuance"]
    assert all(x["status"].endswith("Success") for x in published["publication"]["exchanges"])
    disc = client.get("/api/demo/discover/amara-okafor").json()
    uids = {r["uniqueId"] for r in disc["xds"]["results"]}
    assert {iss["envelope"]["uniqueId"], iss["ips"]["uniqueId"]} <= uids
    r = client.get("/api/demo/retrieve/amara-okafor").json()
    assert r["xds"]["byteIdenticalToPackage"] is True
    assert r["xds"]["mimeType"] == "application/pdf"


def test_at07_mhd_discovery_matches_xds(client, published):
    disc = client.get("/api/demo/discover/amara-okafor").json()
    assert disc["equivalent"] is True
    mhd = {r["entryUUID"]: r for r in disc["mhd"]["results"]}
    for x in disc["xds"]["results"]:
        m = mhd[x["entryUUID"]]
        assert m["id"] != x["entryUUID"].removeprefix("urn:uuid:"), "resource id is server-assigned (MET-08)"
        assert m["masterIdentifier"] == f"urn:oid:{x['uniqueId']}"
        assert m["contentType"] == x["mimeType"] and m["format"] == x["formatCode"]


def test_at08_sips_direct_retrieval(client, published):
    iss = published["publication"]["issuance"]
    q = client.get("/fhir/DocumentReference", params={
        "patient.identifier": "urn:oid:2.999.1.1|SYN-000101",
        "format": "urn:ietf:rfc:3986|http://hl7.org/fhir/uv/ips/StructureDefinition/Bundle-uv-ips"})
    entries = q.json()["entry"]
    assert len(entries) == 1
    att = entries[0]["resource"]["content"][0]["attachment"]
    assert att["contentType"] == "application/fhir+json"
    rid = entries[0]["resource"]["id"]
    r = client.get(f"/fhir/Binary/{rid}", headers={"Accept": "application/fhir+json"})
    assert r.headers["content-type"].startswith("application/fhir+json")
    bundle = r.json()
    assert bundle["resourceType"] == "Bundle" and bundle["type"] == "document"
    env = client.get(f"/api/demo/package/{iss['packageId']}/envelope.pdf").content
    assert extract_ips(env).data == r.content, "projection must equal the embedded Associated File"


def test_at09_version_replacement(client, published):
    v1 = published["publication"]["issuance"]
    d2 = client.post("/api/demo/compose/amara-okafor?revise=true").json()
    assert d2["version"] == 2 and d2["replaces"] == v1["documentUrn"]
    client.post(f"/api/demo/package/{d2['draftId']}").raise_for_status()
    client.post(f"/api/demo/publish/{d2['draftId']}").raise_for_status()
    hist = {i["version"]: i for i in client.get("/api/demo/history/amara-okafor").json()["issuances"]}
    assert hist[2]["envelopeStatus"] == "Approved" and hist[2]["ipsStatus"] == "Approved"
    assert hist[1]["envelopeStatus"] == "Deprecated" and hist[1]["ipsStatus"] == "Deprecated"
    old = client.get("/api/demo/retrieve/amara-okafor", params={"version": 1}).json()
    assert old["integrity"]["passed"] is True, "superseded snapshot stays retrievable and intact"
    current = client.get("/fhir/DocumentReference", params={"patient.identifier": "urn:oid:2.999.1.1|SYN-000101"})
    assert {e["resource"]["status"] for e in current.json()["entry"]} == {"current"}
    assert len(current.json()["entry"]) == 2
    # issued artefacts cannot be re-published
    assert client.post(f"/api/demo/publish/{d2['draftId']}").status_code == 409


@pytest.mark.parametrize("mode", ["embedded", "outer"])
def test_at10_tamper_detection(client, published, mode):
    t = client.post(f"/api/demo/tamper/amara-okafor?mode={mode}").json()
    assert t["tampered"]["passed"] is False
    assert t["storedOriginal"]["passed"] is True
    failed = {c["id"] for c in t["tampered"]["checks"] if not c["passed"]}
    if mode == "embedded":
        assert {"IC-4", "IC-5", "IC-7"} <= failed, "payload divergence from pages must be detected"
    else:
        assert {"IC-1", "IC-2"} <= failed


def test_at11_ehds_boundary_honesty(client, published, settings):
    e = client.get("/api/demo/ehds-preview/amara-okafor").json()
    assert e["normative"] is False and "not an EHDS" in e["disclaimer"]
    statuses = {i["id"]: i["status"] for i in e["items"]}
    assert statuses["FMT-01"] == "unresolved"
    assert statuses["XB-01"] == "out-of-scope"
    assert "demonstrated" in statuses.values()
    assert not any("conformant" in i["status"] for i in e["items"])
    assert "not extracted from the PDF" in e["source"]
    with pytest.raises(NotImplementedError):
        MyHealthEuAdapter().export(b"{}")
    # every register item is reported - nothing silently dropped
    assert len(e["items"]) == len(ReadinessPreviewAdapter(settings).register["items"])


def test_at12_clean_public_clone():
    for f in ["LICENSE", "NOTICE.md", "SECURITY.md", "README.md", "docker-compose.yml", "Dockerfile"]:
        assert (ROOT / f).is_file(), f"{f} missing"
    assert "Apache License" in (ROOT / "LICENSE").read_text()
    try:
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                                 check=True).stdout.split()
    except Exception:
        tracked = []
    files = [ROOT / t for t in tracked] or [p for p in ROOT.rglob("*") if p.is_file()
                                            and not any(x in p.parts for x in (".git", "var", "build", ".venv"))]
    for p in files:
        assert not p.name.startswith(".env"), f"environment file committed: {p}"
        assert p.suffix not in {".pem", ".key", ".p12", ".jks"}, f"key material committed: {p}"
    secret = re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"]{8,}")
    for p in files:
        if p.suffix in {".py", ".yml", ".yaml", ".json", ".md", ".js", ".toml", ".html"} and p.is_file():
            assert not secret.search(p.read_text(encoding="utf-8", errors="ignore")), f"possible secret in {p}"
    for fx in (ROOT / "fixtures" / "synthetic-patients").glob("*.json"):
        assert not scan_for_real_identifiers(fx.read_text()), fx
        assert json.loads(fx.read_text())["synthetic"] is True


def test_all_fixtures_listed(settings):
    assert {f["key"] for f in list_fixtures(settings)} == set(KEYS)


def test_at03_ips_regression_digests(settings, tmp_path):
    """IPS bytes for fixed inputs must not drift between releases without review."""
    import hashlib
    import subprocess
    import sys
    expected = json.loads((ROOT / "fixtures" / "expected" / "digests.json").read_text())["fixtures"]
    subprocess.run([sys.executable, "-m", "scdpoc.cli", "build-fixtures", "--out", str(tmp_path)], check=True,
                   capture_output=True, cwd=ROOT)
    for key, d in expected.items():
        assert hashlib.sha256((tmp_path / f"{key}.ips.json").read_bytes()).hexdigest() == d["ips_sha256"], key
