# Changelog

## 0.5.0 - 2026-10-09

Implements the IPS Preservation Envelope Profile 0.4.0, the response to the third review (items IMP-01 to IMP-09;
ADR-010).

- **Changed (IMP-01):** the rule catalogue lists **normative obligations** (`RULE` or `RULE.a`, `RULE.b`...), each
  with one requirement level. The Checker evaluates and reports obligations; a failed SHOULD / SHOULD NOT is a
  warning and never decides the verdict. Results, claims and vector manifests that refer to an unknown obligation,
  test or outcome are rejected (CHK-10). Live and vector results are keyed by obligation id.
- **Added (IMP-02):** actor bindings in claims (`actorBindings`): the Checker requires only the transactions of the
  bindings a class declares; a Receiver using MHD alone is not required to implement XCA. The MHD Document Responder
  binding and option MHD must be declared together.
- **Added (IMP-03):** the fidelity contract follows the profile's coverage matrix: structured dosage in full
  (dose range, rate, method, site, timing with frequency ranges, `when`, time of day and bounds, maximum doses,
  additional and patient instructions, as-needed condition), notes, body sites, abatement, allergy type, category and
  onset, reaction substance, dose numbers, reference ranges and observation components. The renderer shows them in a
  new *Details* column (`scdpoc-render-4`). Populated elements the matrix marks N are reported as not verified
  (REN-14 `not-tested` unless an inspection is declared). A code shown in a section is accepted when the section's
  source narrative carries it (REN-08 as traceability). Fixtures gained these elements.
- **Changed (IMP-04):** MHD DocumentReferences carry a lifecycle-state extension (`issued`, `replaced-for-update`,
  `replaced-for-correction`, `withdrawn`), so a withdrawal is never presented as a replacement. The responding
  gateway answers a query by identifier for a non-current issuance with its metadata and, through
  GetRelatedDocuments, its RPLC successor; the receiver asks the home community and shows "current", "replaced",
  "withdrawn" or "reason unknown" on its rendition.
- **Changed (IMP-05):** times are mapped as the profile's time table states: `creationTime` and MHD
  `attachment.creation` are the clinical content time (`Composition.date`); MHD `DocumentReference.date` is the
  submission time; `Bundle.timestamp` is the issuance time; attestation time is `attester.time`. The rendition shows
  the clinical content time and the issuance time separately. The software author is an XCN software agent in
  `authorPerson`; `authorRole` is not used for it. `legalAuthenticator` only for a person attester.
- **Changed (IMP-06):** attestation follows the profile's sequence: the attester reviews a validated draft identified
  by its **attested content digest** (SHA-256 of the canonical JSON of the Bundle without identifier, timestamp and
  attestation); the final IPS is the draft plus the attestation and nothing else; packaging and publication refuse
  content that differs from what was attested. Organisational attestation (`kind=organisation`, mode `official`)
  records no `legalAuthenticator`.
- **Changed (IMP-07):** claims give each dependency a status (`exact`, `permitted-alternative`,
  `unsupported-deviation`); a deviation is not eligible for a conformant verdict, whatever reason is recorded. The
  format-code vocabulary is locked to `ihe.formatcode.fhir#1.5.0`.
- **Changed (IMP-08):** every vector expectation carries a justification. I-06 expects ENV-01 only; new vectors I-17
  (no Unicode mapping: ENV-10, advisory), I-18 (content changed after attestation: PROV-02.b, IPS-07) and I-19
  (boundary: an N-disposition element: REN-14 not-tested). V-02 is built by the attestation sequence. New live
  scenarios L-14 (content changed after attestation), L-15 (Checker self-tests: levels and undefined references) and
  L-16 (lifecycle meaning across XDS, MHD, the gateway and the receiver).
- **Changed (IMP-09):** the CI evidence manifest records the source revision, the specification version and the
  build environment. See `REPRODUCING.md`.
- **Known gaps:** independent HL7 validator and veraPDF reports for this release are still not available (profile
  issue IR-01); terminology versions are not recorded (PRES-01); the Protected Preservation option is not claimed;
  the security declarations a deployment must make (SEC-01..03) are not made by a demonstrator.

## 0.4.0 - 2026-10-08

Implements the IPS Preservation Envelope Profile 0.3.0, the response to the second review (items A-01 to A-12;
ADR-009).

- **Changed (A-02):** a composed summary is a *machine-generated preserved snapshot*: the Composition author is a
  Device ("SCD-PoC summary generator") and no attester is recorded anywhere. The 0.3.0 behaviour of recording a
  synthetic legal attester automatically is removed. **Added:** an explicit attestation action
  (`POST /api/demo/attest/{draft}`, UI button) producing an attested draft with evidence (attester, time, method,
  statement, digest of the reviewed draft); `legalAuthenticator` and MHD `authenticator` only for attested issuances;
  the rendition states the assurance level.
- **Added (A-01):** complete fidelity contract - structured dosage (dose, route, timing, as-needed), verification status,
  severity, reaction severity, interpretation, MedicationRequest, recursive and narrative-only sections - with canonical
  text forms and boundary-aware matching (`5 mg` no longer matches inside `25 mg`, nor `confirmed` inside
  `unconfirmed`); results report coverage/accuracy, context and unsupported-assertion facets and the limitation of
  text-based checks. Fixtures gained these elements and a plan-of-care section; renderer `scdpoc-render-3`.
- **Changed (A-04):** MHD DocumentReference ids are server-assigned and unrelated to the entryUUID; `identifier` carries
  typed `entryUUID` and `uniqueId` slices (`IHE.MHD.MHDIdentifierType`), `masterIdentifier` is typed `uniqueId`;
  `identifier` and `_id` search; `relatesTo` uses server ids. Clients find the projection by identifier search.
