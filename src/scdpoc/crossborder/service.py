"""Jurisdiction B: discover a visiting patient, pull their IPS across the
border, verify it, render it for a local clinician, and preserve what was
received and shown.

Every interaction with Jurisdiction A goes over HTTP to A's responding
gateway (ITI-55, ITI-38, ITI-39). Jurisdiction B never sees A's registry,
repository or preservation envelopes directly.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime

from ..config import Settings
from ..demo.service import DemoError, WorkStore
from ..ehds.adapter import SimulatedNcpAdapter
from ..integrity import sha256
from ..ips.validator import BuiltinIpsPreflight
from ..pdfa.extract import extract_ips, page_text
from ..pdfa.packager import attachment_name_for, build_envelope
from ..pdfa.preflight import preflight
from ..render.html import render_html
from ..render.pdf import render_pdf
from ..safety import SyntheticDataViolation, scan_for_real_identifiers
from . import xcpd
from .initiating import InitiatingGateway
from .translate import localise, missing_codes

IPS_FORMAT = "urn:ihe:pcc:ips:2020"
APPROVED = "urn:oasis:names:tc:ebxml-regrep:StatusType:Approved"
DEPRECATED = "urn:oasis:names:tc:ebxml-regrep:StatusType:Deprecated"


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class JurisdictionB:
    def __init__(self, settings: Settings, http, base_url: str, audit):
        self.settings = settings
        self.comm = settings.communities
        self.http = http
        self.audit = audit
        self.work = WorkStore(settings.data_dir / "jurisdiction-b")
        self.gw = InitiatingGateway(base_url.rstrip("/") + self.comm["home"]["gateway_path"], http.post,
                                    sender_oid=self.comm["consumer"]["device_oid"],
                                    receiver_oid=self.comm["home"]["device_oid"])
        self.designations = settings.designations
        self.lab = self.designations["labels"]

    # ------------------------------------------------------------ patients
    def _index(self) -> list[dict]:
        data = json.loads(self.settings.jurisdiction_b_patients_file.read_text(encoding="utf-8"))
        prefix = self.comm["consumer"]["patient_assigning_authority"]["value_prefix"]
        if data.get("synthetic") is not True:
            raise SyntheticDataViolation("Jurisdiction B index is not marked synthetic")
        for p in data["patients"]:
            if not p["identifier"].startswith(prefix):
                raise SyntheticDataViolation(f"Jurisdiction B identifier must start with {prefix}")
        if scan_for_real_identifiers(json.dumps(data)):
            raise SyntheticDataViolation("possible real identifiers in Jurisdiction B index")
        return data["patients"]

    def _patient(self, key: str) -> dict:
        for p in self._index():
            if p["key"] == key:
                return p
        raise DemoError(404, f"no Jurisdiction B patient {key}")

    def _state(self, key: str) -> dict:
        try:
            return self.work.load("patients", key, "state.json")
        except DemoError:
            return {}

    def _save_state(self, key: str, state: dict) -> None:
        self.work.save("patients", key, state, "state.json")

    def patients(self) -> list[dict]:
        out = []
        for p in self._index():
            st = self._state(p["key"])
            out.append({**p, "name": " ".join([*p["given"], p["family"]]), "link": st.get("link"),
                        "received": len(st.get("received", []))})
        return out

    # ---------------------------------------------------- B1 patient discovery
    def discover(self, key: str) -> dict:
        p = self._patient(key)
        aa = self.comm["consumer"]["patient_assigning_authority"]
        demo = xcpd.Demographics(family=p["family"], given=p["given"], birth_date=p["birthDate"],
                                 gender=p["gender"], local_id_root=aa["oid"], local_id_extension=p["identifier"])
        result, ex = self.gw.discover(demo)
        state = self._state(key)
        if result["patient"]:
            state["link"] = {"localId": p["identifier"], "remoteRoot": result["patient"]["root"],
                             "remoteId": result["patient"]["extension"], "homeCommunityId":
                             self.comm["home"]["home_community_id"], "linkedAt": _now().isoformat()}
        else:
            state.pop("link", None)
        self._save_state(key, state)
        self.audit.record("jurisdiction-B", "ITI-55 initiated", local=p["identifier"],
                          result=result["queryResponseCode"])
        return {"patient": p, "result": result, "link": state.get("link"), "exchange": asdict(ex)}

    # ------------------------------------------- B2 cross-gateway query + retrieve
    def exchange(self, key: str) -> dict:
        state = self._state(key)
        link = state.get("link")
        if not link:
            raise DemoError(409, "run patient discovery (ITI-55) first; no identity link to Jurisdiction A")
        cx = f"{link['remoteId']}^^^&{link['remoteRoot']}&ISO"
        # B deliberately asks for everything, current and superseded, in any format: the
        # responding gateway's home-community policy decides what crosses the border.
        docs, homes, q_ex = self.gw.find_documents(cx, link["homeCommunityId"], [APPROVED, DEPRECATED])
        if not docs:
            return {"query": asdict(q_ex), "documents": [], "message":
                    "No shared documents. Publish this patient's summary in Jurisdiction A first."}
        de = docs[0]
        data, mime, r_ex = self.gw.retrieve(homes[0] or link["homeCommunityId"], de.repository_unique_id,
                                            de.unique_id)
        if data is None:
            raise DemoError(502, f"ITI-39 failed: {r_ex.errors}")
        checks = self._verify(data, mime, de, link)
        received = {"uniqueId": de.unique_id, "receivedAt": _now().isoformat(), "bytes": len(data),
                    "sha256": sha256(data), "checksPassed": all(c["passed"] for c in checks),
                    "documentUrn": json.loads(data).get("identifier", {}).get("value")}
        if not self.work.exists("received", _safe(de.unique_id), "ips.json"):
            self.work.put_bytes("received", _safe(de.unique_id), "ips.json", data)
            self.work.save("received", _safe(de.unique_id), {**received, "metadata": asdict(de),
                                                           "verification": checks})
        state.setdefault("received", [])
        if de.unique_id not in [r["uniqueId"] for r in state["received"]]:
            state["received"].append(received)
        state["current"] = de.unique_id
        self._save_state(key, state)
        ncp = SimulatedNcpAdapter(self.settings).export(data).to_dict()
        self.audit.record("jurisdiction-B", "ITI-39 received", document=de.unique_id,
                          verified=received["checksPassed"])
        return {"query": asdict(q_ex), "documents": [{"uniqueId": d.unique_id, "mimeType": d.mime_type,
                                                      "formatCode": d.codes["formatCode"].code,
                                                      "status": d.status.rsplit(":", 1)[-1], "home": h,
                                                      "title": d.title} for d, h in zip(docs, homes, strict=True)],
                "requested": "all formats, current and superseded",
                "retrieve": asdict(r_ex), "received": received, "verification": checks, "ncpPivotCheck": ncp}

    def _verify(self, data: bytes, mime: str, de, link: dict) -> list[dict]:
        out = []

        def add(cid, label, ok, expected="", actual=""):
            out.append({"id": cid, "label": label, "passed": bool(ok), "expected": str(expected),
                        "actual": str(actual)})

        h = hashlib.sha1(data).hexdigest()
        add("VB-1", "SHA-1 equals the registry 'hash' advertised in ITI-38 metadata", h == de.hash, de.hash, h)
        add("VB-2", "Size equals the advertised 'size'", len(data) == de.size, de.size, len(data))
        add("VB-3", "Computable IPS received (application/fhir+json, IPS format code)",
            mime == "application/fhir+json" and de.codes["formatCode"].code == IPS_FORMAT,
            f"application/fhir+json, {IPS_FORMAT}", f"{mime}, {de.codes['formatCode'].code}")
        try:
            bundle = json.loads(data)
            pre = BuiltinIpsPreflight(self.settings).run(bundle)
            add("VB-4", "Received IPS passes B's structural pre-flight", pre.errors == 0, "0 errors",
                f"{pre.errors} errors")
            pids = [r["resource"]["identifier"][0]["value"] for r in bundle["entry"]
                    if r["resource"]["resourceType"] == "Patient"]
            add("VB-5", "IPS subject is the patient linked by ITI-55", pids == [link["remoteId"]],
                link["remoteId"], ", ".join(pids))
        except (ValueError, KeyError) as exc:
            add("VB-4", "Received IPS is parseable JSON", False, "valid IPS", str(exc))
        add("VB-6", "Document is current in the home community", de.status == APPROVED, "Approved",
            de.status.rsplit(":", 1)[-1])
        return out

    def _current(self, key: str) -> tuple[dict, bytes, dict]:
        state = self._state(key)
        uid = state.get("current")
        if not uid:
            raise DemoError(409, "nothing received yet; run the cross-gateway query and retrieve first")
        rec = self.work.load("received", _safe(uid))
        return state, self.work.get_bytes("received", _safe(uid), "ips.json"), rec

    def _meta(self, state: dict, rec: dict, report) -> list[tuple[str, str]]:
        link = state["link"]
        n_ok, n_all = len(report.translated), len(report.translated) + len(report.untranslated)
        return [(self.lab["local_identifier"], link["localId"]),
                (self.lab["provenance"], f"{self.comm['home']['name']} ({link['homeCommunityId']}) via XCA"),
                (self.lab["retrieved"], rec["receivedAt"]),
                (self.lab["coverage"], f"{n_ok}/{n_all}")]

    # ----------------------------------------------------- B3 local rendition
    def render(self, key: str) -> dict:
        state, data, rec = self._current(key)
        view, report, labels = localise(json.loads(data), self.designations)
        html = render_html(view, labels, self._meta(state, rec, report))
        return {"language": labels.lang, "html": html, "translation": report.to_dict(),
                "documentUrn": rec["documentUrn"],
                "note": "Rendition for a clinician in Jurisdiction B, generated from the received IPS. The legal "
                        "record remains Jurisdiction A's preserved envelope, which never crossed the border."}

    def html(self, key: str) -> str:
        return self.render(key)["html"]

    # ---------------------------------------- B4 preserve what was received/shown
    def preserve(self, key: str) -> dict:
        state, data, rec = self._current(key)
        rid = _safe(rec["uniqueId"])
        if self.work.exists("received", rid, "custody.json"):
            return self.work.load("received", rid, "custody.json")
        bundle = json.loads(data)
        view, report, labels = localise(bundle, self.designations)
        doc_id = bundle["id"]
        received_at = datetime.fromisoformat(rec["receivedAt"])
        pages = render_pdf(view, attachment_name_for(doc_id), labels, self._meta(state, rec, report))
        env = build_envelope(pages, data, document_id=doc_id, issued=received_at,
                             title=f"{view.title} - {labels.lang}", author=self.comm["consumer"]["name"],
                             subject=f"Received from {self.comm['home']['name']}; rendered in {labels.lang}")
        pf = preflight(env.pdf_bytes)
        emb = extract_ips(env.pdf_bytes)
        missing = missing_codes(view, page_text(env.pdf_bytes))
        out = {"uniqueId": rec["uniqueId"], "envelopeSha256": env.pdf_sha256, "bytes": len(env.pdf_bytes),
               "language": labels.lang,
               "checks": [
                   {"id": "CB-1", "label": "Embedded IPS byte-identical to what was received",
                    "passed": emb.data == data},
                   {"id": "CB-2", "label": "Every clinical code is visible in the local rendition",
                    "passed": not missing, "detail": ", ".join(missing)},
                   {"id": "CB-3", "label": "PDF/A-3b pre-flight (subset; veraPDF in CI)", "passed": pf.passed},
               ],
               "note": "Custody copy held by Jurisdiction B: what it received (embedded, AFRelationship=Source) "
                       "and what it showed its clinician (pages). It does not replace A's preserved record."}
        self.work.put_bytes("received", rid, "custody.pdf", env.pdf_bytes)
        self.work.save("received", rid, out, "custody.json")
        self.audit.record("jurisdiction-B", "custody copy preserved", document=rec["uniqueId"])
        return out

    def custody_pdf(self, key: str) -> bytes:
        _, _, rec = self._current(key)
        return self.work.get_bytes("received", _safe(rec["uniqueId"]), "custody.pdf")


def _safe(uid: str) -> str:
    return uid.replace("/", "_")

