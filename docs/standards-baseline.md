# Standards baseline: what is implemented, and how far

This page is deliberately explicit about depth. "Implemented" means exercised by tests in this repository; it does not mean conformance-tested.

## HL7 FHIR IPS (target 2.0.1, FHIR R4 4.0.1)

Implemented
- Document Bundle with `identifier`, `timestamp`, Composition as first entry; `fullUrl` as `urn:uuid`.
- Composition `type` LOINC 60591-5; required sections Problems (11450-4), Allergies (48765-2), Medications (10160-0); optional Immunizations (11369-6) and Results (30954-2).
- Section `emptyReason` (`nilknown`) for empty required sections; explicit "No known allergy" (SNOMED CT 716186003) entries.
- `meta.profile` claims for IPS profiles; generated XHTML section narratives.
- Pre-flight checks top-level required elements from the IPS package (digest generated from `hl7.fhir.uv.ips` 2.0.0, CC0).

Not implemented / delegated
- Slicing, value-set bindings and FHIRPath invariants are checked only by the HL7 FHIR validator (CI).
- No IPS XML serialisation; JSON only.

## IHE XDS.b (ITI TF Rev 20.2)

Implemented
- ITI-41 Provide and Register Document Set-b (SOAP 1.2, WS-Addressing, MTOM/XOP; inline base64 also accepted).
- ITI-43 Retrieve Document Set (MTOM response; PartialSuccess when some documents are missing).
- ITI-18 stored queries: FindDocuments (patient, status, formatCode), GetDocuments, GetRelatedDocuments; LeafClass and ObjectRef.
- Registry: required metadata, affinity-domain code policy, duplicate uniqueId detection, SubmissionSet/DocumentEntry patient consistency, HasMember, RPLC (with deprecation of transforms of the replaced document), XFRM.
- Repository: SHA-1 `hash` and `size` computed and verified; write-once storage.

Not implemented
- ITI-42 as a network transaction (in-process), ITI-57 metadata update, APND/signs associations, folders, on-demand documents, XCA, ATNA, TLS/mutual authentication, full stored-query parameter set.
- Not tested at an IHE Connectathon or with Gazelle.

## IHE MHD 4.2.4 (Trial Implementation)

Implemented
- ITI-67 search by `patient.identifier`, `status`, `format`; DocumentReference mapping from DocumentEntry (masterIdentifier, status, type, category, securityLabel, content.attachment with url/size/hash, content.format, context, relatesTo).
- ITI-68 retrieve via `Binary/{id}` (raw bytes, or a FHIR Binary resource when `Accept: application/fhir+json` asks for a non-FHIR document).

Not implemented
- ITI-65 Provide Document Bundle, ITI-66 List search, chained `subject` Patient resolution, OAuth/IUA.

## IHE sIPS 1.0.0 (Trial Implementation)

- IPS available as its own DocumentEntry/DocumentReference with format code `urn:ihe:pcc:ips:2020` and `application/fhir+json`.
- On-demand current summary through `Patient/$summary` (the IPS IG operation), tagged and never registered.

## IHE XCPD (ITI-55) and XCA (ITI-38, ITI-39) - v0.2

Implemented
- ITI-55 synchronous query by demographics (PRPA_IN201305UV02 → PRPA_IN201306UV02): living subject name, birth time, administrative gender, requester's local id; `OK` with one patient and custodian community, or `NF`.
- ITI-38 FindDocuments and GetDocuments with `home` on returned objects; ITI-39 with `HomeCommunityId`, MTOM response.
- Home-community policy: only current documents with the IPS format code cross the gateway.

Not implemented
- XCPD deferred mode, revoke, health data locator, probabilistic matching; XCA asynchronous mode; on-demand documents across the gateway; TLS, SAML/IUA assertions, purpose of use; consent-gated discovery.

## PDF/A-3b (ISO 19005-3)

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