- **Added (A-06):** lifecycle state model (`lifecycle.py`) - issue, replace for update, replace for correction
  (`amended`), withdraw - with ITI-57 Update Document Set (UpdateAvailabilityStatus, atomic) for withdrawal,
  notification-required events naming known recipients, and UI buttons for correction and withdrawal.
- **Added (A-05):** receiver check VB-7 - document provenance (author, custodian, issuing organisation, content date,
  identifiers) established from the received IPS alone; entry-level provenance reported separately.
- **Added (A-09):** hash-chained, append-only preservation event log (eventId, type, time, agent, artefact, digest,
  outcome, evidence, prevHash) kept outside the artefacts, with the issuance-record digest in each issuance event;
  `scdpoc fixity` and `POST /api/demo/preservation/fixity` detect a modified archived copy and record the failure.
- **Changed (A-03, A-08, A-10):** the Checker is driven by the profile's rule catalogue (`conformance/rules.json`): it
  reports every rule applicable to the claimed classes and options, per verification method, with a verdict
  (`conformant` / `incomplete` / `non-conformant`); claim checking (CON-01..05), declared inspections, live and vector
  evidence. New vectors V-02 and I-10 to I-16; `scdpoc check-vectors`; live scenarios L-01 to L-12
  (`scdpoc live-check`); the demonstrator's own claim in `conformance/claims/`.
- **Changed (A-07):** validator versions come from `conformance/dependencies.json` (HL7 FHIR validator 7.0.1 from its
  release tag; veraPDF 1.30.3, verified after install); CI no longer downloads "latest". CI writes an evidence
  manifest (CHK-06).
- **Known gaps:** independent HL7 validator and veraPDF reports for this release are not yet available; terminology
  versions are not recorded (PRES-01); the Protected Preservation option is not claimed (the chain anchor is local).

## 0.3.0 - 2026-10-08

Corrections from the review of the IPS Preservation Envelope Profile 0.1.0 (ADR-008).

- **Fixed:** IPS DocumentEntry format code is now the sIPS code `http://hl7.org/fhir/uv/ips/StructureDefinition/Bundle-uv-ips` (system `urn:ietf:rfc:3986`).
- **Fixed:** XFRM direction - the envelope is the transformation (source), the IPS the target.
- **Changed:** one issuance is one ITI-41 submission; the registry validates before any bytes are stored; replacement is RPLC on the IPS, and the previous envelope is deprecated as its transformation.
- **Added:** rendition fidelity contract (`fidelity.py`) derived independently of the renderer, with section-scoped completeness and foreign-code checks; renderer shows statuses, severity and attestation (`scdpoc-render-2`).
- **Added:** practitioner author and legal attester in the IPS; provenance roles, replacement reason, renderer and validation evidence in the issuance record; XMP `dc:identifier` and `dc:language`; fixity-check and issuance preservation events in the audit log.
- **Added:** offline Checker (`scdpoc check`) and byte-reproducible conformance vectors (`scdpoc build-vectors`); CI runs the Checker on every vector with veraPDF and the HL7 FHIR validator.
- **Changed:** sRGB output intent made deterministic; fixture digests regenerated.

## 0.2.0 - 2026-10-07

Simulated cross-border exchange (ADR-007).

- Second synthetic jurisdiction (B, de-DE) with its own patient index and namespace (`2.999.2.1`).
- Jurisdiction A responding gateway: ITI-55 XCPD (exact, unambiguous demographic match only) and ITI-38/39 XCA, with a home-community policy that releases only current IPS documents; the PDF/A envelope and superseded versions are refused.
- Jurisdiction B initiating gateway, six-point verification of received documents, German rendition from synthetic designations with untranslated codes flagged and free text passed through, and a write-once PDF/A-3b custody copy.
- `SimulatedNcpAdapter` pivot check against a synthetic agreed catalogue.
- Renderers accept localised captions (`render/labels.py`); English output unchanged.
- EHDS register: new `simulated` status; items XB-02, XB-03, TERM-05.
- `build-fixtures` also emits localised custody envelopes so CI validates them with veraPDF.
- Tests XB-AT-01..09; scripted demo extended.

## 0.1.0 - 2026-10-07

First public demonstrator release.

- IPS composition from three synthetic fixtures; offline pre-flight validation from the CC0 IPS package digest; HL7 FHIR validator integration.
- Deterministic HTML and PDF renditions from one view model (ADR-001).
- PDF/A-3b envelope with the IPS as an Associated File (`Source`); extraction, byte-identity and rendition checks; built-in pre-flight; veraPDF integration (ADR-002).
- XDS.b Registry and Repository (ITI-41, ITI-18, ITI-43) with SOAP 1.2/MTOM, affinity-domain policy, RPLC and XFRM handling (ADR-003).
- MHD façade (ITI-67, ITI-68) and sIPS-aligned direct IPS retrieval; on-demand `Patient/$summary`.
- Configuration-driven EHDS readiness preview behind an export adapter boundary (ADR-004).
- Tamper simulations, audit log, demonstrator UI, Docker Compose, CI with evidence bundle and SBOM.

Pinned: HL7 FHIR IPS 2.0.1 (validator target; pre-flight digest from 2.0.0), FHIR 4.0.1, MHD 4.2.4, sIPS 1.0.0, PDF/A-3b.

Known: external validators (HL7 FHIR validator, veraPDF) first execute in CI; see README "Release status".
