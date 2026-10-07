"""Deterministic HTML rendition of the patient summary (for on-screen review).

Produced from the same view model as the PDF pages and the FHIR section
narratives. The HTML is a convenience view; the PDF pages inside the PDF/A
envelope are the preserved human-readable rendition.
"""
from __future__ import annotations

from html import escape

from ..ips.view import SummaryView

RENDERER_VERSION = "scdpoc-render-1"

_CSS = """
body{font-family:Georgia,'Times New Roman',serif;color:#1c1c1c;max-width:860px;margin:2rem auto;padding:0 1rem;line-height:1.45}
.banner{background:#7a1f1f;color:#fff;font:600 12px/1.4 system-ui,sans-serif;letter-spacing:.06em;padding:.4rem .7rem;text-transform:uppercase}
h1{font-size:1.6rem;margin:1rem 0 .2rem}
.meta{font:13px/1.5 system-ui,sans-serif;color:#444;display:grid;grid-template-columns:max-content 1fr;gap:.1rem 1rem;margin:1rem 0}
.meta dt{font-weight:600}
h2{font-size:1.1rem;border-bottom:1px solid #999;padding-bottom:.2rem;margin-top:1.6rem}
table{border-collapse:collapse;width:100%;font:13px/1.4 system-ui,sans-serif}
th,td{text-align:left;padding:.3rem .4rem;border-bottom:1px solid #ddd;vertical-align:top}
th{background:#f1efe9}
.empty{font-style:italic;color:#555}
footer{font:11px/1.4 ui-monospace,monospace;color:#555;margin-top:2rem;border-top:1px solid #ccc;padding-top:.5rem}
"""


def render_html(view: SummaryView) -> str:
    rows = [
        ("Patient", view.patient_name), ("Identifier", view.patient_id),
        ("Date of birth", view.birth_date), ("Sex (administrative)", view.gender),
        ("Issued", view.issued), ("Author", view.author), ("Custodian", view.custodian),
        ("Document", view.document_id),
    ]
    if view.replaces:
        rows.append(("Replaces", view.replaces))
    meta = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows)
    sections = []
    for s in view.sections:
        if s.empty_text:
            body = f'<p class="empty">{escape(s.empty_text)}</p>'
        else:
            head = "".join(f"<th>{escape(c)}</th>" for c in s.columns)
            trs = "".join("<tr>" + "".join(f"<td>{escape(v)}</td>" for v in r) + "</tr>" for r in s.rows)
            body = f"<table><thead><tr>{head}</tr></thead><tbody>{trs}</tbody></table>"
        sections.append(f"<section><h2>{escape(s.title)}</h2>{body}</section>")
    return (f'<!doctype html><html lang="{escape(view.language or "en")}"><head><meta charset="utf-8">'
            f"<title>{escape(view.title)} - {escape(view.patient_name)}</title><style>{_CSS}</style></head>"
            f'<body><div class="banner">Synthetic data only - demonstrator, not for clinical use</div>'
            f"<h1>{escape(view.title)}</h1><dl class=\"meta\">{meta}</dl>{''.join(sections)}"
            f"<footer>Rendered by {RENDERER_VERSION} from FHIR IPS document {escape(view.document_id)}"
            f"</footer></body></html>")
