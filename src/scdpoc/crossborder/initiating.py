"""Jurisdiction B initiating gateway: client side of ITI-55, ITI-38 and ITI-39."""
from __future__ import annotations

from lxml import etree

from ..xds.client import Exchange, _errors, _pretty
from ..xds.ebrim import parse_document_entry
from ..xds.model import NS, SQ_FIND_DOCUMENTS, SQ_GET_DOCUMENTS, SQ_GET_RELATED, Association, DocumentEntry
from ..xds.soap import envelope, parse_message, to_bytes
from . import xcpd
from .gateway import ITI38, ITI39

SOAP_CT = "application/soap+xml; charset=UTF-8"


def q(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


class InitiatingGateway:
    def __init__(self, gateway_base: str, post, sender_oid: str, receiver_oid: str):
        self.xcpd_url = f"{gateway_base}/xcpd"
        self.xca_url = f"{gateway_base}/xca"
        self.post = post
        self.sender_oid = sender_oid
        self.receiver_oid = receiver_oid

    def discover(self, d: xcpd.Demographics) -> tuple[dict, Exchange]:
        req = xcpd.build_request(d, sender_oid=self.sender_oid, receiver_oid=self.receiver_oid)
        env = envelope(xcpd.ITI55, req, to=self.xcpd_url)
        _, ctype, body = self.post(self.xcpd_url, to_bytes(env), SOAP_CT)
        msg = parse_message(body, ctype)
        result = xcpd.parse_response(msg.body)
        return result, Exchange("ITI-55", _pretty(req), _pretty(msg.body), result["queryResponseCode"], [])

    def find_documents(self, patient_cx: str, home_community_id: str,
                       statuses: list[str]) -> tuple[list[DocumentEntry], list[str], Exchange]:
        req = etree.Element(q("query", "AdhocQueryRequest"), nsmap={"query": NS["query"], "rim": NS["rim"]})
        etree.SubElement(req, q("query", "ResponseOption"), returnComposedObjects="true", returnType="LeafClass")
        aq = etree.SubElement(req, q("rim", "AdhocQuery"), id=SQ_FIND_DOCUMENTS, home=home_community_id)
        for name, value in [("$XDSDocumentEntryPatientId", f"'{patient_cx}'"),
                            ("$XDSDocumentEntryStatus", "(" + ",".join(f"'{s}'" for s in statuses) + ")")]:
            s = etree.SubElement(aq, q("rim", "Slot"), name=name)
            etree.SubElement(etree.SubElement(s, q("rim", "ValueList")), q("rim", "Value")).text = value
        env = envelope(ITI38, req, to=self.xca_url)
        _, ctype, body = self.post(self.xca_url, to_bytes(env), SOAP_CT)
        msg = parse_message(body, ctype)
        resp = msg.body
        eos = list(resp.iter(q("rim", "ExtrinsicObject")))
        docs = [parse_document_entry(eo) for eo in eos]
        homes = [eo.get("home", "") for eo in eos]
        return docs, homes, Exchange("ITI-38", _pretty(req), _pretty(resp), resp.get("status", ""), _errors(resp))

    def _query(self, query_id: str, home: str, params: list[tuple[str, str]]):
        req = etree.Element(q("query", "AdhocQueryRequest"), nsmap={"query": NS["query"], "rim": NS["rim"]})
        etree.SubElement(req, q("query", "ResponseOption"), returnComposedObjects="true", returnType="LeafClass")
        aq = etree.SubElement(req, q("rim", "AdhocQuery"), id=query_id, home=home)
        for name, value in params:
            s = etree.SubElement(aq, q("rim", "Slot"), name=name)
            etree.SubElement(etree.SubElement(s, q("rim", "ValueList")), q("rim", "Value")).text = value
        env = envelope(ITI38, req, to=self.xca_url)
        _, ctype, body = self.post(self.xca_url, to_bytes(env), SOAP_CT)
        resp = parse_message(body, ctype).body
        docs = [parse_document_entry(eo) for eo in resp.iter(q("rim", "ExtrinsicObject"))]
        assocs = [Association(a.get("id"), a.get("associationType"), a.get("sourceObject"), a.get("targetObject"))
                  for a in resp.iter(q("rim", "Association"))]
        return docs, assocs, Exchange("ITI-38", _pretty(req), _pretty(resp), resp.get("status", ""), _errors(resp))

    def get_documents(self, unique_id: str, home: str):
        """ITI-38 GetDocuments by uniqueId: metadata and availability status (XB-02.b)."""
        docs, _, ex = self._query(SQ_GET_DOCUMENTS, home, [("$XDSDocumentEntryUniqueId", f"('{unique_id}')")])
        return docs, ex

    def get_related(self, unique_id: str, home: str):
        """ITI-38 GetRelatedDocuments (RPLC): a successor means replaced; none means withdrawn (state mapping)."""
        return self._query(SQ_GET_RELATED, home, [("$XDSDocumentEntryUniqueId", f"('{unique_id}')"),
                                                  ("$AssociationTypes", "('urn:ihe:iti:2007:AssociationType:RPLC')")])

    def retrieve(self, home_community_id: str, repository_unique_id: str,
                 document_unique_id: str) -> tuple[bytes | None, str, Exchange]:
        req = etree.Element(q("xds", "RetrieveDocumentSetRequest"), nsmap={"xds": NS["xds"]})
        dr = etree.SubElement(req, q("xds", "DocumentRequest"))
        etree.SubElement(dr, q("xds", "HomeCommunityId")).text = home_community_id
        etree.SubElement(dr, q("xds", "RepositoryUniqueId")).text = repository_unique_id
        etree.SubElement(dr, q("xds", "DocumentUniqueId")).text = document_unique_id
        env = envelope(ITI39, req, to=self.xca_url)
        _, ctype, body = self.post(self.xca_url, to_bytes(env), SOAP_CT)
        msg = parse_message(body, ctype)
        resp = msg.body
        d = resp.find("xds:DocumentResponse", NS)
        data = msg.binary(d.find("xds:Document", NS)) if d is not None else None
        mime = d.findtext("xds:mimeType", "", NS) if d is not None else ""
        status = resp.find("rs:RegistryResponse", NS).get("status", "")
        return data, mime, Exchange("ITI-39", _pretty(req), _pretty(resp), status, _errors(resp))
