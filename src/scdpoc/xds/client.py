"""XDS.b Document Source and Document Consumer (SOAP clients).

The demo orchestration uses these to talk to the Repository and Registry
over HTTP exactly as an external system would, so the publish / query /
retrieve steps exercise the real SOAP endpoints rather than internal calls.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx
from lxml import etree

from .ebrim import build_submit_objects_request, parse_document_entry
from .model import (
    ITI18,
    ITI41,
    ITI43,
    ITI57,
    NS,
    RESPONSE_SUCCESS,
    SQ_FIND_DOCUMENTS,
    SQ_GET_RELATED,
    Association,
    DocumentEntry,
    Submission,
)
from .soap import build_mtom, envelope, parse_message, to_bytes, xop_include

# transport(url, body, content_type) -> (status_code, content_type, body)
Transport = Callable[[str, bytes, str], tuple[int, str, bytes]]


def httpx_transport(timeout: float = 60.0) -> Transport:
    def post(url: str, body: bytes, ctype: str) -> tuple[int, str, bytes]:
        r = httpx.post(url, content=body, headers={"Content-Type": ctype}, timeout=timeout)
        return r.status_code, r.headers.get("content-type", ""), r.content
    return post


def q(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


@dataclass
class Exchange:
    """A request/response pair kept for display in the demonstrator UI."""
    transaction: str
    request_xml: str
    response_xml: str
    status: str
    errors: list[dict]


def _pretty(el: etree._Element) -> str:
    return etree.tostring(el, pretty_print=True, encoding="unicode")


def _errors(el: etree._Element) -> list[dict]:
    return [{"code": e.get("errorCode"), "context": e.get("codeContext"), "location": e.get("location")}
            for e in el.iter(q("rs", "RegistryError"))]


class XdsClient:
    def __init__(self, repository_url: str, registry_url: str, transport: Transport | None = None):
        self.repository_url = repository_url
        self.registry_url = registry_url
        self.transport = transport or httpx_transport()

    def _call(self, url: str, body: bytes, ctype: str):
        status, rctype, rbody = self.transport(url, body, ctype)
        msg = parse_message(rbody, rctype)
        return status, msg

    # ITI-41 ----------------------------------------------------------------
    def provide_and_register(self, sub: Submission) -> Exchange:
        req = etree.Element(q("xds", "ProvideAndRegisterDocumentSetRequest"), nsmap={"xds": NS["xds"]})
        req.append(build_submit_objects_request(sub))
        parts = {}
        for i, de in enumerate(sub.documents):
            cid = f"document{i}@scdpoc"
            d = etree.SubElement(req, q("xds", "Document"), id=de.entry_uuid)
            xop_include(d, cid)
            parts[cid] = (sub.contents[de.entry_uuid], de.mime_type)
        env = envelope(ITI41, req, to=self.repository_url)
        body, ctype = build_mtom(env, ITI41, parts)
        _, msg = self._call(self.repository_url, body, ctype)
        resp = msg.body
        return Exchange("ITI-41", _pretty(env), _pretty(resp), resp.get("status", ""), _errors(resp))

    # ITI-57 ----------------------------------------------------------------
    def update_document_set(self, sub: Submission) -> Exchange:
        """XDS Metadata Update (Document Administrator): SubmissionSet plus UpdateAvailabilityStatus
        associations, sent to the registry."""
        req = build_submit_objects_request(sub)
        env = envelope(ITI57, req, to=self.registry_url)
        _, msg = self._call(self.registry_url, to_bytes(env), "application/soap+xml; charset=UTF-8")
        resp = msg.body
        return Exchange("ITI-57", _pretty(env), _pretty(resp), resp.get("status", ""), _errors(resp))

    # ITI-18 ----------------------------------------------------------------
    def _stored_query(self, query_id: str, params: dict[str, list[str]], leaf: bool = True):
        req = etree.Element(q("query", "AdhocQueryRequest"), nsmap={"query": NS["query"], "rim": NS["rim"]})
        etree.SubElement(req, q("query", "ResponseOption"), returnComposedObjects="true",
                         returnType="LeafClass" if leaf else "ObjectRef")
        aq = etree.SubElement(req, q("rim", "AdhocQuery"), id=query_id)
        for name, values in params.items():
            s = etree.SubElement(aq, q("rim", "Slot"), name=name)
            vl = etree.SubElement(s, q("rim", "ValueList"))
            for v in values:
                etree.SubElement(vl, q("rim", "Value")).text = v
        env = envelope(ITI18, req, to=self.registry_url)
        _, msg = self._call(self.registry_url, to_bytes(env), "application/soap+xml; charset=UTF-8")
        resp = msg.body
        docs = [parse_document_entry(eo) for eo in resp.iter(q("rim", "ExtrinsicObject"))]
        assocs = [Association(a.get("id"), a.get("associationType"), a.get("sourceObject"), a.get("targetObject"))
                  for a in resp.iter(q("rim", "Association"))]
        ex = Exchange("ITI-18", _pretty(req), _pretty(resp), resp.get("status", ""), _errors(resp))
        return docs, assocs, ex

    def find_documents(self, patient_cx: str, statuses: list[str],
                       format_codes: list[str] | None = None) -> tuple[list[DocumentEntry], Exchange]:
        params = {"$XDSDocumentEntryPatientId": [f"'{patient_cx}'"],
                  "$XDSDocumentEntryStatus": ["(" + ",".join(f"'{s}'" for s in statuses) + ")"]}
        if format_codes:
            params["$XDSDocumentEntryFormatCode"] = ["(" + ",".join(f"'{f}'" for f in format_codes) + ")"]
        docs, _, ex = self._stored_query(SQ_FIND_DOCUMENTS, params)
        return docs, ex

    def get_related(self, entry_uuid: str, assoc_types: list[str]):
        params = {"$XDSDocumentEntryEntryUUID": [f"'{entry_uuid}'"],
                  "$AssociationTypes": ["(" + ",".join(f"'{t}'" for t in assoc_types) + ")"]}
        return self._stored_query(SQ_GET_RELATED, params)

    # ITI-43 ----------------------------------------------------------------
    def retrieve(self, repository_unique_id: str, document_unique_id: str) -> tuple[bytes | None, str, Exchange]:
        req = etree.Element(q("xds", "RetrieveDocumentSetRequest"), nsmap={"xds": NS["xds"]})
        dr = etree.SubElement(req, q("xds", "DocumentRequest"))
        etree.SubElement(dr, q("xds", "RepositoryUniqueId")).text = repository_unique_id
        etree.SubElement(dr, q("xds", "DocumentUniqueId")).text = document_unique_id
        env = envelope(ITI43, req, to=self.repository_url)
        _, msg = self._call(self.repository_url, to_bytes(env), "application/soap+xml; charset=UTF-8")
        resp = msg.body
        d = resp.find("xds:DocumentResponse", NS)
        data = msg.binary(d.find("xds:Document", NS)) if d is not None else None
        mime = d.findtext("xds:mimeType", "", NS) if d is not None else ""
        status = resp.find("rs:RegistryResponse", NS).get("status", "")
        return data, mime, Exchange("ITI-43", _pretty(req), _pretty(resp), status, _errors(resp))


def ok(ex: Exchange) -> bool:
    return ex.status == RESPONSE_SUCCESS
