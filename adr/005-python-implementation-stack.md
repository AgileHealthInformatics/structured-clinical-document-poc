# ADR-005: Python implementation stack (deviation from the reference design)

- Status: Accepted
- Date: 2026-10-07

## Context

The reference design recommends Java 21 with Spring Boot, Open eHealth IPF,
HAPI FHIR, Apache PDFBox and veraPDF, explicitly "not because Java is
intrinsically superior" but to minimise glue code. This release was produced in
a build environment without access to Maven Central, so a Java implementation
could not have been compiled or tested before publication. An untested
reference implementation would undermine the project's core claim that it
proves invariants rather than renders screens.

## Decision

Implement v0.1 in Python 3.12+:

| Concern | Reference design | This release |
|---|---|---|
| Application shell | Spring Boot | FastAPI + Uvicorn |
| IHE XDS.b | Open eHealth IPF | Small, explicit ITI-41/18/43 SOAP + MTOM implementation (`xds/`) |
| FHIR model | HAPI FHIR | Plain JSON documents; offline pre-flight from the CC0 IPS package digest |
| FHIR validation | HAPI validator | Official HL7 FHIR validator CLI (Java) in CI and optional runtime |
| PDF construction | PDFBox | ReportLab (pages) + pikepdf/qpdf (PDF/A-3 packaging) |
| PDF/A validation | veraPDF | veraPDF (CLI or REST), unchanged |
| Metadata store | PostgreSQL | SQLite behind a small store interface (ADR-006) |
| Frontend | Static HTML/JS | Static HTML/JS, unchanged |

## Consequences

- Every acceptance test that does not need an external validator ran green
  before release; validator-dependent tests run in CI.
- The XDS actors are less complete than IPF (no ITI-42 endpoint, a subset of
  stored queries, no ATNA/TLS). They are written to be read and replaced.
- Java remains the natural choice for a production-grade successor; the
  standards interfaces (ITI transactions, FHIR resources, PDF/A artefacts) are
  language-neutral, so a Java implementation can be validated against this
  release's fixtures and acceptance tests.
