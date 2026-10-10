"""Jurisdiction A responding gateway: ITI-55 (XCPD), ITI-38 and ITI-39 (XCA).

The gateway fronts Jurisdiction A's registry and repository for other
communities and enforces the home-community policy from
config/crossborder/communities.yml: only current documents carrying the IPS
format code are discoverable or retrievable across the border. The PDF/A
preservation envelope never leaves the home community - which makes the
PoC's central claim ("the preservation container is not the cross-border
protocol") an enforced rule rather than a convention.

Demonstrator subset; not tested at an IHE Connectathon.
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response
from lxml import etree

from ..config import Settings
from ..safety import list_fixtures, load_fixture
from ..xds.actors import Registry, Repository, parse_list, status_values
from ..xds.ebrim import association_xml, document_entry_xml
from ..xds.endpoints import PARTIAL, SOAP_CT, _errors, _fault
from ..xds.model import (
    ASSOC_RPLC,
    NS,
    RESPONSE_FAILURE,
    RESPONSE_SUCCESS,
    SQ_FIND_DOCUMENTS,
    SQ_GET_DOCUMENTS,
    SQ_GET_RELATED,
    XdsError,
)
from ..xds.soap import build_mtom, envelope, parse_message, to_bytes, xop_include
from . import xcpd

ITI38 = "urn:ihe:iti:2007:CrossGatewayQuery"
ITI39 = "urn:ihe:iti:2007:CrossGatewayRetrieve"


def q(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def find_match(settings: Settings, d: xcpd.Demographics) -> tuple[dict | None, str]:
    """Exact, unambiguous demographic match against Jurisdiction A's synthetic population."""
    ad = settings.affinity_domain["affinity_domain"]["patient_assigning_authority"]
    hits = []
    for f in list_fixtures(settings):
        p = load_fixture(settings, f["key"])["patient"]
        if (_norm(p["family"]) == _norm(d.family) and p["birthDate"] == d.birth_date
                and (d.gender == "unknown" or p["gender"] == d.gender)
                and d.given and _norm(p["given"][0]) == _norm(d.given[0])):
            hits.append({"root": ad["oid"], "extension": p["identifier"], "family": p["family"],
                         "given": p["given"], "gender": p["gender"], "birthDate": p["birthDate"]})
    if len(hits) == 1:
        return hits[0], "exact match on family name, first given name, birth date and administrative gender"
    if len(hits) > 1:
        return None, "ambiguous - more than one candidate; no identity disclosed"
    return None, "no candidate"


