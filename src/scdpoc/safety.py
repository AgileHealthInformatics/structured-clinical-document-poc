"""Public-data safety rules (design section 8, finding F-009).

The demonstrator only ever processes synthetic fixtures from a fixed path.
These checks are a seatbelt against accidental use of real identifiers; they
are not a de-identification tool and do not make the software safe for real
patient data.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import Settings

FIXTURE_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
DEMO_HEADER = ("X-SCDPOC-Demo-Only", "true")
BANNER = "SYNTHETIC DATA ONLY - demonstrator, not for clinical use"


class SyntheticDataViolation(ValueError):
    """Raised when input does not look like demonstrator synthetic data."""


def _nhs_number_like(digits: str) -> bool:
    """True if a 10-digit string passes the NHS number modulus-11 check."""
    if len(digits) != 10 or not digits.isdigit():
        return False
    total = sum(int(d) * w for d, w in zip(digits[:9], range(10, 1, -1), strict=True))
    check = 11 - (total % 11)
    if check == 11:
        check = 0
    return check != 10 and check == int(digits[9])


_PATTERNS = {
    "Irish PPS number-like value": re.compile(r"\b\d{7}[A-W][A-IW]?\b"),
    "US SSN-like value": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
}


def scan_for_real_identifiers(text: str) -> list[str]:
    findings: list[str] = []
    for run in re.findall(r"(?<!\d)\d{3}[ -]?\d{3}[ -]?\d{4}(?!\d)", text):
        if _nhs_number_like(re.sub(r"\D", "", run)):
            findings.append(f"NHS-number-like value '{run}'")
    for label, pat in _PATTERNS.items():
        for m in pat.findall(text):
            findings.append(f"{label} '{m}'")
    return findings


def assert_synthetic_source(source: dict[str, Any], settings: Settings) -> None:
    ad = settings.affinity_domain["affinity_domain"]["patient_assigning_authority"]
    if source.get("synthetic") is not True:
        raise SyntheticDataViolation("fixture is not marked \"synthetic\": true")
    pid = str(source.get("patient", {}).get("identifier", ""))
    if not pid.startswith(ad["value_prefix"]):
        raise SyntheticDataViolation(
            f"patient identifier must use the synthetic prefix {ad['value_prefix']!r}")
    findings = scan_for_real_identifiers(json.dumps(source))
    if findings:
        raise SyntheticDataViolation("possible real identifiers found: " + "; ".join(findings))


def load_fixture(settings: Settings, key: str) -> dict[str, Any]:
    if not FIXTURE_KEY.match(key):
        raise SyntheticDataViolation("invalid fixture key")
    path = (settings.fixtures_dir / f"{key}.json").resolve()
    if path.parent != settings.fixtures_dir.resolve() or not path.is_file():
        raise FileNotFoundError(key)
    source = json.loads(path.read_text(encoding="utf-8"))
    assert_synthetic_source(source, settings)
    return source


def list_fixtures(settings: Settings) -> list[dict[str, Any]]:
    out = []
    for p in sorted(Path(settings.fixtures_dir).glob("*.json")):
        src = json.loads(p.read_text(encoding="utf-8"))
        out.append({
            "key": src["key"],
            "name": " ".join(src["patient"]["given"]) + " " + src["patient"]["family"],
            "identifier": src["patient"]["identifier"],
            "birthDate": src["patient"]["birthDate"],
            "scenario": src.get("scenario", ""),
            "hasRevision": "revision" in src,
            "hasCorrection": "correction" in src,
        })
    return out
