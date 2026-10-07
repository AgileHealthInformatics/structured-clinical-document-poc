import pytest

from scdpoc.safety import (
    SyntheticDataViolation,
    assert_synthetic_source,
    load_fixture,
    scan_for_real_identifiers,
)


def test_nhs_number_pattern_detected():
    # 943 476 5919 is the widely published NHS number test example (valid checksum)
    assert scan_for_real_identifiers("patient 943 476 5919 seen")
    assert not scan_for_real_identifiers("SYN-000101 and 1234567890")  # invalid checksum


def test_ppsn_pattern_detected():
    assert scan_for_real_identifiers("PPSN 1234567TA")


def test_rejects_non_synthetic_prefix(settings):
    src = load_fixture(settings, "amara-okafor")
    src["patient"]["identifier"] = "MRN-000101"
    with pytest.raises(SyntheticDataViolation):
        assert_synthetic_source(src, settings)


def test_rejects_unmarked_fixture(settings):
    src = load_fixture(settings, "amara-okafor")
    src["synthetic"] = False
    with pytest.raises(SyntheticDataViolation):
        assert_synthetic_source(src, settings)


@pytest.mark.parametrize("key", ["../etc/passwd", "AMARA", "a/b", ""])
def test_fixture_key_cannot_escape_path(settings, key):
    with pytest.raises((SyntheticDataViolation, FileNotFoundError)):
        load_fixture(settings, key)


def test_demo_header_and_banner(client):
    r = client.get("/healthz")
    assert r.headers["X-SCDPOC-Demo-Only"] == "true"
    assert r.json()["demoOnly"] is True
