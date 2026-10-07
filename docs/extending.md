# Adopting and extending the demonstrator

The demonstrator is designed so an EHDS-facing service can adopt the *pattern* - preserve the clinical document, exchange the computable summary - while replacing the parts that are deliberately demonstration-grade.

## Extension points

### 1. Source data → IPS (`ips/composer.py`)

`compose_ips(source, settings, *, document_id, issued, series_id, version, replaces_document_urn) -> ComposedDocument`

Replace the fixture-shaped `source` mapping with your clinical data extract (or an existing IPS generator). Keep:
- serialising once (`serialise`) and never regenerating issued bytes;
- `Composition.relatesTo` (`replaces`) for new versions;
- section narratives generated from `ips/view.py`.

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

### 6. EHDS export (`ehds/adapter.py`)

Implement `ExportAdapter.export(ips_bytes) -> ExportResult` for the adopted EEHRxF specification and your NCPeH's requirements (`MyHealthEuAdapter` marks the spot). Keep the readiness register under change control and update it when implementing acts or national rules change.

## Production gaps you must close

Identity (patient and professional), authentication and authorisation (e.g. IUA/OAuth, mutual TLS), consent and EHDS access rights, ATNA-grade audit, terminology licensing and services, signatures/seals, clinical safety case, retention and records-management law, cross-border trust, availability, performance and disaster recovery.

## Validating a different implementation against this one

The fixtures, `fixtures/expected/digests.json` and the acceptance tests are language-neutral evidence. A Java (IPF/HAPI/PDFBox) or other implementation can be checked by producing envelopes for the same fixtures and running the same invariants: byte-identical embedded IPS, veraPDF PDF/A-3B pass, distinct format codes, equivalent XDS/MHD discovery and RPLC/XFRM lifecycle.
