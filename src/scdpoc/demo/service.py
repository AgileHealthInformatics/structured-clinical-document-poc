"""Tutorial orchestration for the end-to-end demonstrator journey.

These operations are deliberately convenient and are NOT interoperability
interfaces. Every standards interaction they trigger (ITI-41, ITI-18,
ITI-43, ITI-67, ITI-68) goes over HTTP to the real endpoints, so the UI
shows genuine request/response evidence.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .. import fidelity, lifecycle
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
from ..ips.attestation import add_attestation, attested_content_digest
from ..ips.composer import apply_correction, apply_revision, compose_ips
from ..ips.validator import validate_ips
from ..ips.view import build_view
from ..mhd.router import on_demand_tag
from ..pdfa.extract import extract_embedded, page_text
from ..pdfa.packager import attachment_name_for, build_envelope, envelope_name_for
from ..pdfa.preflight import preflight
from ..pdfa.verapdf import VeraPdf
from ..preservation import EventLog, record_digest
from ..render.html import RENDERER_VERSION, render_html
from ..render.pdf import render_pdf
from ..safety import list_fixtures, load_fixture
from ..xds.actors import AffinityDomainPolicy, Registry, Repository
from ..xds.client import XdsClient
from ..xds.ebrim import has_member
from ..xds.model import (
    ASSOC_RPLC,
    ASSOC_UPDATE_AVAILABILITY,
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
        self.events = EventLog(settings.data_dir / "preservation")

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
            return {"variant": "base"}

    def _lifecycle(self, key: str) -> dict:
        try:
            return self.work.load("patients", key, "lifecycle.json")
        except DemoError:
            return {}

    def _set_state(self, key: str, document_urn: str, action: str) -> lifecycle.Transition:
        states = self._lifecycle(key)
        t = lifecycle.transition(states.get(document_urn, {}).get("state"), action)
        states[document_urn] = {"state": t.target, "since": _now().isoformat(), "action": action}
        self.work.save("patients", key, states, "lifecycle.json")
        return t

    def _current_issuance(self, key: str) -> dict | None:
        states = self._lifecycle(key)
        cur = [i for i in self._issuances(key) if states.get(i["documentUrn"], {}).get("state") == lifecycle.ISSUED]
        return cur[-1] if cur else None

    def _known_recipients(self, iss: dict) -> list[str]:
        """Communities known to have received this issuance's projection (gateway disclosure audit)."""
        uid = iss["ips"]["uniqueId"]
        hits = [e for e in self.registry.audit.recent(10000) if e["event"] == "ITI-39 cross gateway retrieve"
                and uid in (e["detail"].get("delivered_ids") or [])]
        return [self.settings.communities["consumer"]["name"]] if hits else []

    def lifecycle_state_of(self, entry_uuid: str) -> str | None:
        """The issuer's recorded lifecycle state for a registry entry (feeds the MHD lifecycle extension)."""
        for p in list_fixtures(self.settings):
            states = self._lifecycle(p["key"])
            for iss in self._issuances(p["key"]):
                if entry_uuid in (iss["ips"]["entryUUID"], iss["envelope"]["entryUUID"]):
                    return states.get(iss["documentUrn"], {}).get("state")
        return None

    def _current_source(self, key: str) -> dict:
        return self._source_for(key, self._patient_state(key).get("variant", "base"))

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
    ATTESTATION_METHOD = ("Demonstrator UI confirmation by a synthetic user. Shows the evidence model of an "
                          "attestation action; it is not a clinical attestation.")

    def _source_for(self, key: str, variant: str) -> dict:
        source = load_fixture(self.settings, key)
        if variant in ("revised",):
            source = apply_revision(source)
        if variant == "corrected":
            if self._patient_state(key).get("variant") == "revised":
                source = apply_revision(source)
            source = apply_correction(source)
        return source

    def compose(self, key: str, revise: bool = False, correct: bool = False) -> dict:
        """Compose a draft. Drafts are machine-generated snapshots until attested (profile option AI)."""
        src = load_fixture(self.settings, key)
        issuances = self._issuances(key)
        state = self._patient_state(key)
        if revise or correct:
            block = "revision" if revise else "correction"
            if block not in src:
                raise DemoError(400, f"this synthetic patient has no {block} scenario")
            if not issuances:
                raise DemoError(409, "publish version 1 before composing a replacement")
            if self._current_issuance(key) is None:
                raise DemoError(409, "there is no current issuance to replace (it has been withdrawn); compose a "
                                     "new issuance instead")
            variant, reason = ("revised", "update") if revise else ("corrected", "correction")
        else:
            variant, reason = state.get("variant", "base"), ("update" if issuances else None)
        return self._compose(key, variant, reason)

    def _compose(self, key: str, variant: str, reason: str | None) -> dict:
        source = self._source_for(key, variant)
        prev = self._current_issuance(key)
        if prev is None:
            reason = None
        hist = self._issuances(key)
        composed = compose_ips(source, self.settings, issued=_now(),
                               version=(hist[-1]["version"] + 1) if hist else 1,
                               replaces_document_urn=prev["documentUrn"] if prev else None,
                               status="amended" if reason == "correction" else "final")
        return self._save_draft(key, variant, reason, prev, composed.bundle, composed.json_bytes, composed.version,
                                composed.series_id, evidence=None)

    def _save_draft(self, key: str, variant: str, reason: str | None, prev: dict | None, bundle: dict,
                    json_bytes: bytes, version: int, series_id: str, evidence: dict | None) -> dict:
        report = validate_ips(bundle, json_bytes, self.settings)
        draft_id = bundle["id"]
        comp = bundle["entry"][0]["resource"]
        record = {
            "draftId": draft_id, "patientKey": key, "version": version, "variant": variant,
            "documentUrn": bundle["identifier"]["value"], "seriesId": series_id,
            "issued": bundle["timestamp"], "contentTime": comp.get("date"),
            "replaces": prev["documentUrn"] if prev else None,
            "replacesIpsEntry": prev["ips"]["entryUUID"] if prev else None,
            "replacementReason": reason if prev else None,
            "assurance": "attested-issuance" if evidence else "preserved-snapshot",
            "attestationEvidence": evidence,
            "attestedContentDigest": attested_content_digest(bundle),
            "ips": {"bytes": len(json_bytes), "sha256": sha256(json_bytes), "package": report.package},
            "profiles": sorted({p for e in bundle["entry"] for p in e["resource"].get("meta", {}).get("profile", [])}),
            "sections": [{"title": s["title"], "code": s["code"]["coding"][0]["code"],
                          "entries": len(s.get("entry", [])),
                          "emptyReason": (s.get("emptyReason") or {}).get("coding", [{}])[0].get("code")}
                         for s in comp["section"]],
            "terminology": sorted({c.get("system") for e in bundle["entry"]
                                   for c in _codings(e["resource"]) if c.get("system")}),
            "validation": report.to_dict(),
            "provenance": _provenance(bundle),
        }
        if variant != self._patient_state(key).get("variant", "base"):
            self.work.save("patients", key, {**self._patient_state(key), "variant": variant}, "state.json")
        self.work.put_bytes("drafts", draft_id, "ips.json", json_bytes)
        self.work.save("drafts", draft_id, record)
        return {**record, "bundle": json_bytes.decode("utf-8"),
                "html": render_html(build_view(bundle)), "rendererVersion": RENDERER_VERSION}

    def attest(self, draft_id: str, attester: str = "Dr Sam Synthetic", attester_id: str = "SYN-PRAC-1",
               kind: str = "person") -> dict:
        """Option AI, the attestation sequence of profile 0.4.0 (PROV-08): the attester reviews this validated
        draft; the attestation is recorded against the draft's attested content digest; the final IPS is the
        reviewed draft plus the attestation and nothing else (IPS-07), and is validated again."""
        draft = self.draft(draft_id)
        if draft["assurance"] == "attested-issuance":
            raise DemoError(409, "this draft is already attested")
        if self.work.exists("packages", draft_id, "publication.json"):
            raise DemoError(409, "this draft has already been issued as a preserved snapshot")
        if not draft["validation"]["publishable"]:
            raise DemoError(409, "only a validated draft can be attested (sequence step 2)")
        reviewed = self.draft_ips(draft_id)
        reviewed_bundle = json.loads(reviewed)
        digest = attested_content_digest(reviewed_bundle)
        now = _now()
        name = attester if kind == "person" else self.settings.affinity_domain["author"]["institution"].split("^")[0]
        evidence = {"attester": name, "attesterId": attester_id if kind == "person" else None,
                    "attesterKind": kind, "time": now.isoformat(timespec="seconds"),
                    "mode": "legal" if kind == "person" else "official", "method": self.ATTESTATION_METHOD,
                    "statement": "I have reviewed this patient summary and accept responsibility for its content.",
                    "reviewedDraft": draft_id, "reviewedIpsSha256": sha256(reviewed),
                    "attestedContentDigest": digest}
        final, final_bytes = add_attestation(reviewed_bundle, {"name": name, "id": attester_id, "time": now,
                                                               "kind": kind}, uuid.uuid4(), now)
        if attested_content_digest(final) != digest:
            raise DemoError(500, "internal error: the final IPS does not carry exactly the attested content")
        prev = next((i for i in self._issuances(draft["patientKey"]) if i["documentUrn"] == draft.get("replaces")),
                    None)
        out = self._save_draft(draft["patientKey"], draft["variant"], draft.get("replacementReason"), prev, final,
                               final_bytes, draft["version"], draft["seriesId"], evidence)
        self.registry.audit.record("issuer", "attestation", attester=name, draft=draft_id,
                                   attested_draft=out["draftId"])
        self.events.append("attestation", agent=f"{name} ({attester_id if kind == 'person' else 'organisation'})",
                           artefact={"documentId": out["documentUrn"], "draft": out["draftId"],
                                     "reviewedDraft": draft_id},
                           digest={"algorithm": "SHA-256", "value": digest, "of": "attested content"},
                           detail={"method": self.ATTESTATION_METHOD, "statement": evidence["statement"],
                                   "reviewedIpsSha256": evidence["reviewedIpsSha256"],
                                   "finalIpsSha256": out["ips"]["sha256"]})
        return out

    def _check_attested(self, draft: dict) -> None:
        """Gate (PROV-08.b): attested content must be exactly what was attested."""
        ev = draft.get("attestationEvidence")
        if not ev:
            return
        actual = attested_content_digest(json.loads(self.draft_ips(draft["draftId"])))
        if actual != ev.get("attestedContentDigest"):
            raise DemoError(409, "the clinical content differs from the content that was attested; it must be "
                                 "attested again before it can be published")

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
        self._check_attested(draft)
        if self.work.exists("packages", draft_id):
            return self.work.load("packages", draft_id)
        ips = self.draft_ips(draft_id)
        bundle = json.loads(ips)
        view = build_view(bundle)
        issued = datetime.strptime(bundle["timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        doc_id = bundle["id"]
        pages = render_pdf(view, attachment_name_for(doc_id))
        env = build_envelope(pages, ips, document_id=doc_id, issued=issued, title=view.title, author=view.custodian,
                             subject=f"International Patient Summary for {view.patient_name} ({view.patient_id})",
                             identifier=bundle["identifier"]["value"], language=view.language)
        pf = preflight(env.pdf_bytes)
        vera = VeraPdf(self.settings).validate(env.pdf_bytes)
        embedded = extract_embedded(env.pdf_bytes)
        ips_files = [f for f in embedded if f.relationship == "Source" and f.mime_type == "application/fhir+json"]
        identical = len(ips_files) == 1 and ips_files[0].data == ips
        fid = fidelity.check(bundle, page_text(env.pdf_bytes))
        missing = fid.missing + fid.misplaced + fid.foreign_codes
        vera_ok = vera.status == "passed" or (vera.status == "not-run" and not self.settings.require_verapdf)
        publishable = pf.passed and identical and not missing and vera_ok
        gate = []
        if not pf.passed:
            gate.append("PDF/A pre-flight failed")
        if not identical:
            gate.append("embedded IPS is not byte-identical to the validated IPS")
        if missing:
            gate.append(f"rendition fidelity failed ({len(fid.missing)} missing, {len(fid.misplaced)} misplaced, "
                        f"{len(fid.foreign_codes)} foreign codes)")
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
                "fidelity": fid.to_dict(),
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
    # The content author is software: authorPerson is an XCN software agent (ITI TF-3 4.2.3.1.4.2 allows a
    # machine); authorRole is left empty because it states a role in the documented act (profile MET-13.c).
    SOFTWARE_AUTHOR = "scdpoc-summary-generator^SCD-PoC summary generator^^^^^^^&2.999.1.11&ISO"

    def _entry(self, *, unique_id: str, rep: str, cx: str, issued: datetime, title: str, comments: str,
               legal_authenticator: str = "") -> DocumentEntry:
        ad = self.settings.affinity_domain
        de_cfg = ad["document_entry"]
        mime, fmt = self.reps[rep]
        codes = {k: Code.from_cfg(v) for k, v in de_cfg.items() if isinstance(v, dict)}
        codes["formatCode"] = fmt
        return DocumentEntry(
            entry_uuid=f"urn:uuid:{uuid.uuid4()}", unique_id=unique_id, patient_id=cx, mime_type=mime, title=title,
            creation_time=_dtm(issued), language=de_cfg["languageCode"], codes=codes,
            author_institution=ad["author"]["institution"], author_person=self.SOFTWARE_AUTHOR,
            author_role="", source_patient_id=cx, comments=comments,
            legal_authenticator=legal_authenticator)

    def _submission(self, cx: str, docs: list[DocumentEntry], extra: list[Association],
                    contents: dict[str, bytes]) -> Submission:
        ad = self.settings.affinity_domain
        ss = SubmissionSet(entry_uuid=f"urn:uuid:{uuid.uuid4()}", unique_id=_oid_from_uuid(uuid.uuid4()),
                           source_id=ad["affinity_domain"]["source_id"], patient_id=cx,
                           submission_time=_dtm(_now()),
                           content_type=Code.from_cfg(ad["submission_set"]["contentTypeCode"]),
                           author_institution=ad["author"]["institution"], author_person="")
        assocs = [has_member(ss.entry_uuid, d.entry_uuid) for d in docs] + extra
        return Submission(ss, docs, assocs, contents)

    def publish(self, package_id: str) -> dict:
        """One issuance = one ITI-41 submission (atomic at the registry): both DocumentEntries,
        HasMember x2, XFRM envelope -> IPS (the envelope is rendered from the IPS), and, for a
        replacement, RPLC new IPS -> previous IPS. Per ITI TF-3 the registry also deprecates the
        transformations of a replaced document, so the previous envelope is deprecated with it."""
        pkg = self.package_record(package_id)
        if not pkg["publishable"]:
            raise DemoError(409, "package gate not passed: " + pkg["gate"])
        if self.work.exists("packages", package_id, "publication.json"):
            raise DemoError(409, "this package has already been published (issued documents are immutable)")
        draft = self.draft(package_id)
        self._check_attested(draft)
        key = draft["patientKey"]
        source = load_fixture(self.settings, key)
        cx = self._cx(source)
        # XDS creationTime is the clinical content time (Composition.date), not the issuance time (PROV-09).
        issued = datetime.strptime(draft.get("contentTime") or draft["issued"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        pdf = self.envelope_bytes(package_id)
        ips = self.draft_ips(package_id)
        doc_uuid = uuid.UUID(package_id)

        ev = draft.get("attestationEvidence")
        # legalAuthenticator only for an attestation by a person; an organisation's attestation stays in the IPS and
        # the issuance record (MET-13.a, MET-13.b).
        legal = (f"{ev['attesterId']}^{ev['attester'].split()[-1]}^{' '.join(ev['attester'].split()[1:-1]) or ''}"
                 f"^^^{ev['attester'].split()[0]}^^^&2.999.1.8.1&ISO") \
            if ev and ev.get("attesterKind", "person") == "person" else ""
        ips_entry = self._entry(
            legal_authenticator=legal,
            unique_id=_oid_from_uuid(uuid.uuid5(doc_uuid, "ips-projection")), rep="ips", cx=cx, issued=issued,
            title=f"International Patient Summary v{draft['version']} (FHIR IPS document)",
            comments=f"Structured source of issuance {draft['documentUrn']}")
        env_entry = self._entry(
            legal_authenticator=legal,
            unique_id=_oid_from_uuid(doc_uuid), rep="envelope", cx=cx, issued=issued,
            title=f"International Patient Summary v{draft['version']} (PDF/A-3b preservation envelope)",
            comments=f"Preserved package of issuance {draft['documentUrn']}; rendered from and embedding the IPS "
                     f"(sha256 {pkg['ipsSha256']})")
        assocs = [Association(f"urn:uuid:{uuid.uuid4()}", ASSOC_XFRM, env_entry.entry_uuid, ips_entry.entry_uuid)]
        if draft.get("replacesIpsEntry"):
            assocs.append(Association(f"urn:uuid:{uuid.uuid4()}", ASSOC_RPLC, ips_entry.entry_uuid,
                                      draft["replacesIpsEntry"]))
        sub = self._submission(cx, [ips_entry, env_entry], assocs,
                               {ips_entry.entry_uuid: ips, env_entry.entry_uuid: pdf})
        ex = self.xds.provide_and_register(sub)
        if ex.status != RESPONSE_SUCCESS:
            raise DemoError(502, f"ITI-41 failed; nothing was registered: {ex.errors}")

        repo_id = self.repository.unique_id
        ss_uid = sub.submission_set.unique_id
        issuance = {
            "version": draft["version"], "packageId": package_id, "documentUrn": draft["documentUrn"],
            "seriesId": draft["seriesId"], "issued": draft["issued"], "contentTime": draft.get("contentTime"),
            "submissionTime": sub.submission_set.submission_time, "replaces": draft.get("replaces"),
            "replacementReason": draft.get("replacementReason"), "submissionSet": ss_uid,
            "attestedContentDigest": draft.get("attestedContentDigest"),
            "assurance": draft["assurance"], "attestationEvidence": draft.get("attestationEvidence"),
            "envelope": {"entryUUID": env_entry.entry_uuid, "uniqueId": env_entry.unique_id,
                         "repositoryUniqueId": repo_id, "sha256": pkg["envelope"]["sha256"],
                         "sha1": pkg["envelope"]["sha1"], "size": pkg["envelope"]["bytes"],
                         "formatCode": env_entry.codes["formatCode"].code, "mimeType": env_entry.mime_type},
            "ips": {"entryUUID": ips_entry.entry_uuid, "uniqueId": ips_entry.unique_id, "repositoryUniqueId": repo_id,
                    "sha256": pkg["ipsSha256"], "formatCode": ips_entry.codes["formatCode"].code,
                    "mimeType": ips_entry.mime_type},
            "provenance": draft.get("provenance"),
            "renderer": pkg.get("rendererVersion"),
            "evidence": {"ipsValidation": draft["validation"]["gate"], "pdfa": pkg["gate"],
                         "ipsPackage": draft["validation"]["package"]},
            "validationEvidence": validation_evidence(draft, pkg),
            "representation": representation(draft, pkg),
        }
        prev = next((i for i in self._issuances(key) if i["documentUrn"] == draft.get("replaces")), None)
        self.work.save("packages", package_id, {"issuance": issuance, "exchanges": [asdict(ex)]}, "publication.json")
        self.work.save("patients", key, self._issuances(key) + [issuance], "issuances.json")
        self._set_state(key, draft["documentUrn"], "issue")
        art = {"documentId": draft["documentUrn"], "envelopeUniqueId": env_entry.unique_id,
               "ipsUniqueId": ips_entry.unique_id}
        self.events.append("validation", agent="SCD-PoC issuer (validation gate)", artefact=art,
                           digest={"algorithm": "SHA-256", "value": pkg["ipsSha256"]},
                           detail={"ips": draft["validation"]["gate"], "pdfa": pkg["gate"],
                                   "fidelity": "passed" if not pkg["checks"]["renditionMissingFacts"] else "failed"},
                           evidence=[f"/api/demo/evidence/{package_id}"])
        self.events.append("issuance", agent=f"{self.settings.affinity_domain['author']['institution'].split('^')[0]}"
                                             " (Issuer, Publisher)", artefact=art,
                           digest={"algorithm": "SHA-256", "value": pkg["envelope"]["sha256"]},
                           detail={"issuanceRecordSha256": record_digest(issuance), "ipsSha256": pkg["ipsSha256"],
                                   "assurance": issuance["assurance"], "submissionSet": ss_uid},
                           evidence=[f"/api/demo/evidence/{package_id}"])
        if prev is not None:
            action = "replace-for-correction" if issuance["replacementReason"] == "correction" else "replace-for-update"
            t = self._set_state(key, prev["documentUrn"], action)
            self.events.append("correction" if t.target == lifecycle.REPLACED_CORRECTION else "replacement",
                               agent="SCD-PoC issuer", artefact={"documentId": prev["documentUrn"]},
                               detail={"replacedBy": draft["documentUrn"], "reason": issuance["replacementReason"],
                                       "state": t.target})
            if t.notify:
                self._notify(key, prev, t.target)
        return {"issuance": issuance, "exchanges": [asdict(ex)],
                "metadata": {"envelope": asdict(env_entry), "ips": asdict(ips_entry),
                             "submissionSets": [asdict(sub.submission_set)],
                             "associations": [asdict(a) for a in assocs]}}

    def _notify(self, key: str, iss: dict, state: str) -> None:
        recipients = self._known_recipients(iss)
        self.events.append("notification-required", agent="SCD-PoC issuer",
                           artefact={"documentId": iss["documentUrn"]},
                           detail={"state": state, "knownRecipients": recipients,
                                   "note": "The earlier issuance may already have informed care (LIF-11). The "
                                           "demonstrator records the obligation; it does not send messages."})

    def withdraw(self, key: str, reason: str = "entered in error") -> dict:
        """Withdrawal (LIF-07, MET-11): the current issuance was entered in error and is withdrawn without a
        replacement. Both DocumentEntries go from Approved to Deprecated in one ITI-57 Update Document Set."""
        iss = self._current_issuance(key)
        if iss is None:
            raise DemoError(409, "there is no current issuance to withdraw")
        lifecycle.transition(lifecycle.ISSUED, "withdraw")                 # validates before any side effect
        cx = self._cx(load_fixture(self.settings, key))
        ad = self.settings.affinity_domain
        ss = SubmissionSet(entry_uuid=f"urn:uuid:{uuid.uuid4()}", unique_id=_oid_from_uuid(uuid.uuid4()),
                           source_id=ad["affinity_domain"]["source_id"], patient_id=cx,
                           submission_time=_dtm(_now()),
                           content_type=Code.from_cfg(ad["submission_set"]["contentTypeCode"]),
                           author_institution=ad["author"]["institution"], author_person="")
        assocs = [Association(f"urn:uuid:{uuid.uuid4()}", ASSOC_UPDATE_AVAILABILITY, ss.entry_uuid, uid,
                              {"OriginalStatus": [STATUS_APPROVED], "NewStatus": [STATUS_DEPRECATED]})
                  for uid in (iss["ips"]["entryUUID"], iss["envelope"]["entryUUID"])]
        ex = self.xds.update_document_set(Submission(ss, [], assocs))
        if ex.status != RESPONSE_SUCCESS:
            raise DemoError(502, f"ITI-57 failed; nothing was changed: {ex.errors}")
        t = self._set_state(key, iss["documentUrn"], "withdraw")
        self.events.append("withdrawal", agent="SCD-PoC issuer (Document Administrator)",
                           artefact={"documentId": iss["documentUrn"], "ipsUniqueId": iss["ips"]["uniqueId"],
                                     "envelopeUniqueId": iss["envelope"]["uniqueId"]},
                           detail={"reason": reason, "submissionSet": ss.unique_id, "state": t.target})
        self._notify(key, iss, t.target)
        self.work.save("patients", key, {**self._patient_state(key), "variant": "base"}, "state.json")
        return {"withdrawn": iss["documentUrn"], "reason": reason, "state": t.target,
                "exchange": asdict(ex), "history": self.history(key)}

    def preservation_events(self, document_urn: str | None = None) -> dict:
        events = self.events.for_document(document_urn) if document_urn else self.events.events()
        return {"events": events, "chain": self.events.verify().to_dict()}

    def fixity(self) -> dict:
        from ..preservation import fixity_check
        return fixity_check(self.settings.data_dir, agent="SCD-PoC repository (scheduled fixity check)",
                            log=self.events)

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
        mhd_rows = [{"id": e["resource"]["id"], "entryUUID": _slice(e["resource"], "entryUUID"),
                     "masterIdentifier": e["resource"]["masterIdentifier"]["value"],
                     "status": e["resource"]["status"],
                     "contentType": e["resource"]["content"][0]["attachment"]["contentType"],
                     "format": e["resource"]["content"][0]["format"]["code"],
                     "relatesTo": e["resource"].get("relatesTo", [])} for e in mhd.get("entry", [])]
        agree = sorted(r["entryUUID"] for r in xds_rows) == sorted(r["entryUUID"] for r in mhd_rows)
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
        ips_url = self.mhd_binary_url(iss["ips"]["entryUUID"])
        st, ctype, ips_bytes = self.http.get(ips_url, {"Accept": "application/fhir+json"})
        checks = verify_envelope(data, document_urn=iss["documentUrn"], pdf_sha256=env["sha256"],
                                 ips_sha256=iss["ips"]["sha256"], xds_hash=env["sha1"], size=env["size"],
                                 projection=ips_bytes)
        self.registry.audit.record("consumer", "preservation event: fixity check", document=iss["documentUrn"],
                                   passed=all(c.passed for c in checks),
                                   failed=[c.id for c in checks if not c.passed])
        return {"version": iss["version"],
                "xds": {"transaction": "ITI-43 Retrieve Document Set", "mimeType": mime, "bytes": len(data),
                        "sha256": sha256(data), "byteIdenticalToPackage": sha256(data) == env["sha256"],
                        "exchange": asdict(ex)},
                "mhd": {"transaction": "ITI-68 Retrieve Document (IPS projection)", "url": ips_url,
                        "httpStatus": st, "contentType": ctype, "bytes": len(ips_bytes),
                        "ips": ips_bytes.decode("utf-8")},
                "integrity": checks_to_dict(checks)}

    def mhd_binary_url(self, entry_uuid: str) -> str:
        """ITI-67 search by the entryUUID identifier slice, then follow content.attachment.url (ITI-68). The
        DocumentReference id is server-assigned, so a client never constructs it from the entryUUID."""
        _, _, body = self.http.get(f"{self.base}/fhir/DocumentReference?identifier="
                                   f"{quote('urn:ietf:rfc:3986|' + entry_uuid, safe='')}",
                                   {"Accept": "application/fhir+json"})
        hits = json.loads(body).get("entry", [])
        if not hits:
            raise DemoError(404, f"no DocumentReference for {entry_uuid}")
        return hits[0]["resource"]["content"][0]["attachment"]["url"]

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
            state = self._lifecycle(key).get(iss["documentUrn"], {})
            out.append({**iss, "lifecycleState": state.get("state"), "lifecycleSince": state.get("since"),
                        "envelopeStatus": env.status.rsplit(":", 1)[-1] if env else "unknown",
                        "ipsStatus": ips.status.rsplit(":", 1)[-1] if ips else "unknown",
                        "associations": _related_assocs(self.registry, iss)})
        return {"patientKey": key, "issuances": out, "lifecycleTable": lifecycle.table()}

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


def validation_evidence(draft: dict, pkg: dict) -> dict:
    """SRC-04 / ENV-08 evidence for the issuance record: which validators ran, which versions, which outcome."""
    from ..checker import load_dependencies
    deps = load_dependencies()
    engines = {e["engine"]: e for e in draft["validation"]["engines"]}
    hl7 = engines.get("hl7-fhir-validator", {})
    vera = pkg["checks"]["verapdf"]
    pre = engines.get("builtin-preflight") or next(iter(engines.values()), {})
    return {"ipsPackage": draft["validation"]["package"],
            "preflight": {"status": pre.get("status"), "engine": pre.get("engine")},
            "hl7Validator": {"status": hl7.get("status", "not-run"),
                             "version": deps["hl7-validator"]["version"] if hl7.get("status") == "passed" else None,
                             "detail": hl7.get("detail")},
            "pdfa": {"validator": "veraPDF", "status": vera["status"],
                     "version": deps["verapdf"]["version"] if vera["status"] == "passed" else None,
                     "profile": vera.get("profile") or "PDF/A-3B", "detail": vera.get("detail")},
            "fidelity": "passed" if not pkg["checks"]["renditionMissingFacts"] else "failed"}


def representation(draft: dict, pkg: dict) -> dict:
    """PRES-01 representation information. Terminology versions are not known for the synthetic source data and
    are recorded as unknown rather than invented."""
    return {"ipsPackage": draft["validation"]["package"], "fhirVersion": "4.0.1", "pdfa": "PDF/A-3b (ISO 19005-3:2012)",
            "renderer": pkg.get("rendererVersion"),
            "presentationResources": {"layout": pkg.get("rendererVersion"), "labels": "scdpoc.render.labels (en-GB)",
                                      "fonts": "Bitstream Vera, embedded"},
            "terminologies": [{"system": t, "version": None} for t in draft.get("terminology", [])]}


def _provenance(bundle: dict) -> dict:
    """Profile PROV-01: distinct provenance roles, as carried in the IPS."""
    idx = {e["fullUrl"]: e["resource"] for e in bundle["entry"]}
    comp = bundle["entry"][0]["resource"]

    def name(ref: str | None) -> str:
        r = idx.get(ref or "", {})
        if r.get("resourceType") == "Organization":
            return r.get("name", "")
        if r.get("resourceType") == "Device":
            return ((r.get("deviceName") or [{}])[0]).get("name", "")
        n = (r.get("name") or [{}])[0]
        return n.get("text", "")

    return {"sourceOrganisation": name((comp.get("custodian") or {}).get("reference")),
            "author": [name(a.get("reference")) for a in comp.get("author", [])],
            "attester": [{"party": name((a.get("party") or {}).get("reference")), "mode": a.get("mode"),
                          "time": a.get("time")} for a in comp.get("attester", [])],
            "custodian": name((comp.get("custodian") or {}).get("reference")),
            "renderer": RENDERER_VERSION}


def _slice(dr: dict, code: str) -> str | None:
    for i in dr.get("identifier", []):
        if any(c.get("code") == code for c in (i.get("type") or {}).get("coding", [])):
            return i.get("value")
    return None


def _related_assocs(registry, iss: dict) -> list[dict]:
    seen, out = set(), []
    for uid in (iss["ips"]["entryUUID"], iss["envelope"]["entryUUID"]):
        for a in registry.store.associations_for(uid):
            if a.type.endswith("HasMember") or a.entry_uuid in seen:
                continue
            seen.add(a.entry_uuid)
            out.append(asdict(a))
    return out


def _codings(node: Any):
    if isinstance(node, dict):
        if "coding" in node and isinstance(node["coding"], list):
            yield from node["coding"]
        for v in node.values():
            yield from _codings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _codings(v)
