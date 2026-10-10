"""Application factory: wires the actors, the standards endpoints and the demo UI."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Settings
from .crossborder.api import build_router as xb_router
from .crossborder.gateway import build_router as gateway_router
from .crossborder.service import JurisdictionB
from .demo.api import build_router as demo_router
from .demo.http import Http, HttpxTransport
from .demo.service import DemoService
from .mhd.router import build_router as mhd_router
from .safety import BANNER, DEMO_HEADER
from .xds.actors import AffinityDomainPolicy, Registry, Repository
from .xds.endpoints import build_router as xds_router
from .xds.store import AuditLog, Database, ObjectStore, RegistryStore

STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, http: Http | None = None) -> FastAPI:
    settings = settings or Settings()
    db = Database(settings.data_dir / "scdpoc.sqlite3")
    audit = AuditLog(db)
    policy = AffinityDomainPolicy(settings.affinity_domain)
    registry = Registry(RegistryStore(db), policy, audit)
    repository = Repository(settings.affinity_domain["affinity_domain"]["repository_unique_id"],
                            ObjectStore(db, settings.data_dir / "repository"), registry, audit)

    app = FastAPI(
        title="EHDS Structured Clinical Document PoC",
        version=__version__,
        description=(f"**{BANNER}.** Preserve the clinical document (PDF/A-3b envelope with embedded FHIR IPS); "
                     "exchange the computable summary (IHE XDS.b, MHD, sIPS). Not an EHDS, MyHealth@EU, NCPeH, "
                     "XDS, MHD or IPS certified implementation."),
        license_info={"name": "Apache-2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"},
    )
    svc = DemoService(settings, registry, repository, http or HttpxTransport())
    app.state.settings = settings
    app.state.service = svc

    @app.middleware("http")
    async def demo_only_header(request: Request, call_next):
        response = await call_next(request)
        response.headers[DEMO_HEADER[0]] = DEMO_HEADER[1]
        return response

    app.include_router(xds_router(repository, registry))
    app.include_router(mhd_router(registry, repository, settings, svc.on_demand_summary, svc.lifecycle_state_of))
    app.include_router(demo_router(svc))
    # v0.2 cross-border simulation: A's responding gateway, B's orchestration (talks to A over HTTP only)
    app.include_router(gateway_router(settings, registry, repository))
    jb = JurisdictionB(settings, svc.http, svc.base, audit)
    app.state.jurisdiction_b = jb
    app.include_router(xb_router(jb))

    @app.get("/healthz", include_in_schema=False)
    def health():
        return {"status": "ok", "demoOnly": True, "version": __version__}

    @app.get("/api/info", tags=["Demonstrator orchestration (non-normative)"])
    def info():
        return JSONResponse({
            "version": __version__, "banner": BANNER, "standards": settings.ips_package,
            "validators": {"hl7FhirValidator": bool(settings.hl7_validator_jar),
                           "verapdf": bool(settings.verapdf_cli or settings.verapdf_url),
                           "requireHl7Validator": settings.require_hl7_validator,
                           "requireVeraPdf": settings.require_verapdf},
            "crossBorder": {"home": settings.communities["home"]["name"],
                            "consumer": settings.communities["consumer"]["name"],
                            "consumerLanguage": settings.communities["consumer"]["language"]},
            "endpoints": {"xdsRepository": "/xds/repository", "xdsRegistry": "/xds/registry", "mhd": "/fhir",
                          "xcpd": "/gateway/xcpd", "xca": "/gateway/xca",
                          "openapi": "/docs"},
        })

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
