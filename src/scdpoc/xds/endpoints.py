"""SOAP endpoints for the XDS.b Repository (ITI-41, ITI-43) and Registry (ITI-18)."""
from __future__ import annotations

from fastapi import APIRouter, Request, Response
from lxml import etree

from .actors import Registry, Repository, parse_list, status_values
from .ebrim import association_xml, document_entry_xml, parse_submit_objects_request
from .model import (
    ASSOC_RPLC,
    ASSOC_XFRM,
    ITI18,
    ITI41,
    ITI43,
    ITI57,
    NS,
    RESPONSE_FAILURE,
    RESPONSE_SUCCESS,
    SEVERITY_ERROR,
    SQ_FIND_DOCUMENTS,
    SQ_GET_DOCUMENTS,
    SQ_GET_RELATED,
    XdsError,
)
from .soap import build_mtom, envelope, fault, parse_message, to_bytes, xop_include

SOAP_CT = "application/soap+xml; charset=UTF-8"
PARTIAL = "urn:ihe:iti:2007:ResponseStatusType:PartialSuccess"


def q(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


def _registry_response(errors: list[XdsError] | None = None, status: str | None = None) -> etree._Element:
    rr = etree.Element(q("rs", "RegistryResponse"), nsmap={"rs": NS["rs"]})
    rr.set("status", status or (RESPONSE_FAILURE if errors else RESPONSE_SUCCESS))
    _errors(rr, errors)
    return rr


def _errors(parent, errors) -> None:
    if errors:
        lst = etree.Element(q("rs", "RegistryErrorList"))
        parent.insert(0, lst)   # schema order: RegistryErrorList precedes RegistryObjectList
        for e in errors:
            etree.SubElement(lst, q("rs", "RegistryError"), errorCode=e.code, codeContext=e.message,
                             location=e.context or "", severity=SEVERITY_ERROR)


def _soap(el: etree._Element, action: str, relates_to: str) -> Response:
    return Response(to_bytes(envelope(action + "Response", el, relates_to=relates_to)), media_type=SOAP_CT)


def _fault(reason: str, status: int = 400) -> Response:
    return Response(to_bytes(fault(reason, "Sender" if status < 500 else "Receiver")), status_code=status,
                    media_type=SOAP_CT)


def build_router(repository: Repository, registry: Registry) -> APIRouter:
    router = APIRouter(tags=["IHE XDS.b (SOAP)"])

    @router.post("/xds/repository", summary="XDS.b Document Repository: ITI-41 and ITI-43 (SOAP 1.2, MTOM)")
    async def repository_endpoint(request: Request) -> Response:
        try:
            msg = parse_message(await request.body(), request.headers.get("content-type", ""))
        except Exception as exc:
            return _fault(f"Malformed SOAP/MTOM message: {exc}")
        if msg.action == ITI41:
            return _iti41(msg)
        if msg.action == ITI43:
            return _iti43(msg)
        return _fault(f"Unsupported action {msg.action!r} on repository endpoint")

    def _iti41(msg) -> Response:
        try:
            req = msg.body
            if etree.QName(req).localname != "ProvideAndRegisterDocumentSetRequest":
                raise XdsError("XDSRepositoryError", "expected ProvideAndRegisterDocumentSetRequest")
            sor = req.find("lcm:SubmitObjectsRequest", NS)
            if sor is None:
                raise XdsError("XDSRepositoryMetadataError", "missing SubmitObjectsRequest")
            sub = parse_submit_objects_request(sor)
            for d in req.findall("xds:Document", NS):
                sub.contents[d.get("id")] = msg.binary(d)
            repository.provide_and_register(sub)
            rr = _registry_response()
        except XdsError as exc:
            repository.audit.record("repository", "ITI-41 rejected", code=exc.code, message=exc.message)
            rr = _registry_response([exc])
        return _soap(rr, ITI41, msg.message_id)

    def _iti43(msg) -> Response:
        req = msg.body
        resp = etree.Element(q("xds", "RetrieveDocumentSetResponse"), nsmap={"xds": NS["xds"], "rs": NS["rs"]})
        rr = etree.SubElement(resp, q("rs", "RegistryResponse"))
        errors, parts, found = [], {}, 0
        requests = req.findall("xds:DocumentRequest", NS)
        for i, dr in enumerate(requests):
            repo_id = (dr.findtext("xds:RepositoryUniqueId", "", NS) or "").strip()
            doc_id = (dr.findtext("xds:DocumentUniqueId", "", NS) or "").strip()
            got = repository.retrieve(repo_id, doc_id)
            if got is None:
                code = "XDSUnknownRepositoryId" if repo_id != repository.unique_id else "XDSDocumentUniqueIdError"
                errors.append(XdsError(code, f"document {doc_id} not available", doc_id))
                continue
            data, mime = got
            found += 1
            cid = f"doc{i}@scdpoc"
            parts[cid] = (data, mime)
            d = etree.SubElement(resp, q("xds", "DocumentResponse"))
            etree.SubElement(d, q("xds", "RepositoryUniqueId")).text = repo_id
            etree.SubElement(d, q("xds", "DocumentUniqueId")).text = doc_id
            etree.SubElement(d, q("xds", "mimeType")).text = mime
            xop_include(etree.SubElement(d, q("xds", "Document")), cid)
        rr.set("status", RESPONSE_SUCCESS if not errors else (PARTIAL if found else RESPONSE_FAILURE))
        _errors(rr, errors)
        env = envelope(ITI43 + "Response", resp, relates_to=msg.message_id)
        body, ctype = build_mtom(env, ITI43 + "Response", parts)
        return Response(body, media_type=ctype)

    @router.post("/xds/registry", summary="XDS.b Document Registry: ITI-18 Registry Stored Query and ITI-57 Update Document Set (SOAP 1.2)")
    async def registry_endpoint(request: Request) -> Response:
        try:
            msg = parse_message(await request.body(), request.headers.get("content-type", ""))
        except Exception as exc:
            return _fault(f"Malformed SOAP message: {exc}")
        if msg.action == ITI57:
            try:
                sor = msg.body if etree.QName(msg.body).localname == "SubmitObjectsRequest" else None
                if sor is None:
                    raise XdsError("XDSRegistryError", "expected SubmitObjectsRequest")
                registry.update_availability(parse_submit_objects_request(sor))
                rr = _registry_response()
            except XdsError as exc:
                registry.audit.record("registry", "ITI-57 rejected", code=exc.code, message=exc.message)
                rr = _registry_response([exc])
            return _soap(rr, ITI57, msg.message_id)
        if msg.action != ITI18:
            return _fault(f"Unsupported action {msg.action!r} on registry endpoint")
        resp = etree.Element(q("query", "AdhocQueryResponse"),
                             nsmap={"query": NS["query"], "rim": NS["rim"], "rs": NS["rs"]})
        rol = etree.SubElement(resp, q("rim", "RegistryObjectList"))
        try:
            aq = msg.body.find("rim:AdhocQuery", NS)
            ro = msg.body.find("query:ResponseOption", NS)
            leaf = ro is None or ro.get("returnType", "LeafClass") == "LeafClass"
            params = {s.get("name"): [v.text or "" for v in s.findall("rim:ValueList/rim:Value", NS)]
                      for s in aq.findall("rim:Slot", NS)}
            qid = aq.get("id")
            assocs = []
            if qid == SQ_FIND_DOCUMENTS:
                pid = parse_list(params.get("$XDSDocumentEntryPatientId", []))
                if not pid:
                    raise XdsError("XDSStoredQueryParamNumber", "$XDSDocumentEntryPatientId is required")
                statuses = status_values(params.get("$XDSDocumentEntryStatus", []))
                if not statuses:
                    raise XdsError("XDSStoredQueryParamNumber", "$XDSDocumentEntryStatus is required")
                fmts = [f.split("^^")[0] for f in parse_list(params.get("$XDSDocumentEntryFormatCode", []))]
                docs = registry.find_documents(pid[0], statuses, fmts or None)
            elif qid == SQ_GET_DOCUMENTS:
                docs = registry.get_documents(parse_list(params.get("$XDSDocumentEntryEntryUUID", [])),
                                              parse_list(params.get("$XDSDocumentEntryUniqueId", [])))
            elif qid == SQ_GET_RELATED:
                uuids = parse_list(params.get("$XDSDocumentEntryEntryUUID", []))
                if not uuids:
                    uid = parse_list(params.get("$XDSDocumentEntryUniqueId", []))
                    hit = registry.get_documents(unique_ids=uid)
                    uuids = [hit[0].entry_uuid] if hit else []
                types = parse_list(params.get("$AssociationTypes", [])) or [ASSOC_RPLC, ASSOC_XFRM]
                docs, assocs = registry.get_related(uuids[0], types) if uuids else ([], [])
            else:
                raise XdsError("XDSUnknownStoredQuery", f"stored query {qid} not supported by this demonstrator")
            for d in docs:
                if leaf:
                    rol.append(document_entry_xml(d, include_status=True))
                else:
                    etree.SubElement(rol, q("rim", "ObjectRef"), id=d.entry_uuid)
            for a in assocs:
                rol.append(association_xml(a) if leaf else etree.Element(q("rim", "ObjectRef"), id=a.entry_uuid))
            resp.set("status", RESPONSE_SUCCESS)
            registry.audit.record("registry", "ITI-18 query", query=qid, results=len(docs))
        except XdsError as exc:
            resp.set("status", RESPONSE_FAILURE)
            _errors(resp, [exc])
        except Exception as exc:
            return _fault(f"Malformed stored query: {exc}")
        return _soap(resp, ITI18, msg.message_id)

    return router
