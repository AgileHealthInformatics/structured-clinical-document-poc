"""Command line entry point: ``scdpoc serve`` and offline helpers used by CI."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def _serve(args) -> None:
    import uvicorn

    from .app import create_app
    uvicorn.run(create_app(), host=args.host, port=args.port)


def _build_fixtures(args) -> None:
    """Write IPS JSON and PDF/A envelopes for every fixture (for external validators in CI)."""
    import uuid

    from .config import Settings
    from .ips.composer import compose_ips
    from .ips.validator import validate_ips
    from .ips.view import build_view
    from .pdfa.packager import attachment_name_for, build_envelope
    from .render.pdf import render_pdf
    from .safety import list_fixtures, load_fixture

    s = Settings()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    issued = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    failed = False
    for f in list_fixtures(s):
        doc_id = uuid.uuid5(uuid.NAMESPACE_URL, f"scdpoc:fixture:{f['key']}")
        d = compose_ips(load_fixture(s, f["key"]), s, document_id=doc_id, issued=issued)
        rep = validate_ips(d.bundle, d.json_bytes, s)
        view = build_view(d.bundle)
        env = build_envelope(render_pdf(view, attachment_name_for(str(doc_id))), d.json_bytes,
                             document_id=str(doc_id), issued=issued, title=view.title, author=view.custodian,
                             subject=f"International Patient Summary for {view.patient_name}")
        (out / f"{f['key']}.ips.json").write_bytes(d.json_bytes)
        (out / f"{f['key']}.pdf").write_bytes(env.pdf_bytes)
        # v0.2: consumer-side (Jurisdiction B) custody envelope, so CI also validates a localised PDF/A
        from .crossborder.translate import localise
        lview, _, labels = localise(d.bundle, s.designations)
        cenv = build_envelope(render_pdf(lview, attachment_name_for(str(doc_id)), labels), d.json_bytes,
                              document_id=str(doc_id), issued=issued, title=lview.title, author="Jurisdiction B",
                              subject="custody copy")
        (out / f"{f['key']}.custody-{labels.lang}.pdf").write_bytes(cenv.pdf_bytes)
        print(f"{f['key']}: preflight={'pass' if rep.engines[0].status == 'passed' else 'FAIL'} "
              f"pdf_sha256={env.pdf_sha256}")
        failed |= rep.engines[0].status != "passed"
    sys.exit(1 if failed else 0)


def _validate(args) -> None:
    from .config import Settings
    from .ips.validator import validate_ips
    s = Settings()
    data = Path(args.file).read_bytes()
    rep = validate_ips(json.loads(data), data, s)
    print(json.dumps(rep.to_dict(), indent=2))
    sys.exit(0 if rep.publishable else 1)


def _check(args) -> None:
    from .checker import main as check_main
    sys.exit(check_main(args))


def _check_vectors(args) -> None:
    from .conformance_kit import check_vectors, write
    out = check_vectors(Path(args.vectors), reports_dir=Path(args.reports) if args.reports else None)
    write(out, args.out)
    sys.exit(0 if all(r["outcome"] == "pass" for r in out["results"].values()) else 1)


def _live_check(args) -> None:
    import os

    from .conformance_kit import live_check, write
    kw = {}
    for env, key in (("SCDPOC_REQUIRE_HL7_VALIDATOR", "require_hl7_validator"),
                     ("SCDPOC_REQUIRE_VERAPDF", "require_verapdf")):
        if os.environ.get(env):
            kw[key] = os.environ[env].lower() in ("1", "true", "yes")
    out = live_check(args.only, kw)
    write(out, args.out)
    sys.exit(0 if all(r["outcome"] == "pass" for r in out["results"].values()) else 1)


def _fixity(args) -> None:
    from .preservation import main as fixity_main
    sys.exit(fixity_main(args))


def _build_vectors(args) -> None:
    from .vectors import build
    m = build(Path(args.out))
    print(f"wrote {len(m['vectors'])} vectors to {args.out}")


def main() -> None:
    p = argparse.ArgumentParser(prog="scdpoc", description="EHDS Structured Clinical Document PoC (synthetic only)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the demonstrator")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)
    s.set_defaults(fn=_serve)
    b = sub.add_parser("build-fixtures", help="write IPS + PDF/A for each fixture (deterministic)")
    b.add_argument("--out", default="build/fixtures")
    b.set_defaults(fn=_build_fixtures)
    v = sub.add_parser("validate", help="run IPS validation on a JSON file")
    v.add_argument("file")
    v.set_defaults(fn=_validate)
    c = sub.add_parser("check", help="Checker: evaluate a claim (classes, options) against the profile rules")
    c.add_argument("envelope", nargs="?", help="envelope PDF (artefact method)")
    c.add_argument("--projection")
    c.add_argument("--record", help="issuance record JSON")
    c.add_argument("--class", dest="cls", action="append", help="conformance class claimed (repeatable); "
                                                                 "default: from --claim, else Envelope")
    c.add_argument("--option", action="append", help="option claimed: AI, PP, MHD, OD (repeatable)")
    c.add_argument("--claim", help="conformance claim JSON (claim and inspection methods)")
    c.add_argument("--evidence", action="append", help="live-check or check-vectors result file (repeatable)")
    c.add_argument("--out")
    c.set_defaults(fn=_check)
    cv = sub.add_parser("check-vectors", help="run the Checker over the conformance vectors (method: vectors)")
    cv.add_argument("--vectors", default="conformance/vectors")
    cv.add_argument("--out")
    cv.add_argument("--reports", help="directory for one Checker report per vector")
    cv.set_defaults(fn=_check_vectors)
    lc = sub.add_parser("live-check", help="run live scenarios L-01..L-12 against a fresh in-process instance")
    lc.add_argument("--only", action="append", help="scenario id, e.g. L-06 (repeatable)")
    lc.add_argument("--out")
    lc.set_defaults(fn=_live_check)
    fx = sub.add_parser("fixity", help="independent fixity check of issued artefacts and the preservation log")
    fx.add_argument("--data-dir", help="demonstrator data directory (default: SCDPOC_DATA_DIR or ./data)")
    fx.add_argument("--out")
    fx.set_defaults(fn=_fixity)
    bv = sub.add_parser("build-vectors", help="regenerate deterministic conformance test vectors")
    bv.add_argument("--out", default="conformance/vectors")
    bv.set_defaults(fn=_build_vectors)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
