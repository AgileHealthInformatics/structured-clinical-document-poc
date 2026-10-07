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
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
