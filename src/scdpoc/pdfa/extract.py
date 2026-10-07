"""Read Associated Files back out of an envelope (inventory + exact bytes)."""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

import pikepdf
from pikepdf import Name


@dataclass
class EmbeddedFile:
    filename: str
    mime_type: str
    relationship: str
    description: str
    size: int
    sha256: str
    data: bytes
    listed_in_af: bool
    listed_in_name_tree: bool

    def inventory(self) -> dict:
        d = self.__dict__.copy()
        d.pop("data")
        return d


def _decode_name(n) -> str:
    s = str(n).lstrip("/")
    return s


def extract_embedded(pdf_bytes: bytes) -> list[EmbeddedFile]:
    """Return every embedded file reachable from /AF or the EmbeddedFiles name tree."""
    pdf = pikepdf.open(io.BytesIO(pdf_bytes))
    root = pdf.Root
    af_specs = list(root.get(Name.AF, pikepdf.Array()))
    tree_specs = []
    names = root.get(Name.Names)
    if names is not None and Name.EmbeddedFiles in names:
        arr = names.EmbeddedFiles.get(Name.Names, pikepdf.Array())
        tree_specs = [arr[i + 1] for i in range(0, len(arr), 2)]

    def key(spec) -> tuple:
        return spec.objgen if spec.is_indirect else (id(spec), 0)

    seen: dict[tuple, EmbeddedFile] = {}
    af_keys = {key(s) for s in af_specs}
    tree_keys = {key(s) for s in tree_specs}
    for spec in [*af_specs, *tree_specs]:
        k = key(spec)
        if k in seen:
            continue
        stream = spec.EF.get(Name.UF) or spec.EF.get(Name.F)
        data = stream.read_bytes()
        seen[k] = EmbeddedFile(
            filename=str(spec.get(Name.UF) or spec.get(Name.F)),
            mime_type=_decode_name(stream.get(Name.Subtype, "")),
            relationship=_decode_name(spec.get(Name.AFRelationship, "/Unspecified")),
            description=str(spec.get(Name.Desc, "")),
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            data=data,
            listed_in_af=k in af_keys,
            listed_in_name_tree=k in tree_keys,
        )
    return list(seen.values())


def extract_ips(pdf_bytes: bytes) -> EmbeddedFile:
    for f in extract_embedded(pdf_bytes):
        if f.relationship == "Source" and f.mime_type == "application/fhir+json":
            return f
    raise LookupError("no FHIR IPS Associated File with AFRelationship=Source found")


def page_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(p.extract_text() or "" for p in reader.pages)
