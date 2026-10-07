"""IHE XCPD (ITI-55 Cross Gateway Patient Discovery) - demonstrator subset.

HL7 V3 PRPA_IN201305UV02 query by demographics and PRPA_IN201306UV02
response. Supported parameters: living subject name (given/family), birth
time, administrative gender and the requester's own patient id. Only an
unambiguous exact match is returned; otherwise the response says NF (no
match). Not implemented: deferred mode, revoke, health data locator,
probabilistic matching, patient-identity feed, consent-gated discovery.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from lxml import etree

V3 = "urn:hl7-org:v3"
ITI55 = "urn:hl7-org:v3:PRPA_IN201305UV02:CrossGatewayPatientDiscovery"
ITI55_RESPONSE = "urn:hl7-org:v3:PRPA_IN201306UV02:CrossGatewayPatientDiscovery"
INTERACTION_OID = "2.16.840.1.113883.1.6"
GENDER_TO_V3 = {"female": "F", "male": "M", "other": "UN", "unknown": "UN"}
V3_TO_GENDER = {"F": "female", "M": "male"}


def v(local: str) -> str:
    return f"{{{V3}}}{local}"


@dataclass
class Demographics:
    family: str
    given: list[str]
    birth_date: str          # YYYY-MM-DD
    gender: str
    local_id_root: str = ""
    local_id_extension: str = ""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def _device(parent, type_code: str, oid: str) -> None:
    el = etree.SubElement(parent, v("receiver" if type_code == "RCV" else "sender"), typeCode=type_code)
    dev = etree.SubElement(el, v("device"), classCode="DEV", determinerCode="INSTANCE")
    etree.SubElement(dev, v("id"), root=oid)


def _header(root, interaction: str, sender_oid: str, receiver_oid: str) -> None:
    etree.SubElement(root, v("id"), root=str(uuid.uuid4()))
    etree.SubElement(root, v("creationTime"), value=_now())
    etree.SubElement(root, v("interactionId"), root=INTERACTION_OID, extension=interaction)
    etree.SubElement(root, v("processingCode"), code="P")
    etree.SubElement(root, v("processingModeCode"), code="T")
    etree.SubElement(root, v("acceptAckCode"), code="NE" if interaction.endswith("06UV02") else "AL")
    _device(root, "RCV", receiver_oid)
    _device(root, "SND", sender_oid)


def _param(plist, name: str, semantics: str):
    p = etree.SubElement(plist, v(name))
    return p, semantics


def build_request(d: Demographics, *, sender_oid: str, receiver_oid: str) -> etree._Element:
    root = etree.Element(v("PRPA_IN201305UV02"), nsmap={None: V3}, ITSVersion="XML_1.0")
    _header(root, "PRPA_IN201305UV02", sender_oid, receiver_oid)
    cap = etree.SubElement(root, v("controlActProcess"), classCode="CACT", moodCode="EVN")
    etree.SubElement(cap, v("code"), code="PRPA_TE201305UV02", codeSystem=INTERACTION_OID)
    qbp = etree.SubElement(cap, v("queryByParameter"))
    etree.SubElement(qbp, v("queryId"), root=str(uuid.uuid4()))
    etree.SubElement(qbp, v("statusCode"), code="new")
    etree.SubElement(qbp, v("responseModalityCode"), code="R")
    etree.SubElement(qbp, v("responsePriorityCode"), code="I")
    pl = etree.SubElement(qbp, v("parameterList"))

    g = etree.SubElement(pl, v("livingSubjectAdministrativeGender"))
    etree.SubElement(g, v("value"), code=GENDER_TO_V3.get(d.gender, "UN"))
    etree.SubElement(g, v("semanticsText")).text = "LivingSubject.administrativeGender"
    b = etree.SubElement(pl, v("livingSubjectBirthTime"))
    etree.SubElement(b, v("value"), value=d.birth_date.replace("-", ""))
    etree.SubElement(b, v("semanticsText")).text = "LivingSubject.birthTime"
    if d.local_id_extension:
        i = etree.SubElement(pl, v("livingSubjectId"))
        etree.SubElement(i, v("value"), root=d.local_id_root, extension=d.local_id_extension)
        etree.SubElement(i, v("semanticsText")).text = "LivingSubject.id"
    n = etree.SubElement(pl, v("livingSubjectName"))
    nv = etree.SubElement(n, v("value"))
    for given in d.given:
        etree.SubElement(nv, v("given")).text = given
    etree.SubElement(nv, v("family")).text = d.family
    etree.SubElement(n, v("semanticsText")).text = "LivingSubject.name"
    return root


def parse_request(root: etree._Element) -> tuple[Demographics, str]:
    if etree.QName(root).localname != "PRPA_IN201305UV02":
        raise ValueError("expected PRPA_IN201305UV02")
    pl = root.find(f"{v('controlActProcess')}/{v('queryByParameter')}/{v('parameterList')}")
    if pl is None:
        raise ValueError("missing parameterList")
    name = pl.find(f"{v('livingSubjectName')}/{v('value')}")
    bt = pl.find(f"{v('livingSubjectBirthTime')}/{v('value')}")
    gen = pl.find(f"{v('livingSubjectAdministrativeGender')}/{v('value')}")
    lid = pl.find(f"{v('livingSubjectId')}/{v('value')}")
    if name is None or bt is None:
        raise ValueError("livingSubjectName and livingSubjectBirthTime are required by this responder")
    raw = bt.get("value", "")
    d = Demographics(
        family=(name.findtext(v("family")) or "").strip(),
        given=[(g.text or "").strip() for g in name.findall(v("given"))],
        birth_date=f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) >= 8 else raw,
        gender=V3_TO_GENDER.get(gen.get("code"), "unknown") if gen is not None else "unknown",
        local_id_root=lid.get("root", "") if lid is not None else "",
        local_id_extension=lid.get("extension", "") if lid is not None else "")
    query_id = root.find(f"{v('controlActProcess')}/{v('queryByParameter')}/{v('queryId')}")
    return d, (query_id.get("root", "") if query_id is not None else "")


def build_response(request: etree._Element, match: dict | None, *, sender_oid: str, receiver_oid: str,
                   home_community_id: str) -> etree._Element:
    root = etree.Element(v("PRPA_IN201306UV02"), nsmap={None: V3}, ITSVersion="XML_1.0")
    _header(root, "PRPA_IN201306UV02", sender_oid, receiver_oid)
    ack = etree.SubElement(root, v("acknowledgement"))
    etree.SubElement(ack, v("typeCode"), code="AA")
    req_id = request.find(v("id"))
    etree.SubElement(ack, v("targetMessage")).append(
        etree.Element(v("id"), root=req_id.get("root", "") if req_id is not None else ""))
    cap = etree.SubElement(root, v("controlActProcess"), classCode="CACT", moodCode="EVN")
    etree.SubElement(cap, v("code"), code="PRPA_TE201306UV02", codeSystem=INTERACTION_OID)
    if match:
        subj = etree.SubElement(cap, v("subject"), typeCode="SUBJ")
        reg = etree.SubElement(subj, v("registrationEvent"), classCode="REG", moodCode="EVN")
        etree.SubElement(reg, v("statusCode"), code="active")
        s1 = etree.SubElement(reg, v("subject1"), typeCode="SBJ")
        pat = etree.SubElement(s1, v("patient"), classCode="PAT")
        etree.SubElement(pat, v("id"), root=match["root"], extension=match["extension"])
        etree.SubElement(pat, v("statusCode"), code="active")
        person = etree.SubElement(pat, v("patientPerson"), classCode="PSN", determinerCode="INSTANCE")
        nm = etree.SubElement(person, v("name"))
        for g in match["given"]:
            etree.SubElement(nm, v("given")).text = g
        etree.SubElement(nm, v("family")).text = match["family"]
        etree.SubElement(person, v("administrativeGenderCode"), code=GENDER_TO_V3.get(match["gender"], "UN"))
        etree.SubElement(person, v("birthTime"), value=match["birthDate"].replace("-", ""))
        cust = etree.SubElement(reg, v("custodian"), typeCode="CST")
        ae = etree.SubElement(cust, v("assignedEntity"), classCode="ASSIGNED")
        etree.SubElement(ae, v("id"), root=home_community_id.removeprefix("urn:oid:"))
    qa = etree.SubElement(cap, v("queryAck"))
    qid = request.find(f"{v('controlActProcess')}/{v('queryByParameter')}/{v('queryId')}")
    if qid is not None:
        etree.SubElement(qa, v("queryId"), root=qid.get("root", ""))
    etree.SubElement(qa, v("queryResponseCode"), code="OK" if match else "NF")
    etree.SubElement(qa, v("resultTotalQuantity"), value="1" if match else "0")
    qbp = request.find(f"{v('controlActProcess')}/{v('queryByParameter')}")
    if qbp is not None:
        cap.append(etree.fromstring(etree.tostring(qbp)))
    return root


def parse_response(root: etree._Element) -> dict:
    code_el = root.find(f"{v('controlActProcess')}/{v('queryAck')}/{v('queryResponseCode')}")
    out = {"queryResponseCode": code_el.get("code") if code_el is not None else "?", "patient": None,
           "custodian": None}
    pat = root.find(f"{v('controlActProcess')}/{v('subject')}/{v('registrationEvent')}/{v('subject1')}/{v('patient')}")
    if pat is not None:
        pid = pat.find(v("id"))
        out["patient"] = {"root": pid.get("root"), "extension": pid.get("extension")}
        cust = root.find(f".//{v('custodian')}/{v('assignedEntity')}/{v('id')}")
        out["custodian"] = cust.get("root") if cust is not None else None
    return out
