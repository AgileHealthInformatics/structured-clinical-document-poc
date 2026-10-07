def test_on_demand_summary_is_distinct_from_snapshots(client, published):
    iss = published["publication"]["issuance"]
    r = client.get("/fhir/Patient/$summary", params={"identifier": "urn:oid:2.999.1.1|SYN-000101"})
    assert r.status_code == 200
    b = r.json()
    assert b["meta"]["tag"][0]["code"] == "on-demand"
    assert b["identifier"]["value"] != iss["documentUrn"]
    # not registered: discovery still shows only the issued snapshot pair
    disc = client.get("/api/demo/discover/amara-okafor").json()
    assert len(disc["xds"]["results"]) == 2


def test_unknown_patient_on_demand(client):
    r = client.get("/fhir/Patient/$summary", params={"identifier": "urn:oid:2.999.1.1|SYN-999999"})
    assert r.status_code == 404


def test_ehds_requires_publication(client):
    assert client.get("/api/demo/ehds-preview/ines-duarte").status_code == 404


def test_revision_requires_v1(client):
    assert client.post("/api/demo/compose/amara-okafor?revise=true").status_code == 409
