"""ebRIM 3.0 serialisation of XDS.b metadata (build and parse).

Used by the Document Source (to build ITI-41 requests), by the Registry
(to parse submissions and answer ITI-18 queries) and by the Consumer.
"""
from __future__ import annotations

import uuid

from lxml import etree

from .model import (
    ASSOC_HAS_MEMBER,
    CODE_SCHEMES,
    DE_AUTHOR,
    DE_PATIENT_ID,
    DE_UNIQUE_ID,
    DOC_ENTRY_STABLE,
    NS,
    SS_AUTHOR,
    SS_CONTENT_TYPE,
    SS_PATIENT_ID,
    SS_SOURCE_ID,
    SS_UNIQUE_ID,
    STATUS_APPROVED,
    SUBMISSION_SET_NODE,
    Association,
    Code,
    DocumentEntry,
    Submission,
    SubmissionSet,
    XdsError,
)

RIM = NS["rim"]


def q(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


def _uuid() -> str:
    return f"urn:uuid:{uuid.uuid4()}"


def _slot(parent, name: str, values: list[str]) -> None:
    s = etree.SubElement(parent, q("rim", "Slot"), name=name)
    vl = etree.SubElement(s, q("rim", "ValueList"))
    for v in values:
        etree.SubElement(vl, q("rim", "Value")).text = v


def _name(parent, text: str) -> None:
    n = etree.SubElement(parent, q("rim", "Name"))
    etree.SubElement(n, q("rim", "LocalizedString"), value=text)


def _classification(parent, scheme: str, classified: str, node_rep: str, coding_scheme: str | None,
                    display: str | None) -> etree._Element:
    c = etree.SubElement(parent, q("rim", "Classification"), id=_uuid(), classificationScheme=scheme,
                         classifiedObject=classified, nodeRepresentation=node_rep, objectType=
                         "urn:oasis:names:tc:ebxml-regrep:ObjectType:RegistryObject:Classification")
    if coding_scheme is not None:
        _slot(c, "codingScheme", [coding_scheme])
    if display is not None:
        _name(c, display)
    return c


def _external_id(parent, scheme: str, reg_obj: str, value: str, label: str) -> None:
    e = etree.SubElement(parent, q("rim", "ExternalIdentifier"), id=_uuid(), registryObject=reg_obj,
                         identificationScheme=scheme, value=value, objectType=
                         "urn:oasis:names:tc:ebxml-regrep:ObjectType:RegistryObject:ExternalIdentifier")
    _name(e, label)


def document_entry_xml(de: DocumentEntry, include_status: bool = False) -> etree._Element:
    eo = etree.Element(q("rim", "ExtrinsicObject"), id=de.entry_uuid, mimeType=de.mime_type,
                       objectType=DOC_ENTRY_STABLE, nsmap={"rim": RIM})
    if include_status:
        eo.set("status", de.status)
    _slot(eo, "creationTime", [de.creation_time])
    _slot(eo, "languageCode", [de.language])
    if de.source_patient_id:
        _slot(eo, "sourcePatientId", [de.source_patient_id])
    if de.hash:
        _slot(eo, "hash", [de.hash])
    if de.size is not None:
        _slot(eo, "size", [str(de.size)])
    if de.repository_unique_id:
        _slot(eo, "repositoryUniqueId", [de.repository_unique_id])
    _name(eo, de.title)
    if de.comments:
        d = etree.SubElement(eo, q("rim", "Description"))
        etree.SubElement(d, q("rim", "LocalizedString"), value=de.comments)
    if de.author_institution or de.author_person:
        a = _classification(eo, DE_AUTHOR, de.entry_uuid, "", None, None)
        if de.author_person:
            _slot(a, "authorPerson", [de.author_person])
        if de.author_institution:
            _slot(a, "authorInstitution", [de.author_institution])
        if de.author_role:
            _slot(a, "authorRole", [de.author_role])
    for key, scheme in CODE_SCHEMES.items():
        c = de.codes.get(key)
        if c:
            _classification(eo, scheme, de.entry_uuid, c.code, c.scheme, c.display)
    _external_id(eo, DE_PATIENT_ID, de.entry_uuid, de.patient_id, "XDSDocumentEntry.patientId")
    _external_id(eo, DE_UNIQUE_ID, de.entry_uuid, de.unique_id, "XDSDocumentEntry.uniqueId")
    return eo


def submission_set_xml(ss: SubmissionSet) -> etree._Element:
    rp = etree.Element(q("rim", "RegistryPackage"), id=ss.entry_uuid, nsmap={"rim": RIM})
    _slot(rp, "submissionTime", [ss.submission_time])
    _name(rp, "Patient summary submission")
    if ss.author_institution or ss.author_person:
        a = _classification(rp, SS_AUTHOR, ss.entry_uuid, "", None, None)
        if ss.author_person:
            _slot(a, "authorPerson", [ss.author_person])
        if ss.author_institution:
            _slot(a, "authorInstitution", [ss.author_institution])
    _classification(rp, SS_CONTENT_TYPE, ss.entry_uuid, ss.content_type.code, ss.content_type.scheme,
                    ss.content_type.display)
    _external_id(rp, SS_UNIQUE_ID, ss.entry_uuid, ss.unique_id, "XDSSubmissionSet.uniqueId")
    _external_id(rp, SS_SOURCE_ID, ss.entry_uuid, ss.source_id, "XDSSubmissionSet.sourceId")
    _external_id(rp, SS_PATIENT_ID, ss.entry_uuid, ss.patient_id, "XDSSubmissionSet.patientId")
    return rp


def association_xml(a: Association) -> etree._Element:
    el = etree.Element(q("rim", "Association"), id=a.entry_uuid, associationType=a.type,
                       sourceObject=a.source, targetObject=a.target,
                       objectType="urn:oasis:names:tc:ebxml-regrep:ObjectType:RegistryObject:Association",
                       nsmap={"rim": RIM})
    for k, v in a.slots.items():
        _slot(el, k, v)
    return el


def build_submit_objects_request(sub: Submission) -> etree._Element:
    req = etree.Element(q("lcm", "SubmitObjectsRequest"), nsmap={"lcm": NS["lcm"], "rim": RIM})
    rol = etree.SubElement(req, q("rim", "RegistryObjectList"))
    for de in sub.documents:
        rol.append(document_entry_xml(de))
    rol.append(submission_set_xml(sub.submission_set))
    etree.SubElement(rol, q("rim", "Classification"), id=_uuid(), classifiedObject=sub.submission_set.entry_uuid,
                     classificationNode=SUBMISSION_SET_NODE,
                     objectType="urn:oasis:names:tc:ebxml-regrep:ObjectType:RegistryObject:Classification")
    for a in sub.associations:
        rol.append(association_xml(a))
    return req


# ------------------------------------------------------------------ parsing

def _slots(el) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for s in el.findall("rim:Slot", NS):
        out[s.get("name")] = [v.text or "" for v in s.findall("rim:ValueList/rim:Value", NS)]
    return out


def _name_of(el) -> str:
    ls = el.find("rim:Name/rim:LocalizedString", NS)
    return ls.get("value", "") if ls is not None else ""


def _ext_ids(el) -> dict[str, str]:
    return {e.get("identificationScheme"): e.get("value") for e in el.findall("rim:ExternalIdentifier", NS)}


def _first(slots: dict[str, list[str]], name: str, default: str = "") -> str:
    v = slots.get(name)
    return v[0] if v else default


def parse_document_entry(eo) -> DocumentEntry:
    s = _slots(eo)
    ext = _ext_ids(eo)
    codes: dict[str, Code] = {}
    author_inst = author_person = author_role = ""
    for c in eo.findall("rim:Classification", NS):
        scheme = c.get("classificationScheme")
        if scheme == DE_AUTHOR:
            cs = _slots(c)
            author_inst, author_person, author_role = (_first(cs, "authorInstitution"),
                                                       _first(cs, "authorPerson"), _first(cs, "authorRole"))
            continue
        for key, sch in CODE_SCHEMES.items():
            if sch == scheme:
                codes[key] = Code(c.get("nodeRepresentation", ""), _first(_slots(c), "codingScheme"),
                                  _name_of(c))
    desc = eo.find("rim:Description/rim:LocalizedString", NS)
    size = _first(s, "size")
    return DocumentEntry(
        entry_uuid=eo.get("id"), unique_id=ext.get(DE_UNIQUE_ID, ""), patient_id=ext.get(DE_PATIENT_ID, ""),
        mime_type=eo.get("mimeType", ""), title=_name_of(eo), creation_time=_first(s, "creationTime"),
        language=_first(s, "languageCode"), codes=codes, author_institution=author_inst,
        author_person=author_person, author_role=author_role, source_patient_id=_first(s, "sourcePatientId"),
        hash=_first(s, "hash"), size=int(size) if size else None,
        repository_unique_id=_first(s, "repositoryUniqueId"), status=eo.get("status", STATUS_APPROVED),
        comments=desc.get("value", "") if desc is not None else "")


def parse_submit_objects_request(req) -> Submission:
    rol = req.find("rim:RegistryObjectList", NS)
    if rol is None:
        raise XdsError("XDSRegistryMetadataError", "SubmitObjectsRequest has no RegistryObjectList")
    docs = [parse_document_entry(eo) for eo in rol.findall("rim:ExtrinsicObject", NS)]
    packages = rol.findall("rim:RegistryPackage", NS)
    ss_ids = {c.get("classifiedObject") for c in rol.findall("rim:Classification", NS)
              if c.get("classificationNode") == SUBMISSION_SET_NODE}
    for p in packages:
        for c in p.findall("rim:Classification", NS):
            if c.get("classificationNode") == SUBMISSION_SET_NODE:
                ss_ids.add(p.get("id"))
    ss_el = next((p for p in packages if p.get("id") in ss_ids), None)
    if ss_el is None:
        raise XdsError("XDSRegistryMetadataError", "no SubmissionSet RegistryPackage found")
    s = _slots(ss_el)
    ext = _ext_ids(ss_el)
    ct = next((c for c in ss_el.findall("rim:Classification", NS)
               if c.get("classificationScheme") == SS_CONTENT_TYPE), None)
    auth = next((c for c in ss_el.findall("rim:Classification", NS)
                 if c.get("classificationScheme") == SS_AUTHOR), None)
    ss = SubmissionSet(
        entry_uuid=ss_el.get("id"), unique_id=ext.get(SS_UNIQUE_ID, ""), source_id=ext.get(SS_SOURCE_ID, ""),
        patient_id=ext.get(SS_PATIENT_ID, ""), submission_time=_first(s, "submissionTime"),
        content_type=Code(ct.get("nodeRepresentation", ""), _first(_slots(ct), "codingScheme"), _name_of(ct))
        if ct is not None else Code("", ""),
        author_institution=_first(_slots(auth), "authorInstitution") if auth is not None else "",
        author_person=_first(_slots(auth), "authorPerson") if auth is not None else "")
    assocs = [Association(a.get("id"), a.get("associationType"), a.get("sourceObject"), a.get("targetObject"),
                          _slots(a)) for a in rol.findall("rim:Association", NS)]
    return Submission(ss, docs, assocs)


def has_member(ss_uuid: str, doc_uuid: str) -> Association:
    return Association(_uuid(), ASSOC_HAS_MEMBER, ss_uuid, doc_uuid, {"SubmissionSetStatus": ["Original"]})
