"""Shared fixtures: an isolated app instance whose demo orchestration calls the
real SOAP/FHIR endpoints through an in-process HTTP transport."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scdpoc.app import create_app
from scdpoc.config import Settings
from scdpoc.demo.http import TestClientTransport

BASE = "http://testserver"


@pytest.fixture()
def settings(tmp_path, monkeypatch) -> Settings:
    for var in ("SCDPOC_VERAPDF_CLI", "SCDPOC_VERAPDF_URL", "SCDPOC_HL7_VALIDATOR_JAR",
                "SCDPOC_REQUIRE_VERAPDF", "SCDPOC_REQUIRE_HL7_VALIDATOR"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(data_dir=tmp_path / "var")
    s.base_url = BASE
    return s


@pytest.fixture()
def client(settings) -> TestClient:
    transport = TestClientTransport(None, BASE)
    app = create_app(settings, http=transport)
    c = TestClient(app, base_url=BASE)
    transport.client = c
    return c


@pytest.fixture()
def published(client):
    """Compose, package and publish version 1 for amara-okafor."""
    d = client.post("/api/demo/compose/amara-okafor").json()
    client.post(f"/api/demo/package/{d['draftId']}").raise_for_status()
    p = client.post(f"/api/demo/publish/{d['draftId']}")
    p.raise_for_status()
    return {"draft": d, "publication": p.json()}


def mhd_doc(client, entry_uuid: str) -> dict:
    """ITI-67 search by the entryUUID identifier slice (the resource id is server-assigned)."""
    b = client.get("/fhir/DocumentReference", params={"identifier": f"urn:ietf:rfc:3986|{entry_uuid}"}).json()
    assert b["total"] == 1, b
    return b["entry"][0]["resource"]
