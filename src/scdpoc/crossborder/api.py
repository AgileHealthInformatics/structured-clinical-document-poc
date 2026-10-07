"""Demonstrator orchestration for Jurisdiction B (non-normative REST)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import HTMLResponse

from ..demo.service import DemoError
from ..safety import SyntheticDataViolation
from .service import JurisdictionB


def build_router(b: JurisdictionB) -> APIRouter:
    r = APIRouter(prefix="/api/demo/xb", tags=["Cross-border demonstrator - Jurisdiction B (non-normative)"])

    def run(fn, *a):
        try:
            return fn(*a)
        except DemoError as e:
            raise HTTPException(e.status, e.message) from e
        except SyntheticDataViolation as e:
            raise HTTPException(422, f"synthetic data guard: {e}") from e

    @r.get("/patients", summary="Jurisdiction B local patient index")
    def patients():
        return run(b.patients)

    @r.post("/discover/{key}", summary="B1. ITI-55 Cross Gateway Patient Discovery")
    def discover(key: str):
        return run(b.discover, key)

    @r.post("/exchange/{key}", summary="B2. ITI-38 query and ITI-39 retrieve, with verification")
    def exchange(key: str):
        return run(b.exchange, key)

    @r.get("/render/{key}", summary="B3. Local-language rendition and translation report")
    def render(key: str):
        return run(b.render, key)

    @r.get("/render/{key}/summary.html", response_class=HTMLResponse, summary="B3. Rendition as HTML")
    def render_html(key: str):
        return HTMLResponse(run(b.html, key))

    @r.post("/preserve/{key}", summary="B4. Custody copy: received IPS + local rendition as PDF/A-3b")
    def preserve(key: str):
        return run(b.preserve, key)

    @r.get("/preserve/{key}/custody.pdf", summary="Download the custody copy")
    def custody(key: str):
        return Response(run(b.custody_pdf, key), media_type="application/pdf")

    return r
