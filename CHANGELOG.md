# Changelog

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