def build_router(settings: Settings, registry: Registry, repository: Repository) -> APIRouter:
    comm = settings.communities
    home = comm["home"]
    policy = comm["gateway_policy"]
    hcid = home["home_community_id"]
    prefix = home["gateway_path"]
    router = APIRouter(prefix=prefix, tags=["Jurisdiction A responding gateway (IHE XCPD / XCA, simulated)"])

    def exposed(de) -> bool:
        """Content may be released (XB-01, XB-02.a): the projection format, and current."""
        return de.codes["formatCode"].code in policy["exposed_format_codes"] and \
            de.status in policy["exposed_statuses"]

    def metadata_visible(de) -> bool:
        """Metadata by identifier (XB-02.b): a receiver holding a non-current issuance can learn its status."""
        return de.codes["formatCode"].code in policy["exposed_format_codes"]

    @router.post("/xcpd", summary="ITI-55 Cross Gateway Patient Discovery (subset)")
    async def xcpd_endpoint(request: Request) -> Response:
        try:
            msg = parse_message(await request.body(), request.headers.get("content-type", ""))
        except Exception as exc:
            return _fault(f"Malformed SOAP message: {exc}")
        if msg.action != xcpd.ITI55:
            return _fault(f"Unsupported action {msg.action!r} on XCPD endpoint")
        try:
            demo, _ = xcpd.parse_request(msg.body)
        except ValueError as exc:
            return _fault(str(exc))
        match, reason = find_match(settings, demo)
        sender = msg.body.find(f"{xcpd.v('sender')}/{xcpd.v('device')}/{xcpd.v('id')}")
        resp = xcpd.build_response(msg.body, match, sender_oid=home["device_oid"],
                                   receiver_oid=sender.get("root", "") if sender is not None else "",
                                   home_community_id=hcid)
        registry.audit.record("gateway-A", "ITI-55 patient discovery", matched=match is not None, reason=reason,
                              requester_local_id=demo.local_id_extension)
        env = envelope(xcpd.ITI55_RESPONSE, resp, relates_to=msg.message_id)
        return Response(to_bytes(env), media_type=SOAP_CT)

    @router.post("/xca", summary="ITI-38 Cross Gateway Query and ITI-39 Cross Gateway Retrieve (subset)")
    async def xca_endpoint(request: Request) -> Response:
        try:
            msg = parse_message(await request.body(), request.headers.get("content-type", ""))
        except Exception as exc:
            return _fault(f"Malformed SOAP/MTOM message: {exc}")
        if msg.action == ITI38:
            return _iti38(msg)
        if msg.action == ITI39:
            return _iti39(msg)
        return _fault(f"Unsupported action {msg.action!r} on XCA endpoint")

    def _iti38(msg) -> Response:
        resp = etree.Element(q("query", "AdhocQueryResponse"),
                             nsmap={"query": NS["query"], "rim": NS["rim"], "rs": NS["rs"]})
        rol = etree.SubElement(resp, q("rim", "RegistryObjectList"))
        try:
            aq = msg.body.find("rim:AdhocQuery", NS)
            params = {s.get("name"): [x.text or "" for x in s.findall("rim:ValueList/rim:Value", NS)]
                      for s in aq.findall("rim:Slot", NS)}
            if aq.get("id") == SQ_FIND_DOCUMENTS:
                pid = parse_list(params.get("$XDSDocumentEntryPatientId", []))
                if not pid:
                    raise XdsError("XDSStoredQueryParamNumber", "$XDSDocumentEntryPatientId is required")
                statuses = status_values(params.get("$XDSDocumentEntryStatus", []))
                docs = registry.find_documents(pid[0], statuses or policy["exposed_statuses"])
            elif aq.get("id") == SQ_GET_DOCUMENTS:
                docs = registry.get_documents(unique_ids=parse_list(params.get("$XDSDocumentEntryUniqueId", [])))
            elif aq.get("id") == SQ_GET_RELATED:
                uid = parse_list(params.get("$XDSDocumentEntryUniqueId", []))
                hit = registry.get_documents(unique_ids=uid)
                docs, assocs = registry.get_related(hit[0].entry_uuid, [ASSOC_RPLC]) if hit else ([], [])
                docs = [d for d in docs if metadata_visible(d)]
                ids = {d.entry_uuid for d in docs}
                for a in assocs:
                    if a.source in ids and a.target in ids:
                        rol.append(association_xml(a))
            else:
                raise XdsError("XDSUnknownStoredQuery", "only FindDocuments, GetDocuments and GetRelatedDocuments "
                                                        "cross the gateway")
            by_id = aq.get("id") in (SQ_GET_DOCUMENTS, SQ_GET_RELATED)
            shown = [d for d in docs if (metadata_visible(d) if by_id else exposed(d))]
            for d in shown:
                eo = document_entry_xml(d, include_status=True)
                eo.set("home", hcid)
                rol.insert(0, eo)
            resp.set("status", RESPONSE_SUCCESS)
            registry.audit.record("gateway-A", "ITI-38 cross gateway query", returned=len(shown),
                                  withheld_by_policy=len(docs) - len(shown))
        except XdsError as exc:
            resp.set("status", RESPONSE_FAILURE)
            _errors(resp, [exc])
        except Exception as exc:
            return _fault(f"Malformed cross gateway query: {exc}")
        env = envelope(ITI38 + "Response", resp, relates_to=msg.message_id)
        return Response(to_bytes(env), media_type=SOAP_CT)

    def _iti39(msg) -> Response:
        resp = etree.Element(q("xds", "RetrieveDocumentSetResponse"), nsmap={"xds": NS["xds"], "rs": NS["rs"]})
        rr = etree.SubElement(resp, q("rs", "RegistryResponse"))
        errors, parts, found = [], {}, 0
        for i, dr in enumerate(msg.body.findall("xds:DocumentRequest", NS)):
            home_id = (dr.findtext("xds:HomeCommunityId", "", NS) or "").strip()
            repo_id = (dr.findtext("xds:RepositoryUniqueId", "", NS) or "").strip()
            doc_id = (dr.findtext("xds:DocumentUniqueId", "", NS) or "").strip()
            if home_id != hcid:
                errors.append(XdsError("XDSUnknownCommunity", f"home community {home_id!r} is not served here", doc_id))
                continue
            de = registry.store.get_by_unique_id(doc_id)
            if de is None or not exposed(de):
                why = ("not available for cross-border exchange: the preservation envelope and superseded "
                       "documents stay in the home community") if de is not None else "unknown document"
                errors.append(XdsError("XDSDocumentUniqueIdError", f"document {doc_id} {why}", doc_id))
                continue
            got = repository.retrieve(repo_id, doc_id)
            if got is None:
                errors.append(XdsError("XDSUnknownRepositoryId", f"repository {repo_id} unknown", doc_id))
                continue
            data, mime = got
            found += 1
            cid = f"xdoc{i}@scdpoc"
            parts[cid] = (data, mime)
            d = etree.SubElement(resp, q("xds", "DocumentResponse"))
            etree.SubElement(d, q("xds", "HomeCommunityId")).text = hcid
            etree.SubElement(d, q("xds", "RepositoryUniqueId")).text = repo_id
            etree.SubElement(d, q("xds", "DocumentUniqueId")).text = doc_id
            etree.SubElement(d, q("xds", "mimeType")).text = mime
            xop_include(etree.SubElement(d, q("xds", "Document")), cid)
        rr.set("status", RESPONSE_SUCCESS if not errors else (PARTIAL if found else RESPONSE_FAILURE))
        _errors(rr, errors)
        registry.audit.record("gateway-A", "ITI-39 cross gateway retrieve", delivered=found,
                              delivered_ids=[p for p in (d.findtext("xds:DocumentUniqueId", "", NS)
                                                         for d in resp.findall("xds:DocumentResponse", NS))],
                              refused=[e.context for e in errors])
        env = envelope(ITI39 + "Response", resp, relates_to=msg.message_id)
        body, ctype = build_mtom(env, ITI39 + "Response", parts)
        return Response(body, media_type=ctype)

    return router
