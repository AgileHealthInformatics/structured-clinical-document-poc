import uuid

import pytest
from lxml import etree

from scdpoc.xds.actors import parse_list
from scdpoc.xds.model import ITI41, NS, Code
from scdpoc.xds.soap import build_mtom, envelope, parse_message, xop_include


def test_mtom_roundtrip_is_binary_safe():
    payload = bytes(range(256)) * 50 + b"\r\n--not-a-boundary\r\n\r\n"
    body = etree.Element("{urn:ihe:iti:xds-b:2007}Document", nsmap={"xds": NS["xds"]})
    xop_include(body, "doc0@test")
    data, ctype = build_mtom(envelope(ITI41, body), ITI41, {"doc0@test": (payload, "application/pdf")})
    msg = parse_message(data, ctype)
    assert msg.action == ITI41
    assert msg.binary(msg.body) == payload


def test_parser_refuses_external_entities():
    evil = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
            b'<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body><a>&e;</a>'
            b'</soap:Body></soap:Envelope>')
    msg = parse_message(evil, "application/soap+xml")
    assert "root:" not in etree.tostring(msg.body).decode()


def test_parse_list():
    assert parse_list(["('a','b')"]) == ["a", "b"]
    assert parse_list(["'x^^^&1.2&ISO'"]) == ["x^^^&1.2&ISO"]


def _entry(svc, rep="envelope"):
    from scdpoc.demo.service import _now
    return svc._entry(unique_id=f"2.25.{uuid.uuid4().int}", rep=rep, cx=svc.policy.patient_cx("SYN-000101"),
                      issued=_now(), title="t", comments="")


def test_registry_rejects_ips_format_code_on_pdf(client):
    svc = client.app.state.service
    de = _entry(svc)
    de.codes["formatCode"] = Code("urn:ihe:pcc:ips:2020", "1.3.6.1.4.1.19376.1.2.3")
    sub = svc._submission(de.patient_id, [de], [], {de.entry_uuid: b"%PDF-1.7 test"})
    ex = svc.xds.provide_and_register(sub)
    assert ex.status.endswith("Failure")
    assert ex.errors[0]["code"] == "XDSRegistryMetadataError"
    assert "format code" in ex.errors[0]["context"]


def test_registry_rejects_foreign_patient_domain(client):
    svc = client.app.state.service
    de = _entry(svc)
    de.patient_id = de.source_patient_id = "943476591^^^&2.16.840.1.113883.2.1.4.1&ISO"
    sub = svc._submission(svc.policy.patient_cx("SYN-000101"), [de], [], {de.entry_uuid: b"%PDF"})
    ex = svc.xds.provide_and_register(sub)
    assert ex.status.endswith("Failure")
    assert ex.errors[0]["code"] == "XDSUnknownPatientId"


def test_repository_rejects_hash_mismatch(client):
    svc = client.app.state.service
    de = _entry(svc)
    de.hash = "0" * 40
    sub = svc._submission(de.patient_id, [de], [], {de.entry_uuid: b"%PDF"})
    ex = svc.xds.provide_and_register(sub)
    assert ex.errors[0]["code"] == "XDSNonIdenticalHash"


def test_duplicate_unique_id_rejected(client):
    svc = client.app.state.service
    de = _entry(svc)
    sub = svc._submission(de.patient_id, [de], [], {de.entry_uuid: b"%PDF-1"})
    assert svc.xds.provide_and_register(sub).status.endswith("Success")
    de2 = _entry(svc)
    de2.unique_id = de.unique_id
    sub2 = svc._submission(de.patient_id, [de2], [], {de2.entry_uuid: b"%PDF-2"})
    assert svc.xds.provide_and_register(sub2).errors[0]["code"] == "XDSDuplicateUniqueIdInRegistry"


def test_unknown_stored_query_and_bad_action(client):
    r = client.post("/xds/registry", content=b"<x/>", headers={"Content-Type": "application/soap+xml"})
    assert r.status_code == 400 and b"Fault" in r.content


def test_package_refused_when_validation_failed(client, monkeypatch):
    d = client.post("/api/demo/compose/lukas-brenner").json()
    svc = client.app.state.service
    rec = svc.draft(d["draftId"])
    rec["validation"]["publishable"] = False
    svc.work.save("drafts", d["draftId"], rec)
    assert client.post(f"/api/demo/package/{d['draftId']}").status_code == 409


@pytest.mark.parametrize("path", ["/fhir/metadata", "/api/info", "/", "/docs"])
def test_endpoints_up(client, path):
    assert client.get(path).status_code == 200
