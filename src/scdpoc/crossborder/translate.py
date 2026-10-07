"""Consumer-side localisation of a received IPS (Jurisdiction B).

The received IPS bytes are never modified. Localisation works on a copy and
produces a *view* for the reading clinician: coded displays are replaced by
the consumer jurisdiction's designations, captions are translated, and
anything that cannot be translated is shown in the original language with an
explicit marker. Free text (e.g. dosage instructions) is passed through
unchanged and reported, because it cannot be transcoded safely.
"""
from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..ips.view import SummaryView, build_view
from ..render.labels import Labels

COUNTED_SYSTEMS_KEY = "codes"


@dataclass
class TranslationReport:
    language: str
    translated: list[dict[str, str]] = field(default_factory=list)
    untranslated: list[dict[str, str]] = field(default_factory=list)
    free_text_passed_through: list[dict[str, str]] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        n = len(self.translated) + len(self.untranslated)
        return 1.0 if n == 0 else len(self.translated) / n

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["coverage"] = round(self.coverage, 3)
        return d


def _walk_ccs(node: Any, path: str):
    if isinstance(node, dict):
        if isinstance(node.get("coding"), list):
            yield path, node
        for k, v in node.items():
            if k != "text":
                yield from _walk_ccs(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_ccs(v, f"{path}[{i}]")


def localise(bundle: dict[str, Any], designations: dict[str, Any]) -> tuple[SummaryView, TranslationReport, Labels]:
    lang = designations["language"]
    codes = designations["codes"]
    values = designations.get("values", {})
    lab_cfg = designations.get("labels", {})
    labels = Labels.from_config(lang, lab_cfg)
    marker = lab_cfg.get("untranslated", "untranslated")
    report = TranslationReport(language=lang)
    b = copy.deepcopy(bundle)

    for entry in b["entry"][1:]:                       # clinical resources (Composition handled below)
        res = entry["resource"]
        rt = res["resourceType"]
        for path, cc in _walk_ccs(res, rt):
            coding = cc["coding"][0]
            system, code = coding.get("system"), coding.get("code")
            if system not in codes:
                continue                               # status/category vocabularies: handled as values
            original = cc.get("text") or coding.get("display") or code
            german = codes[system].get(code)
            if german:
                coding["display"] = german
                cc["text"] = german
                report.translated.append({"resource": rt, "path": path, "system": system, "code": code,
                                          "original": original, "translated": german})
            else:
                cc["text"] = f"{original} [{marker}]"
                report.untranslated.append({"resource": rt, "path": path, "system": system, "code": code,
                                            "original": original})
        if rt == "MedicationStatement":
            for d in res.get("dosage", []):
                if d.get("text"):
                    report.free_text_passed_through.append({"resource": rt, "element": "dosage.text",
                                                            "text": d["text"]})

    comp = b["entry"][0]["resource"]
    comp["title"] = lab_cfg.get("title", comp.get("title", ""))
    for sec in comp.get("section", []):
        code = sec["code"]["coding"][0]["code"]
        sec["title"] = designations.get("sections", {}).get(code, sec["title"])

    view = build_view(b)
    col_map = lab_cfg.get("columns", {})
    view.gender = values.get(view.gender, view.gender)
    for s in view.sections:
        s.columns = [col_map.get(c, c) for c in s.columns]
        s.rows = [[values.get(cell, cell) for cell in row] for row in s.rows]
        if s.empty_text:
            s.empty_text = values.get(s.empty_text, s.empty_text)
    return view, report, labels


def coded_facts(view: SummaryView) -> list[str]:
    """Codes that must be visible in any consumer-side rendition (code column)."""
    return [row[1] for s in view.sections for row in s.rows if len(row) > 1 and row[1]]


def missing_codes(view: SummaryView, page_text: str) -> list[str]:
    norm = re.sub(r"\s+", "", page_text)
    return [c for c in coded_facts(view) if re.sub(r"\s+", "", c) not in norm]
