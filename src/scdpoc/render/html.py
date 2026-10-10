"""Deterministic HTML rendition of the patient summary (for on-screen review).

Produced from the same view model as the PDF pages and the FHIR section
narratives. The HTML is a convenience view; the PDF pages inside the PDF/A
envelope are the preserved human-readable rendition.
"""
from __future__ import annotations

from html import escape

from ..ips.view import SummaryView
from .labels import ENGLISH, Labels

RENDERER_VERSION = "scdpoc-render-4"

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


def render_html(view: SummaryView, labels: Labels = ENGLISH,
                extra_meta: list[tuple[str, str]] | None = None) -> str:
    rows = [
        (labels.patient, view.patient_name), (labels.identifier, view.patient_id),
        (labels.dob, view.birth_date), (labels.sex, view.gender),
        (labels.content_time, view.content_time), (labels.issued, view.issued), (labels.author, view.author), (labels.custodian, view.custodian),
        (labels.document, view.document_id), (labels.status, view.status), (labels.assurance, view.assurance),
    ]
    if view.attester:
        rows.append((labels.attester, view.attester))
    if view.replaces:
        rows.append((labels.replaces, view.replaces))
    rows += extra_meta or []
    meta = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows)
    def render(s, level: int) -> str:
        if s.empty_text:
            body = f'<p class="empty">{escape(s.empty_text)}</p>'
        elif s.rows:
            head = "".join(f"<th>{escape(c)}</th>" for c in s.columns)
            trs = "".join("<tr>" + "".join(f"<td>{escape(v)}</td>" for v in r) + "</tr>" for r in s.rows)
            body = f"<table><thead><tr>{head}</tr></thead><tbody>{trs}</tbody></table>"
        else:
            body = ""
        h = f"h{min(level, 6)}"
        subs = "".join(render(c, level + 1) for c in s.subsections)
        return f"<section><{h}>{escape(s.title)}</{h}>{body}{subs}</section>"

    sections = [render(s, 2) for s in view.sections]
    footer = labels.rendered_by.format(renderer=RENDERER_VERSION)
    return (f'<!doctype html><html lang="{escape(labels.lang)}"><head><meta charset="utf-8">'
            f"<title>{escape(view.title)} - {escape(view.patient_name)}</title><style>{_CSS}</style></head>"
            f'<body><div class="banner">{escape(labels.banner)}</div>'
            f"<h1>{escape(view.title)}</h1><dl class=\"meta\">{meta}</dl>{''.join(sections)}"
            f"<footer>{escape(footer)} &middot; {escape(view.document_id)}</footer></body></html>")
