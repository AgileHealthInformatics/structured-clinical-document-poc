"""Profile 0.3.0 acceptance tests for review 2 items A-02, A-04, A-05, A-06 and A-09.

Fidelity (A-01) is in test_fidelity_030.py; Checker and vectors (A-03, A-10) in test_checker_030.py.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from scdpoc import lifecycle
from scdpoc.preservation import EventLog, fixity_check
from scdpoc.xds.model import (
    ASSOC_UPDATE_AVAILABILITY,
    RESPONSE_SUCCESS,
    STATUS_APPROVED,
    STATUS_DEPRECATED,
    Association,
    Submission,
)
from tests.conftest import mhd_doc

KEY = "amara-okafor"
SEQ = "ines-duarte"            # has both a revision and a correction scenario
MHD_ID_TYPE = "https://profiles.ihe.net/ITI/MHD/CodeSystem/IHE.MHD.MHDIdentifierType"


def _publish(client, key=KEY, **params) -> dict:
    d = client.post(f"/api/demo/compose/{key}", params=params)
    d.raise_for_status()
    return _issue(client, d.json()["draftId"])


def _issue(client, draft_id: str) -> dict:
    client.post(f"/api/demo/package/{draft_id}").raise_for_status()
    p = client.post(f"/api/demo/publish/{draft_id}")
    p.raise_for_status()
    return p.json()["issuance"]


def _status(client, entry_uuid: str) -> str:
    return client.app.state.service.registry.store.get(entry_uuid).status.rsplit(":", 1)[-1]


def _composition(client, draft_id: str) -> dict:
    return client.get(f"/api/demo/drafts/{draft_id}/ips.json").json()["entry"][0]["resource"]


def _events(client, doc=None) -> list[dict]:
    return client.get("/api/demo/preservation/events", params={"document": doc} if doc else {}).json()["events"]


# ------------------------------------------------------------------ A-02 assurance options

def test_snapshot_carries_no_attestation_claim(client):
    d = client.post(f"/api/demo/compose/{KEY}").json()
    assert d["assurance"] == "preserved-snapshot" and d["attestationEvidence"] is None
    comp = _composition(client, d["draftId"])
    assert "attester" not in comp, "PROV-04: no attestation claim in the source"
    author = comp["author"][0]["reference"]
    bundle = client.get(f"/api/demo/drafts/{d['draftId']}/ips.json").json()
    assert next(e for e in bundle["entry"] if e["fullUrl"] == author)["resource"]["resourceType"] == "Device"
    assert "not clinically attested" in d["html"], "PROV-05: assurance shown in the rendition"
    iss = _issue(client, d["draftId"])
    assert iss["assurance"] == "preserved-snapshot"
    de = client.app.state.service.registry.store.get(iss["ips"]["entryUUID"])
    assert de.legal_authenticator == "", "MET-13: no legalAuthenticator without attestation"
    assert "authenticator" not in mhd_doc(client, iss["ips"]["entryUUID"])


def test_attestation_is_an_explicit_action_with_evidence(client):
    d = client.post(f"/api/demo/compose/{KEY}").json()
    a = client.post(f"/api/demo/attest/{d['draftId']}").json()
    assert a["assurance"] == "attested-issuance" and a["draftId"] != d["draftId"]
    ev = a["attestationEvidence"]
    assert ev["reviewedDraft"] == d["draftId"] and ev["reviewedIpsSha256"] == d["ips"]["sha256"]
    assert ev["attester"] and ev["time"] and ev["mode"] == "legal" and "not a clinical attestation" in ev["method"]
    comp = _composition(client, a["draftId"])
    assert comp["attester"][0]["mode"] == "legal" and comp["attester"][0]["time"]
    assert client.post(f"/api/demo/attest/{a['draftId']}").status_code == 409, "already attested"
    iss = _issue(client, a["draftId"])
    assert iss["assurance"] == "attested-issuance" and iss["attestationEvidence"] == ev
    de = client.app.state.service.registry.store.get(iss["ips"]["entryUUID"])
    assert de.legal_authenticator.startswith("SYN-PRAC-1^Synthetic^Sam")
    dr = mhd_doc(client, iss["ips"]["entryUUID"])
    assert dr["authenticator"] == {"reference": "#legal-authenticator"}
    assert dr["contained"][0]["resourceType"] == "Practitioner"
    assert any(e["type"] == "attestation" for e in _events(client, a["documentUrn"]))


def test_issued_snapshot_cannot_be_attested_afterwards(client, published):
    assert client.post(f"/api/demo/attest/{published['draft']['draftId']}").status_code == 409


# ------------------------------------------------------------------ A-04 MHD identity

def test_mhd_ids_are_server_assigned_and_identifiers_typed(client, published):
    iss = published["publication"]["issuance"]
    for rep in ("ips", "envelope"):
        dr = mhd_doc(client, iss[rep]["entryUUID"])
        assert iss[rep]["entryUUID"].removeprefix("urn:uuid:") not in dr["id"]
        slices = {i["type"]["coding"][0]["code"]: i for i in dr["identifier"]}
        assert slices["entryUUID"]["system"] == "urn:ietf:rfc:3986"
        assert slices["entryUUID"]["value"] == iss[rep]["entryUUID"]
        assert slices["uniqueId"]["value"] == f"urn:oid:{iss[rep]['uniqueId']}" == dr["masterIdentifier"]["value"]
        assert dr["masterIdentifier"]["type"]["coding"][0] == {"system": MHD_ID_TYPE, "code": "uniqueId"}
        assert all(i["type"]["coding"][0]["system"] == MHD_ID_TYPE for i in dr["identifier"])
        # read by the server id works; a URL constructed from the entryUUID does not
        assert client.get(f"/fhir/DocumentReference/{dr['id']}").json()["id"] == dr["id"]
        assert client.get(f"/fhir/DocumentReference/{iss[rep]['entryUUID'].removeprefix('urn:uuid:')}"
                          ).status_code == 404
        # search by the uniqueId slice finds the same resource
        by_uid = client.get("/fhir/DocumentReference",
                            params={"identifier": f"urn:ietf:rfc:3986|urn:oid:{iss[rep]['uniqueId']}"}).json()
        assert by_uid["entry"][0]["resource"]["id"] == dr["id"]
    env = mhd_doc(client, iss["envelope"]["entryUUID"])
    ips = mhd_doc(client, iss["ips"]["entryUUID"])
    assert {"code": "transforms", "target": {"reference": f"DocumentReference/{ips['id']}"}} in env["relatesTo"]
    assert client.get(ips["content"][0]["attachment"]["url"]).content == \
        client.get(f"/api/demo/drafts/{published['draft']['draftId']}/ips.json").content


# ------------------------------------------------------------------ A-06 lifecycle

def test_lifecycle_table_rejects_unlisted_transitions():
    assert lifecycle.transition(None, "issue").target == lifecycle.ISSUED
    for state in (lifecycle.REPLACED_UPDATE, lifecycle.REPLACED_CORRECTION, lifecycle.WITHDRAWN):
        for action in ("withdraw", "replace-for-update", "replace-for-correction", "issue"):
            with pytest.raises(lifecycle.LifecycleError):
                lifecycle.transition(state, action)
    rows = lifecycle.table()
    assert {r["action"] for r in rows} == {"issue", "replace-for-update", "replace-for-correction", "withdraw"}
    assert all(r["originalRetrievable"] for r in rows if r["from"] != "-")
    assert not any(r["originalCurrent"] for r in rows if r["from"] != "-")


def test_issue_replace_correct_withdraw_sequence(client):
    """Review A-06 acceptance: execute the complete sequences, checking both DocumentEntries each time."""
    svc = client.app.state.service
    v1 = _publish(client, SEQ)
    assert (_status(client, v1["ips"]["entryUUID"]), _status(client, v1["envelope"]["entryUUID"])) == \
        ("Approved", "Approved")

    v2 = _publish(client, SEQ, revise="true")                                     # replacement for update
    assert v2["replacementReason"] == "update"
    assert (_status(client, v1["ips"]["entryUUID"]), _status(client, v1["envelope"]["entryUUID"])) == \
        ("Deprecated", "Deprecated")
    assert (_status(client, v2["ips"]["entryUUID"]), _status(client, v2["envelope"]["entryUUID"])) == \
        ("Approved", "Approved")

    v3 = _publish(client, SEQ, correct="true")                                    # replacement for correction
    assert v3["replacementReason"] == "correction"
    bundle = client.get(f"/api/demo/drafts/{v3['packageId']}/ips.json").json()
    assert bundle["entry"][0]["resource"]["status"] == "amended", "IPS-04"
    assert "2.5 mg orally twice daily" in json.dumps(bundle), "the corrected dose"
    assert (_status(client, v2["ips"]["entryUUID"]), _status(client, v2["envelope"]["entryUUID"])) == \
        ("Deprecated", "Deprecated")

    w = client.post(f"/api/demo/withdraw/{SEQ}").json()                    # withdrawal, no replacement
    assert w["state"] == lifecycle.WITHDRAWN and w["exchange"]["transaction"] == "ITI-57"
    assert (_status(client, v3["ips"]["entryUUID"]), _status(client, v3["envelope"]["entryUUID"])) == \
        ("Deprecated", "Deprecated")

    states = {i["version"]: i["lifecycleState"] for i in client.get(f"/api/demo/history/{SEQ}").json()["issuances"]}
    assert states == {1: lifecycle.REPLACED_UPDATE, 2: lifecycle.REPLACED_CORRECTION, 3: lifecycle.WITHDRAWN}

    # Historical retrievability is distinct from being the current summary (LIF-10).
    cx = svc._cx({"patient": {"identifier": "SYN-000103"}})
    assert svc.registry.find_documents(cx, [STATUS_APPROVED]) == [], "no current summary after withdrawal"
    for v in (v1, v2, v3):
        assert svc.repository.retrieve(v["envelope"]["repositoryUniqueId"], v["envelope"]["uniqueId"]) is not None
    assert mhd_doc(client, v3["ips"]["entryUUID"])["status"] == "superseded"
    assert client.post(f"/api/demo/compose/{SEQ}", params={"revise": "true"}).status_code == 409
    assert client.post(f"/api/demo/withdraw/{SEQ}").status_code == 409

    types = [e["type"] for e in _events(client)]
    assert types.count("issuance") == 3 and "replacement" in types and "correction" in types
    assert "withdrawal" in types and types.count("notification-required") == 2, "after correction and withdrawal"

    v4 = _publish(client, SEQ)                                                    # a new issuance after withdrawal
    assert v4["replaces"] is None and v4["replacementReason"] is None


def test_withdrawal_is_atomic(client, published):
    """A failing ITI-57 submission (second change invalid) changes nothing."""
    svc = client.app.state.service
    iss = published["publication"]["issuance"]
    ss = svc._submission(svc._cx({"patient": {"identifier": "SYN-000101"}}), [], [], {}).submission_set
    ok = Association("urn:uuid:00000000-0000-4000-8000-000000000001", ASSOC_UPDATE_AVAILABILITY, ss.entry_uuid,
                     iss["ips"]["entryUUID"], {"OriginalStatus": [STATUS_APPROVED], "NewStatus": [STATUS_DEPRECATED]})
    bad = Association("urn:uuid:00000000-0000-4000-8000-000000000002", ASSOC_UPDATE_AVAILABILITY, ss.entry_uuid,
                      iss["envelope"]["entryUUID"],
                      {"OriginalStatus": [STATUS_DEPRECATED], "NewStatus": [STATUS_APPROVED]})   # wrong original
    ex = svc.xds.update_document_set(Submission(ss, [], [ok, bad]))
    assert ex.status != RESPONSE_SUCCESS and ex.errors[0]["code"] == "XDSMetadataUpdateError"
    assert _status(client, iss["ips"]["entryUUID"]) == "Approved", "no partial update"


def test_gateway_releases_nothing_after_withdrawal(client, published):
    client.post("/api/demo/xb/discover/b-okafor")
    first = client.post("/api/demo/xb/exchange/b-okafor").json()
    assert first["received"]["checksPassed"]
    client.post(f"/api/demo/withdraw/{KEY}").raise_for_status()
    again = client.post("/api/demo/xb/exchange/b-okafor").json()
    assert again["documents"] == [], "a withdrawn issuance is not released across the border"
    note = [e for e in _events(client) if e["type"] == "notification-required"]
    consumer = client.app.state.settings.communities["consumer"]["name"]
    assert note[-1]["detail"]["knownRecipients"] == [consumer], "the receiving community is a known recipient"


def test_stale_summary_is_not_current_for_the_receiver(client, published):
    client.post("/api/demo/xb/discover/b-okafor")
    client.post("/api/demo/xb/exchange/b-okafor").raise_for_status()
    _publish(client, revise="true")
    b = client.app.state.jurisdiction_b
    state = b._state("b-okafor")
    old_uid = state["current"]
    de = client.app.state.service.registry.store.get_by_unique_id(old_uid)
    assert de.status == STATUS_DEPRECATED
    data, _, _ = b.gw.retrieve("urn:oid:2.999.1.9", de.repository_unique_id, de.unique_id)
    assert data is None, "the superseded IPS is not released across the border (XB-02)"
    r = client.post("/api/demo/xb/exchange/b-okafor").json()
    assert r["received"]["uniqueId"] != old_uid and r["received"]["checksPassed"]


# ------------------------------------------------------------------ A-05 provenance across the boundary

def test_receiver_establishes_provenance_from_ips_alone(client, published):
    client.post("/api/demo/xb/discover/b-okafor")
    r = client.post("/api/demo/xb/exchange/b-okafor").json()
    vb7 = next(c for c in r["verification"] if c["id"] == "VB-7")
    assert vb7["passed"], vb7
    prov = r["received"]["provenance"]
    assert prov["author"] == ["SCD-PoC summary generator"] and prov["authorTypes"] == ["Device"]
    assert prov["custodian"] == prov["issuingOrganisation"] == "Synthetic Health Organisation"
    assert prov["clinicalContentDate"] and prov["documentIdentifier"].startswith("urn:uuid:")
    assert prov["attester"] == [] and "no attestation claimed" in prov["assurance"]
    assert prov["entryLevel"], "entry-level provenance carried where the source has it, not inferred"


# ------------------------------------------------------------------ A-09 preservation events and fixity

def test_event_log_structure_and_chain(client, published):
    events = _events(client)
    required = {"eventId", "type", "time", "agent", "artefact", "digest", "outcome", "evidence", "prevHash", "hash"}
    assert events and all(required <= set(e) for e in events)
    iss = next(e for e in events if e["type"] == "issuance")
    assert iss["detail"]["issuanceRecordSha256"] and iss["digest"]["algorithm"] == "SHA-256"
    chain = client.get("/api/demo/preservation/events").json()["chain"]
    assert chain["intact"] and chain["anchorMatches"]


def test_fixity_detects_modified_archived_copy(client, published, settings):
    """Review A-09 acceptance: modify an archived copy without changing its issuance record; an independent
    fixity check detects it, records the failure and leaves the earlier evidence intact."""
    ok = client.post("/api/demo/preservation/fixity").json()
    assert ok["passed"] and ok["checked"] == 2
    before = _events(client)

    iss = published["publication"]["issuance"]
    svc = client.app.state.service
    row = svc.repository.objects.db.one("SELECT path FROM repository_object WHERE unique_id=?",
                                         (iss["envelope"]["uniqueId"],))
    path = settings.data_dir / "repository" / row[0]
    os.chmod(path, stat.S_IWUSR | stat.S_IRUSR)
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01                                              # one bit in the archived copy
    path.write_bytes(bytes(data))

    out = fixity_check(settings.data_dir, agent="independent auditor")      # no server involved
    assert not out["passed"] and len(out["failed"]) == 1
    assert out["failed"][0]["artefact"] == "envelope" and out["failed"][0]["expected"] == iss["envelope"]["sha256"]
    assert out["chain"]["intact"], "recording the failure keeps the chain intact"
    after = EventLog(settings.data_dir / "preservation").events()
    assert after[:len(before)] == before, "earlier events unchanged"
    failures = [e for e in after if e["type"] == "fixity-check" and e["outcome"] == "failure"]
    assert len(failures) == 1 and failures[0]["agent"] == "independent auditor"


def test_altered_issuance_record_or_event_is_detected(client, published, settings):
    p = settings.data_dir / "work" / "patients" / KEY / "issuances.json"
    recs = json.loads(p.read_text())
    recs[0]["assurance"] = "attested-issuance"                              # an after-the-fact claim
    p.write_text(json.dumps(recs))
    out = fixity_check(settings.data_dir)
    assert any("differs from the digest recorded at issuance" in x for x in out["issuanceRecordProblems"])

    log = EventLog(settings.data_dir / "preservation")
    lines = log.path.read_text().splitlines()
    first = json.loads(lines[0])
    first["outcome"] = "failure"
    log.path.write_text("\n".join([json.dumps(first), *lines[1:]]) + "\n")
    assert not log.verify().intact
