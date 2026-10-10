# Standards baseline: what is implemented, and how far

This page is deliberately explicit about depth. "Implemented" means exercised by tests in this repository; it does not mean conformance-tested.

## HL7 FHIR IPS (target 2.0.1, FHIR R4 4.0.1)

Implemented
- Document Bundle with `identifier`, `timestamp`, Composition as first entry; `fullUrl` as `urn:uuid`.
- Composition `type` LOINC 60591-5; required sections Problems (11450-4), Allergies (48765-2), Medications (10160-0); optional Immunizations (11369-6), Results (30954-2) and a narrative-only Plan of care (18776-5).
- Composition author is a Device for a preserved snapshot; `attester` (mode legal, time, Practitioner) only after an explicit attestation action; `status` `final`, or `amended` for a correction; `relatesTo` replaces.
- Entry qualifiers in the fidelity contract: Condition verification status and severity; AllergyIntolerance verification status, criticality, reaction severity; MedicationStatement and MedicationRequest dosage text, dose, route (EDQM), timing, as-needed, intent; Observation interpretation.
- Section `emptyReason` (`nilknown`) for empty required sections; explicit "No known allergy" (SNOMED CT 716186003) entries.
- `meta.profile` claims for IPS profiles; generated XHTML section narratives.
- Pre-flight checks top-level required elements from the IPS package (digest generated from `hl7.fhir.uv.ips` 2.0.0, CC0).

Not implemented / delegated
- Slicing, value-set bindings and FHIRPath invariants are checked only by the HL7 FHIR validator (CI).
- No IPS XML serialisation; JSON only.

## IHE XDS.b (ITI TF Rev 20.2, November 2025)

Implemented
- ITI-41 Provide and Register Document Set-b (SOAP 1.2, WS-Addressing, MTOM/XOP; inline base64 also accepted).
- ITI-43 Retrieve Document Set (MTOM response; PartialSuccess when some documents are missing).
- ITI-18 stored queries: FindDocuments (patient, status, formatCode), GetDocuments, GetRelatedDocuments; LeafClass and ObjectRef.
- Registry: required metadata, affinity-domain code policy, duplicate uniqueId detection, SubmissionSet/DocumentEntry patient consistency, HasMember, RPLC (with deprecation of XFRM and APND documents of the replaced document), XFRM with the transformed document as source.
- One issuance is one SubmissionSet; the registry validates the whole submission before the repository stores any bytes.
- Repository: SHA-1 `hash` and `size` computed and verified; write-once storage.

Not implemented
- ITI-42 as a network transaction (in-process), metadata updates other than availability status, APND/signs associations, folders, on-demand documents, XCA, ATNA, TLS/mutual authentication, full stored-query parameter set.
- Not tested at an IHE Connectathon or with Gazelle.

## IHE XDS Metadata Update (supplement Rev 1.14, Trial Implementation)

Implemented
- ITI-57 Update Document Set limited to UpdateAvailabilityStatus of DocumentEntries (sourceObject the SubmissionSet, targetObject the entry, `OriginalStatus` and `NewStatus` slots), used for withdrawal. All changes in one submission are validated before any is applied; a mismatched `OriginalStatus` returns `XDSMetadataUpdateError`.

Not implemented
- Other metadata updates (attribute changes, association status), the Update Responder grouping options, Restricted Metadata Update.
- ITI-57 is defined by the supplement, not by the ITI TF Final Text (Volume 2 marks it as Trial Implementation).

## IHE MHD 4.2.4 (Trial Implementation)

Implemented
- ITI-67 search by `patient.identifier`, `status`, `format`, `identifier` (entryUUID or uniqueId) and `_id`; DocumentReference mapping from DocumentEntry (typed `masterIdentifier`; `identifier` slices `entryUUID` and `uniqueId` typed with `IHE.MHD.MHDIdentifierType`; status, type, category, securityLabel, content.attachment with url/size/hash, content.format, context, relatesTo by server id; `authenticator` as a contained Practitioner from `legalAuthenticator`).
- Times (profile 0.4.0 time mapping): `content.attachment.creation` is `creationTime`, the clinical content time
  (`Composition.date`); `DocumentReference.date` is the time the reference was created, here the SubmissionSet
  `submissionTime`. `author` shows the author institution and, for a software author, the software agent named in
  `authorPerson`.
- Lifecycle: `status` is `current` or `superseded` (the only values MHD 4.2.4 allows); the issuer's lifecycle state
  (`issued`, `replaced-for-update`, `replaced-for-correction`, `withdrawn`) is carried in the profile extension
  `urn:oid:2.25.11064312502901710892401295388462879468.1`, so a withdrawal is not presented as a replacement.
- Resource ids are assigned by the server and are unrelated to the entryUUID.
- ITI-68 retrieve via `Binary/{id}` (raw bytes, or a FHIR Binary resource when `Accept: application/fhir+json` asks for a non-FHIR document).

Not implemented
- ITI-65 Provide Document Bundle, ITI-66 List search, chained `subject` Patient resolution, OAuth/IUA.

## IHE sIPS 1.0.0 (Trial Implementation)

- IPS available as its own DocumentEntry/DocumentReference with the sIPS format code `http://hl7.org/fhir/uv/ips/StructureDefinition/Bundle-uv-ips` (system `urn:ietf:rfc:3986`) and `application/fhir+json`.
- sIPS 1.0.0 does not pin an IPS version; interoperability with IPS 2.0.1 is assumed here, not tested.
- On-demand current summary through `Patient/$summary` (the IPS IG operation), tagged and never registered.

## IHE XCPD (ITI-55) and XCA (ITI-38, ITI-39) - v0.2

Implemented
- ITI-55 synchronous query by demographics (PRPA_IN201305UV02 → PRPA_IN201306UV02): living subject name, birth time, administrative gender, requester's local id; `OK` with one patient and custodian community, or `NF`.
- ITI-38 FindDocuments and GetDocuments with `home` on returned objects; ITI-39 with `HomeCommunityId`, MTOM response.
- Home-community policy: only current documents with the IPS format code cross the gateway.

Not implemented
- XCPD deferred mode, revoke, health data locator, probabilistic matching; XCA asynchronous mode; on-demand documents across the gateway; TLS, SAML/IUA assertions, purpose of use; consent-gated discovery.

## PDF/A-3b (ISO 19005-3, based on ISO 32000-1 / PDF 1.7)

Implemented
- XMP `pdfaid:part=3`, `pdfaid:conformance=B`; Info/XMP consistency; sRGB output intent; all fonts embedded; trailer `/ID`; no encryption, JavaScript or transparency groups.
- Associated File in `/AF` and `EmbeddedFiles` with `/AFRelationship /Source`, MIME subtype, `/Params` (`Size`, `ModDate`, `CreationDate`, `CheckSum`), `/F` and `/UF`.

Evidence
- Built-in pre-flight: every test run (subset only).
- veraPDF PDF/A-3B profile: CI `validators` job.

## EHDS (Regulation (EU) 2025/327), EEHRxF, MyHealth@EU

- Non-normative readiness register (`config/ehds-readiness.yml`) evaluated against the IPS projection.
- v0.2: simulated cross-border exchange between two synthetic jurisdictions (`simulated` status), with a simulated NCP pivot check against a synthetic catalogue.
- No EEHRxF profile, no NCPeH connectivity, no real cross-border identity or trust.
- The eHealth Network Patient Summary guideline and EHDS implementing acts must be consulted directly by adopters; the register records where the PoC's position depends on them.
