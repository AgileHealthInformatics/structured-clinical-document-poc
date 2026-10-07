"""PoC-specific REST endpoints driving the tutorial journey (non-normative)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import HTMLResponse

from ..safety import SyntheticDataViolation
from .service import DemoError, DemoService


def build_router(svc: DemoService) -> APIRouter:
    r = APIRouter(prefix="/api/demo", tags=["Demonstrator orchestration (non-normative)"])

    def run(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except DemoError as e:
            raise HTTPException(e.status, e.message) from e
        except SyntheticDataViolation as e:
            raise HTTPException(422, f"synthetic data guard: {e}") from e
        except FileNotFoundError as e:
            raise HTTPException(404, f"not found: {e}") from e

    @r.get("/patients", summary="1. List synthetic scenarios")
    def patients():
        return run(svc.patients)

    @r.get("/patients/{key}", summary="Synthetic source record and issuance state")
    def patient(key: str):
        return run(svc.patient, key)

    @r.post("/compose/{key}", summary="2-4. Compose IPS, validate, render preview")
    def compose(key: str, revise: bool = Query(False, description="apply the fixture's revision (replacement demo)")):
        return run(svc.compose, key, revise)

    @r.get("/drafts/{draft_id}", summary="Draft record (validation evidence)")
    def draft(draft_id: str):
        return run(svc.draft, draft_id)

    @r.get("/drafts/{draft_id}/ips.json", summary="Exact IPS bytes of the draft")
    def draft_ips(draft_id: str):
        return Response(run(svc.draft_ips, draft_id), media_type="application/fhir+json")

    @r.get("/drafts/{draft_id}/summary.html", response_class=HTMLResponse, summary="HTML rendition")
    def draft_html(draft_id: str):
        return HTMLResponse(run(svc.draft_html, draft_id))

    @r.post("/package/{draft_id}", summary="5-6. Create PDF/A-3b envelope and validate it")
    def package(draft_id: str):
        return run(svc.package, draft_id)

    @r.get("/package/{package_id}/envelope.pdf", summary="Download the envelope")
    def envelope(package_id: str, download: bool = False):
        rec = run(svc.package_record, package_id)
        disp = "attachment" if download else "inline"
        return Response(run(svc.envelope_bytes, package_id), media_type="application/pdf",
                        headers={"Content-Disposition": f'{disp}; filename="{rec["envelopeName"]}"'})

    @r.get("/package/{package_id}/verapdf-report.xml", summary="veraPDF machine-readable report")
    def vera(package_id: str):
        return Response(run(svc.verapdf_report, package_id), media_type="application/xml")

    @r.post("/publish/{package_id}", summary="7. Publish via XDS.b ITI-41")
    def publish(package_id: str):
        return run(svc.publish, package_id)

    @r.get("/discover/{key}", summary="8a. XDS ITI-18 and MHD ITI-67 side by side")
    def discover(key: str):
        return run(svc.discover, key)

    @r.get("/retrieve/{key}", summary="8b/9. ITI-43 envelope + ITI-68 IPS projection, with integrity checks")
    def retrieve(key: str, version: int | None = None):
        return run(svc.retrieve, key, version)

    @r.get("/ehds-preview/{key}", summary="10. EHDS readiness preview (non-normative)")
    def ehds(key: str, version: int | None = None):
        return run(svc.ehds_preview, key, version)

    @r.post("/tamper/{key}", summary="Tamper-detection demonstration (in-memory copy)")
    def tamper(key: str, mode: str = Query("embedded", pattern="^(embedded|outer)$")):
        return run(svc.tamper, key, mode)

    @r.get("/history/{key}", summary="Issued versions, registry status and associations")
    def history(key: str):
        return run(svc.history, key)

    @r.get("/evidence/{package_id}", summary="Combined evidence record for one issuance")
    def evidence(package_id: str):
        return run(svc.evidence, package_id)

    @r.get("/audit", summary="Recent audit events")
    def audit(limit: int = 50):
        return svc.registry.audit.recent(limit)

    return r
