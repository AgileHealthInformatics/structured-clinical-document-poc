"""Tutorial orchestration for the end-to-end demonstrator journey.

These operations are deliberately convenient and are NOT interoperability
interfaces. Every standards interaction they trigger (ITI-41, ITI-18,
ITI-43, ITI-67, ITI-68) goes over HTTP to the real endpoints, so the UI
shows genuine request/response evidence.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import Settings
from ..ehds.adapter import ReadinessPreviewAdapter
from ..integrity import (
    checks_to_dict,
    sha1,
    sha256,
    tamper_embedded_payload,
    tamper_outer_bytes,
    verify_envelope,
)
from ..ips.composer import apply_revision, compose_ips
from ..ips.validator import validate_ips
from ..ips.view import build_view
from ..mhd.router import on_demand_tag
from ..pdfa.extract import extract_embedded, page_text
from ..pdfa.packager import attachment_name_for, build_envelope, envelope_name_for
from ..pdfa.preflight import preflight
from ..pdfa.verapdf import VeraPdf
from ..render.html import RENDERER_VERSION, render_html
from ..render.pdf import render_pdf
from ..safety import list_fixtures, load_fixture
from ..xds.actors import AffinityDomainPolicy, Registry, Repository
from ..xds.client import XdsClient
from ..xds.ebrim import has_member
from ..xds.model import (
    ASSOC_RPLC,
    ASSOC_XFRM,
    RESPONSE_SUCCESS,
    STATUS_APPROVED,
    STATUS_DEPRECATED,
    Association,
    Code,
    DocumentEntry,
    Submission,
    SubmissionSet,
)
from .http import Http


class DemoError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _dtm(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S")


def _oid_from_uuid(u: uuid.UUID) -> str:
    """ITU-T X.667 UUID-derived OID - globally unique without a registered arc."""
    return f"2.25.{u.int}"


class WorkStore:
    """File-backed records for drafts, packages and issuances (inspectable on disk)."""

    def __init__(self, root: Path):
        self.root = root
        for d in ("drafts", "packages", "patients"):
            (root / d).mkdir(parents=True, exist_ok=True)

    def path(self, kind: str, rid: str, name: str = "record.json") -> Path:
        p = self.root / kind / rid
        p.mkdir(parents=True, exist_ok=True)
        return p / name

    def save(self, kind: str, rid: str, obj: Any, name: str = "record.json") -> None:
        self.path(kind, rid, name).write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")

    def load(self, kind: str, rid: str, name: str = "record.json") -> Any:
        p = self.root / kind / rid / name
        if not p.is_file():
            raise DemoError(404, f"{kind[:-1]} {rid} not found")
        return json.loads(p.read_text(encoding="utf-8"))

    def put_bytes(self, kind: str, rid: str, name: str, data: bytes) -> None:
        p = self.path(kind, rid, name)
        if p.exists():
            raise DemoError(409, f"{name} already exists for {rid}; issued artefacts are immutable")
        p.write_bytes(data)

    def get_bytes(self, kind: str, rid: str, name: str) -> bytes:
        p = self.root / kind / rid / name
        if not p.is_file():
            raise DemoError(404, f"{name} not found for {rid}")
        return p.read_bytes()

    def exists(self, kind: str, rid: str, name: str = "record.json") -> bool:
        return (self.root / kind / rid / name).is_file()


class DemoService:
    def __init__(self, settings: Settings, registry: Registry, repository: Repository, http: Http):
        self.settings = settings
        self.registry = registry
        self.repository = repository
        self.http = http
        self.policy: AffinityDomainPolicy = registry.policy
        self.work = WorkStore(settings.data_dir / "work")
        base = (settings.xds_endpoint_base or settings.base_url).rstrip("/")
        self.base = base
        self.xds = XdsClient(f"{base}/xds/repository", f"{base}/xds/registry", transport=http.post)
        ad = settings.affinity_domain
        self.reps = {k: (v["mimeType"], Code.from_cfg(v["formatCode"])) for k, v in ad["representations"].items()}

    # ------------------------------------------------------------ helpers
    def _cx(self, source: dict) -> str:
        return self.policy.patient_cx(source["patient"]["identifier"])

    def _issuances(self, key: str) -> list[dict]:
        try:
            return self.work.load("patients", key, "issuances.json")
        except DemoError:
            return []

    def _patient_state(self, key: str) -> dict:
        try:
            return self.work.load("patients", key, "state.json")
        except DemoError:
            return {"revised": False}

    def _current_source(self, key: str) -> dict:
        src = load_fixture(self.settings, key)
        return apply_revision(src) if self._patient_state(key).get("revised") else src

    # --------------------------------------------------------- 1 patients
    def patients(self) -> list[dict]:
        out = []
        for p in list_fixtures(self.settings):
            iss = self._issuances(p["key"])
            p["issuedVersions"] = len(iss)
            p["currentVersion"] = iss[-1]["version"] if iss else None
            out.append(p)
        return out

    def patient(self, key: str) -> dict:
        src = load_fixture(self.settings, key)
        return {"source": src, "state": self._patient_state(key), "issuances": self._issuances(key)}

    # ---------------------------------------------------- 2-4 compose/validate/render
    def compose(self, key: str, revise: bool = False) -> dict:
        source = load_fixture(self.settings, key)
        issuances = self._issuances(key)
        if revise:
            if "revision" not in source:
                raise DemoError(400, "this synthetic patient has no revision scenario")
            if not issuances:
                raise DemoError(409, "publish version 1 before composing a replacement")
            source = apply_revision(source)
        elif self._patient_state(key).get("revised"):
            source = apply_revision(source)
        prev = issuances[-1] if issuances else None
        composed = compose_ips(source, self.settings, issued=_now(), version=(prev["version"] + 1) if prev else 1,
                               replaces_document_urn=prev["documentUrn"] if prev else None)
        report = validate_ips(composed.bundle, composed.json_bytes, self.settings)
        draft_id = str(composed.document_id)
        comp = composed.bundle["entry"][0]["resource"]
        record = {
            "draftId": draft_id, "patientKey": key, "version": composed.version, "revision": revise,
            "documentUrn": composed.document_urn, "seriesId": composed.series_id,
            "issued": composed.bundle["timestamp"], "replaces": prev["documentUrn"] if prev else None,
            "replacesEnvelopeEntry": prev["envelope"]["entryUUID"] if prev else None,
            "ips": {"bytes": len(composed.json_bytes), "sha256": sha256(composed.json_bytes),
                    "package": report.package},
            "profiles": sorted({p for e in composed.bundle["entry"]
                                for p in e["resource"].get("meta", {}).get("profile", [])}),
            "sections": [{"title": s["title"], "code": s["code"]["coding"][0]["code"],
                          "entries": len(s.get("entry", [])),
                          "emptyReason": (s.get("emptyReason") or {}).get("coding", [{}])[0].get("code")}
                         for s in comp["section"]],
            "terminology": sorted({c.get("system") for e in composed.bundle["entry"]
                                   for c in _codings(e["resource"]) if c.get("system")}),
            "validation": report.to_dict(),
        }
        if revise:
            self.work.save("patients", key, {"revised": True}, "state.json")
        self.work.put_bytes("drafts", draft_id, "ips.json", composed.json_bytes)
        self.work.save("drafts", draft_id, record)
        return {**record, "bundle": composed.json_bytes.decode("utf-8"),
                "html": render_html(build_view(composed.bundle)), "rendererVersion": RENDERER_VERSION}

    def draft(self, draft_id: str) -> dict:
        return self.work.load("drafts", draft_id)

    def draft_ips(self, draft_id: str) -> bytes:
        return self.work.get_bytes("drafts", draft_id, "ips.json")

    def draft_html(self, draft_id: str) -> str:
        return render_html(build_view(json.loads(self.draft_ips(draft_id))))

    # ------------------------------------------------------ 5-6 package + validate
    def package(self, draft_id: str) -> dict:
        draft = self.draft(draft_id)
        if not draft["validation"]["publishable"]:
            raise DemoError(409, "IPS validation gate not passed: " + draft["validation"]["gate"])
        if self.work.exists("packages", draft_id):
            return self.work.load("packages", draft_id)
        ips = self.draft_ips(draft_id)
        bundle = json.loads(ips)
        view = build_view(bundle)
        issued = datetime.strptime(bundle["timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        doc_id = bundle["id"]
        pages = render_pdf(view, attachment_name_for(doc_id))
        env = build_envelope(pages, ips, document_id=doc_id, issued=issued, title=view.title, author=view.custodian,
                             subject=f"International Patient Summary for {view.patient_name} ({view.patient_id})")
        pf = preflight(env.pdf_bytes)
        vera = VeraPdf(self.settings).validate(env.pdf_bytes)
        embedded = extract_embedded(env.pdf_bytes)
        ips_files = [f for f in embedded if f.relationship == "Source" and f.mime_type == "application/fhir+json"]
        identical = len(ips_files) == 1 and ips_files[0].data == ips
        text = re.sub(r"\s+", "", page_text(env.pdf_bytes))
        missing = [f for f in view.visible_facts() if re.sub(r"\s+", "", f) not in text]
        vera_ok = vera.status == "passed" or (vera.status == "not-run" and not self.settings.require_verapdf)
        publishable = pf.passed and identical and not missing and vera_ok
        gate = []
        if not pf.passed:
            gate.append("PDF/A pre-flight failed")
        if not identical:
            gate.append("embedded IPS is not byte-identical to the validated IPS")
        if missing:
            gate.append(f"{len(missing)} clinical facts missing from the rendition")
        if not vera_ok:
            gate.append(f"veraPDF {vera.status}")
        record = {
            "packageId": draft_id, "draftId": draft_id, "patientKey": draft["patientKey"],
            "version": draft["version"], "envelopeName": envelope_name_for(doc_id),
            "envelope": {"bytes": len(env.pdf_bytes), "sha256": env.pdf_sha256, "sha1": sha1(env.pdf_bytes)},
            "associatedFiles": [f.inventory() for f in embedded],
            "ipsSha256": env.ips_sha256,
            "checks": {
                "embeddedPayloadIdentical": identical,
                "renditionMissingFacts": missing,
                "preflight": pf.to_dict(),
                "verapdf": vera.to_dict(),
            },
            "publishable": publishable,
            "gate": "all packaging checks passed" + ("" if vera.status == "passed" else
                                                     " (veraPDF not run: conformance not claimed)")
            if publishable else "; ".join(gate),
            "rendererVersion": RENDERER_VERSION,
        }
        self.work.put_bytes("packages", draft_id, "envelope.pdf", env.pdf_bytes)
        if vera.raw_report:
            self.work.put_bytes("packages", draft_id, "verapdf-report.xml", vera.raw_report)
        self.work.save("packages", draft_id, record)
        return record

    def package_record(self, package_id: str) -> dict:
        return self.work.load("packages", package_id)

    def envelope_bytes(self, package_id: str) -> bytes:
        return self.work.get_bytes("packages", package_id, "envelope.pdf")

    def verapdf_report(self, package_id: str) -> bytes:
        return self.work.get_bytes("packages", package_id, "verapdf-report.xml")

    # ------------------------------------------------------------- 7 publish
    def _entry(self, *, unique_id: str, rep: str, cx: str, issued: datetime, title: str, comments: str) -> DocumentEntry:
        ad = self.settings.affinity_domain
        de_cfg = ad["document_entry"]
        mime, fmt = self.reps[rep]
        codes = {k: Code.from_cfg(v) for k, v in de_cfg.items() if isinstance(v, dict)}
        codes["formatCode"] = fmt
        return DocumentEntry(
            entry_uuid=f"urn:uuid:{uuid.uuid4()}", unique_id=unique_id, patient_id=cx, mime_type=mime, title=title,
            creation_time=_dtm(issued), language=de_cfg["languageCode"], codes=codes,
            author_institution=ad["author"]["institution"], author_person=ad["author"]["person"],
            author_role=ad["author"]["role"], source_patient_id=cx, comments=comments)

    def _submission(self, cx: str, docs: list[DocumentEntry], extra: list[Association],
                    contents: dict[str, bytes]) -> Submission:
        ad = self.settings.affinity_domain
        ss = SubmissionSet(entry_uuid=f"urn:uuid:{uuid.uuid4()}", unique_id=_oid_from_uuid(uuid.uuid4()),
                           source_id=ad["affinity_domain"]["source_id"], patient_id=cx,
                           submission_time=_dtm(_now()),
                           content_type=Code.from_cfg(ad["submission_set"]["contentTypeCode"]),
                           author_institution=ad["author"]["institution"], author_person=ad["author"]["person"])
        assocs = [has_member(ss.entry_uuid, d.entry_uuid) for d in docs] + extra
        return Submission(ss, docs, assocs, contents)

    def publish(self, package_id: str) -> dict:
        pkg = self.package_record(package_id)
        if not pkg["publishable"]:
            raise DemoError(409, "package gate not passed: " + pkg["gate"])
        if self.work.exists("packages", package_id, "publication.json"):
            raise DemoError(409, "this package has already been published (issued documents are immutable)")
        draft = self.draft(package_id)
        key = draft["patientKey"]
        source = load_fixture(self.settings, key)
        cx = self._cx(source)
        issued = datetime.strptime(draft["issued"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        pdf = self.envelope_bytes(package_id)
        ips = self.draft_ips(package_id)
        doc_uuid = uuid.UUID(package_id)

        env_entry = self._entry(
            unique_id=_oid_from_uuid(doc_uuid), rep="envelope", cx=cx, issued=issued,
            title=f"International Patient Summary v{draft['version']} (PDF/A-3b envelope)",
            comments=f"Preserved package. Embedded FHIR IPS {draft['documentUrn']} sha256={pkg['ipsSha256']}")
        extra = []
        if draft.get("replacesEnvelopeEntry"):
            extra.append(Association(f"urn:uuid:{uuid.uuid4()}", ASSOC_RPLC, env_entry.entry_uuid,
                                     draft["replacesEnvelopeEntry"]))
        sub_a = self._submission(cx, [env_entry], extra, {env_entry.entry_uuid: pdf})
        ex_a = self.xds.provide_and_register(sub_a)
        if ex_a.status != RESPONSE_SUCCESS:
            raise DemoError(502, f"ITI-41 (envelope) failed: {ex_a.errors}")

        ips_entry = self._entry(
            unique_id=_oid_from_uuid(uuid.uuid5(doc_uuid, "ips-projection")), rep="ips", cx=cx, issued=issued,
            title=f"International Patient Summary v{draft['version']} (FHIR IPS document)",
            comments=f"Exchange projection of {draft['documentUrn']}; byte-identical to the envelope's Associated File")
        sub_b = self._submission(cx, [ips_entry], [Association(f"urn:uuid:{uuid.uuid4()}", ASSOC_XFRM,
                                                               ips_entry.entry_uuid, env_entry.entry_uuid)],
                                 {ips_entry.entry_uuid: ips})
        ex_b = self.xds.provide_and_register(sub_b)
        if ex_b.status != RESPONSE_SUCCESS:
            raise DemoError(502, f"ITI-41 (IPS projection) failed: {ex_b.errors}")

        repo_id = self.repository.unique_id
        issuance = {
            "version": draft["version"], "packageId": package_id, "documentUrn": draft["documentUrn"],
            "issued": draft["issued"], "replaces": draft.get("replaces"),
            "envelope": {"entryUUID": env_entry.entry_uuid, "uniqueId": env_entry.unique_id,
                         "repositoryUniqueId": repo_id, "sha256": pkg["envelope"]["sha256"],
                         "sha1": pkg["envelope"]["sha1"], "size": pkg["envelope"]["bytes"],
                         "formatCode": env_entry.codes["formatCode"].code, "mimeType": env_entry.mime_type,
                         "submissionSet": sub_a.submission_set.unique_id},
            "ips": {"entryUUID": ips_entry.entry_uuid, "uniqueId": ips_entry.unique_id, "repositoryUniqueId": repo_id,
                    "sha256": pkg["ipsSha256"], "formatCode": ips_entry.codes["formatCode"].code,
                    "mimeType": ips_entry.mime_type, "submissionSet": sub_b.submission_set.unique_id},
        }
        self.work.save("packages", package_id, {"issuance": issuance,
                                                "exchanges": [asdict(ex_a), asdict(ex_b)]}, "publication.json")
        self.work.save("patients", key, self._issuances(key) + [issuance], "issuances.json")
        return {"issuance": issuance, "exchanges": [asdict(ex_a), asdict(ex_b)],
                "metadata": {"envelope": asdict(env_entry), "ips": asdict(ips_entry),
                             "submissionSets": [asdict(sub_a.submission_set), asdict(sub_b.submission_set)]}}

    # ------------------------------------------------------ 8 discover + retrieve
    def discover(self, key: str) -> dict:
        source = load_fixture(self.settings, key)
        cx = self._cx(source)
        docs, ex = self.xds.find_documents(cx, [STATUS_APPROVED, STATUS_DEPRECATED])
        system = self.settings.affinity_domain["affinity_domain"]["patient_assigning_authority"]["fhir_system"]
        url = (f"{self.base}/fhir/DocumentReference?patient.identifier={system}|{source['patient']['identifier']}"
               f"&status=current,superseded")
        status, _, body = self.http.get(url, {"Accept": "application/fhir+json"})
        mhd = json.loads(body)
        xds_rows = [{"entryUUID": d.entry_uuid, "uniqueId": d.unique_id, "status": d.status.rsplit(":", 1)[-1],
                     "mimeType": d.mime_type, "formatCode": d.codes["formatCode"].code, "title": d.title,
                     "size": d.size, "hash": d.hash} for d in docs]
        mhd_rows = [{"id": e["resource"]["id"], "masterIdentifier": e["resource"]["masterIdentifier"]["value"],
                     "status": e["resource"]["status"],
                     "contentType": e["resource"]["content"][0]["attachment"]["contentType"],
                     "format": e["resource"]["content"][0]["format"]["code"],
                     "relatesTo": e["resource"].get("relatesTo", [])} for e in mhd.get("entry", [])]
        agree = sorted(r["entryUUID"].removeprefix("urn:uuid:") for r in xds_rows) == sorted(r["id"] for r in mhd_rows)
        return {"patientCX": cx, "xds": {"transaction": "ITI-18 FindDocuments", "results": xds_rows,
                                         "exchange": asdict(ex)},
                "mhd": {"transaction": "ITI-67 Find Document References", "url": url, "httpStatus": status,
                        "results": mhd_rows, "bundle": mhd},
                "equivalent": agree}

    def _issuance(self, key: str, version: int | None = None) -> dict:
        iss = self._issuances(key)
        if not iss:
            raise DemoError(404, "nothing has been published for this patient yet")
        if version is None:
            return iss[-1]
        for i in iss:
            if i["version"] == version:
                return i
        raise DemoError(404, f"version {version} not found")

    def retrieve(self, key: str, version: int | None = None) -> dict:
        iss = self._issuance(key, version)
        env = iss["envelope"]
        data, mime, ex = self.xds.retrieve(env["repositoryUniqueId"], env["uniqueId"])
        if data is None:
            raise DemoError(502, f"ITI-43 retrieve failed: {ex.errors}")
        ips_url = f"{self.base}/fhir/Binary/{iss['ips']['entryUUID'].removeprefix('urn:uuid:')}"
        st, ctype, ips_bytes = self.http.get(ips_url, {"Accept": "application/fhir+json"})
        checks = verify_envelope(data, document_urn=iss["documentUrn"], pdf_sha256=env["sha256"],
                                 ips_sha256=iss["ips"]["sha256"], xds_hash=env["sha1"], size=env["size"],
                                 projection=ips_bytes)
        return {"version": iss["version"],
                "xds": {"transaction": "ITI-43 Retrieve Document Set", "mimeType": mime, "bytes": len(data),
                        "sha256": sha256(data), "byteIdenticalToPackage": sha256(data) == env["sha256"],
                        "exchange": asdict(ex)},
                "mhd": {"transaction": "ITI-68 Retrieve Document (IPS projection)", "url": ips_url,
                        "httpStatus": st, "contentType": ctype, "bytes": len(ips_bytes),
                        "ips": ips_bytes.decode("utf-8")},
                "integrity": checks_to_dict(checks)}

    # ---------------------------------------------------------- 10 EHDS preview
    def ehds_preview(self, key: str, version: int | None = None) -> dict:
        iss = self._issuance(key, version)
        ips = iss["ips"]
        data, _, _ex = self.xds.retrieve(ips["repositoryUniqueId"], ips["uniqueId"])
        if data is None:
            raise DemoError(502, "could not retrieve the IPS projection")
        result = ReadinessPreviewAdapter(self.settings).export(data).to_dict()
        result["documentUrn"] = iss["documentUrn"]
        result["source"] = "IPS projection retrieved via ITI-43 (not extracted from the PDF)"
        return result

    # ----------------------------------------------------------- tamper + history
    def tamper(self, key: str, mode: str) -> dict:
        iss = self._issuance(key)
        env = iss["envelope"]
        original = self.repository.retrieve(env["repositoryUniqueId"], env["uniqueId"])[0]
        ips_projection = self.repository.retrieve(iss["ips"]["repositoryUniqueId"], iss["ips"]["uniqueId"])[0]
        if mode == "embedded":
            tampered, what = tamper_embedded_payload(original)
        elif mode == "outer":
            tampered, what = tamper_outer_bytes(original)
        else:
            raise DemoError(400, "mode must be 'embedded' or 'outer'")
        checks = verify_envelope(tampered, document_urn=iss["documentUrn"], pdf_sha256=env["sha256"],
                                 ips_sha256=iss["ips"]["sha256"], xds_hash=env["sha1"], size=env["size"],
                                 projection=ips_projection)
        still = verify_envelope(original, document_urn=iss["documentUrn"], pdf_sha256=env["sha256"],
                                ips_sha256=iss["ips"]["sha256"], xds_hash=env["sha1"], size=env["size"],
                                projection=ips_projection)
        self.registry.audit.record("demo", "tamper simulation", mode=mode, document=env["uniqueId"])
        return {"mode": mode, "tampering": what, "note": "Simulation on an in-memory copy; the stored "
                "repository object is immutable and unchanged.",
                "tampered": checks_to_dict(checks), "storedOriginal": checks_to_dict(still)}

    def history(self, key: str) -> dict:
        out = []
        for iss in self._issuances(key):
            env = self.registry.store.get(iss["envelope"]["entryUUID"])
            ips = self.registry.store.get(iss["ips"]["entryUUID"])
            out.append({**iss, "envelopeStatus": env.status.rsplit(":", 1)[-1] if env else "unknown",
                        "ipsStatus": ips.status.rsplit(":", 1)[-1] if ips else "unknown",
                        "associations": [asdict(a) for a in self.registry.store.associations_for(
                            iss["envelope"]["entryUUID"]) if a.type != "urn:oasis:names:tc:ebxml-regrep:AssociationType:HasMember"]})
        return {"patientKey": key, "issuances": out}

    def evidence(self, package_id: str) -> dict:
        ev = {"draft": self.draft(package_id), "package": self.package_record(package_id),
              "standards": self.settings.ips_package}
        if self.work.exists("packages", package_id, "publication.json"):
            ev["publication"] = self.work.load("packages", package_id, "publication.json")["issuance"]
        return ev

    # ------------------------------------------------------- sIPS on-demand
    def on_demand_summary(self, patient_value: str) -> dict:
        for p in list_fixtures(self.settings):
            if p["identifier"] == patient_value:
                composed = compose_ips(self._current_source(p["key"]), self.settings, issued=_now())
                bundle = composed.bundle
                bundle["meta"]["tag"] = [on_demand_tag()]
                return bundle
        raise LookupError(f"no synthetic patient {patient_value}")


def _codings(node: Any):
    if isinstance(node, dict):
        if "coding" in node and isinstance(node["coding"], list):
            yield from node["coding"]
        for v in node.values():
            yield from _codings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _codings(v)
