# Adopting and extending the demonstrator

The demonstrator is designed so an EHDS-facing service can adopt the *pattern* - preserve the clinical document, exchange the computable summary - while replacing the parts that are deliberately demonstration-grade.

## Extension points

### 1. Source data → IPS (`ips/composer.py`)

`compose_ips(source, settings, *, document_id, issued, series_id, version, replaces_document_urn, attestation, status) -> ComposedDocument`

Replace the fixture-shaped `source` mapping with your clinical data extract (or an existing IPS generator). Keep:
- serialising once (`serialise`) and never regenerating issued bytes;
- `Composition.relatesTo` (`replaces`) for new versions;
- section narratives generated from `ips/view.py`;
- a Device author and **no attester** unless a real attestation action has taken place. Attest with
  `ips/attestation.py`: the attester reviews a validated draft, the attestation is recorded against the draft's
  attested content digest (`attested_content_digest`), and `add_attestation` produces the final IPS - the draft plus
  the attestation and nothing else. Keep the evidence (who, when, method, statement, attested content digest) in the
  issuance record, and refuse to publish content whose digest differs (profile PROV-02, PROV-04, PROV-08, IPS-07).
- `Composition.date` as the clinical content time, distinct from `Bundle.timestamp` (issuance) and `attester.time`
  (profile PROV-09 and the time mapping).

Remove `safety.load_fixture` only inside a governed environment with real information-governance controls.

### 2. Validation (`ips/validator.py`)

`validate_ips(bundle, json_bytes, settings) -> ValidationReport`. For production, set `SCDPOC_REQUIRE_HL7_VALIDATOR=true` (or call your validation service) and give the validator a terminology server so bindings are checked. Add national profiles by extending the `-ig` arguments.

### 3. Rendition (`render/`)

Any renderer is acceptable if it consumes only `SummaryView` (or the Bundle) and the IC-7 rendition check keeps passing. Clinical presentation (ordering, emphasis, translations) belongs here.

### 4. Preservation envelope (`pdfa/packager.py`)

`build_envelope(page_pdf, ips_json, *, document_id, issued, title, author, subject) -> Envelope`. To add a qualified electronic seal, sign **after** packaging (PAdES), re-run veraPDF, and extend `integrity.py` with a signature check. Set `SCDPOC_REQUIRE_VERAPDF=true`.

### 5. Document sharing (`xds/`)

Point `SCDPOC_XDS_ENDPOINT_BASE` at production XDS infrastructure: the demo's Document Source and Consumer (`xds/client.py`) speak standard ITI-41/18/43. Move `config/affinity-domain.yml` values to your affinity domain's governed codes, keeping **distinct format codes** for the envelope and the IPS.

To persist metadata in PostgreSQL instead of SQLite, implement the `RegistryStore` and `ObjectStore` methods in `xds/store.py`.

### 6. Lifecycle, preservation events and fixity (`lifecycle.py`, `preservation.py`)

Keep the state table: a correction is a new issuance with status `amended`; a withdrawal is an ITI-57
UpdateAvailabilityStatus of both entries. Replace the notification-required event with your notification process.
For the Protected Preservation option, hold the event-chain anchor (`preservation/anchor.json`) with an independent
party, or seal the log, and schedule `scdpoc fixity` (or your archive's fixity service) and record its results as
events.

### 7. Conformance claims (`conformance/`)

Copy `conformance/claim.example.json`, state your classes, options, the actor bindings you implement for each class
(only their transactions are required of you), dependency versions with their statuses (`exact`,
`permitted-alternative`; an `unsupported-deviation` is never eligible for a conformant verdict), declarations and
declared inspections (by obligation id, for example `REN-14` or `PROV-02.c`), and run
`scdpoc check` with your artefacts, your live-scenario results and the vector results. Replace the live-scenario
driver (`conformance_kit.py`) with one that drives your system through the same scenario definitions; key its results
by obligation id (`REN-04.a`, not `REN-04`), or the Checker rejects them.

### 8. EHDS export (`ehds/adapter.py`)

Implement `ExportAdapter.export(ips_bytes) -> ExportResult` for the adopted EEHRxF specification and your NCPeH's requirements (`MyHealthEuAdapter` marks the spot). Keep the readiness register under change control and update it when implementing acts or national rules change.

### 9. Cross-border (`crossborder/`)

- The responding gateway's release policy is configuration (`config/crossborder/communities.yml`). Keep the envelope out of the exposed format codes.
- Replace `find_match` with your master patient index and matching rules; keep "disclose nothing on ambiguity".
- Replace `config/crossborder/designations-*.yml` and `catalogue.yml` with mandated catalogues and a transcoding/terminology service; free text must stay flagged, never machine-translated silently.
- If MyHealth@EU's target is FHIR-based, implement the same B1-B4 behaviour over FHIR (for example PDQm and MHD across a gateway) and run the XB acceptance tests against it.

## Production gaps you must close

Identity (patient and professional), authentication and authorisation (e.g. IUA/OAuth, mutual TLS), consent and EHDS access rights, ATNA-grade audit, terminology licensing and services, signatures/seals, clinical safety case, retention and records-management law, cross-border trust, availability, performance and disaster recovery.

## Validating a different implementation against this one

The fixtures, `fixtures/expected/digests.json` and the acceptance tests are language-neutral evidence. A Java (IPF/HAPI/PDFBox) or other implementation can be checked by producing envelopes for the same fixtures and running the same invariants: byte-identical embedded IPS, veraPDF PDF/A-3B pass, distinct format codes, equivalent XDS/MHD discovery and RPLC/XFRM lifecycle.
