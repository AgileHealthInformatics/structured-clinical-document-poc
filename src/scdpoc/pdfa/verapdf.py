"""Independent PDF/A validation with veraPDF (AT-05, finding F-010).

Two integration modes, chosen by configuration:

* CLI  - ``SCDPOC_VERAPDF_CLI=/path/to/verapdf`` (the veraPDF command-line tool)
* REST - ``SCDPOC_VERAPDF_URL=http://verapdf:8080`` (the veraPDF REST service)

If neither is configured the result is ``not-run``. The demonstrator never
reports PDF/A conformance on the strength of its own pre-flight.
Reports are parsed from veraPDF's XML (machine-readable report) output.
"""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from lxml import etree

from ..config import Settings

FLAVOUR = "3b"


@dataclass
class VeraPdfResult:
    status: str                         # passed | failed | not-run | error
    detail: str
    profile: str = ""
    failed_rules: list[dict] = field(default_factory=list)
    raw_report: bytes | None = None

    def to_dict(self) -> dict:
        return {"engine": "veraPDF", "status": self.status, "detail": self.detail, "profile": self.profile,
                "failed_rules": self.failed_rules, "report_available": self.raw_report is not None}


def parse_report(xml: bytes) -> VeraPdfResult:
    root = etree.fromstring(xml)
    vr = next((e for e in root.iter() if etree.QName(e).localname == "validationReport"), None)
    if vr is None:
        return VeraPdfResult("error", "veraPDF report contained no validationReport element", raw_report=xml)
    compliant = vr.get("isCompliant", "").lower() == "true"
    failed = []
    for rule in vr.iter():
        if etree.QName(rule).localname == "rule" and rule.get("status") == "failed":
            desc = next((c.text for c in rule if etree.QName(c).localname == "description"), "")
            failed.append({"clause": rule.get("clause"), "test": rule.get("testNumber"),
                           "failedChecks": rule.get("failedChecks"), "description": (desc or "").strip()})
    return VeraPdfResult("passed" if compliant else "failed",
                         vr.get("statement", "") or ("compliant" if compliant else "not compliant"),
                         profile=vr.get("profileName", ""), failed_rules=failed, raw_report=xml)


class VeraPdf:
    def __init__(self, settings: Settings):
        self.cli = settings.verapdf_cli
        self.url = settings.verapdf_url

    def configured(self) -> bool:
        return bool(self.cli or self.url)

    def validate(self, pdf_bytes: bytes) -> VeraPdfResult:
        try:
            if self.cli:
                return self._cli(pdf_bytes)
            if self.url:
                return self._rest(pdf_bytes)
        except Exception as exc:  # validator infrastructure failure is reported, never hidden
            return VeraPdfResult("error", f"veraPDF invocation failed: {exc}")
        return VeraPdfResult("not-run", "veraPDF not configured (set SCDPOC_VERAPDF_CLI or SCDPOC_VERAPDF_URL); "
                                        "CI validates every fixture envelope with veraPDF")

    def _cli(self, pdf_bytes: bytes) -> VeraPdfResult:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "envelope.pdf"
            f.write_bytes(pdf_bytes)
            proc = subprocess.run([self.cli, "--flavour", FLAVOUR, "--format", "xml", str(f)],
                                  capture_output=True, timeout=300, check=False)
        if not proc.stdout:
            return VeraPdfResult("error", f"veraPDF CLI produced no output (exit {proc.returncode}): "
                                          f"{proc.stderr.decode(errors='replace')[-400:]}")
        return parse_report(proc.stdout)

    def _rest(self, pdf_bytes: bytes) -> VeraPdfResult:
        r = httpx.post(f"{self.url.rstrip('/')}/api/validate/{FLAVOUR}",
                       files={"file": ("envelope.pdf", pdf_bytes, "application/pdf")},
                       headers={"Accept": "application/xml"}, timeout=120)
        r.raise_for_status()
        return parse_report(r.content)
