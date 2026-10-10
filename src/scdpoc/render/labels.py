"""Rendition labels. English defaults; other jurisdictions supply their own
(see config/crossborder/designations-*.yml). Clinical content is never in
here - only the fixed captions around it."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Labels:
    lang: str = "en-GB"
    banner: str = "SYNTHETIC DATA ONLY - DEMONSTRATOR, NOT FOR CLINICAL USE"
    patient: str = "Patient"
    identifier: str = "Identifier"
    dob: str = "Date of birth"
    sex: str = "Sex (administrative)"
    issued: str = "Issued"
    content_time: str = "Clinical content as of"
    author: str = "Author"
    custodian: str = "Custodian"
    document: str = "Document"
    replaces: str = "Replaces"
    status: str = "Document status"
    attester: str = "Attested by"
    assurance: str = "Assurance"
    structured_source: str = "Structured source"
    rendered_by: str = "Rendered by {renderer} from the embedded FHIR IPS Associated File (AFRelationship=Source)"
    page: str = "Page"

    @classmethod
    def from_config(cls, lang: str, cfg: dict) -> Labels:
        known = {k: v for k, v in cfg.items() if k in cls.__dataclass_fields__ and isinstance(v, str)}
        return cls(lang=lang, **known)


ENGLISH = Labels()
