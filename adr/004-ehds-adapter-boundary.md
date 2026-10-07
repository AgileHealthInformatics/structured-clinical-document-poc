# ADR-004: EHDS-specific behaviour sits behind an export adapter boundary

- Status: Accepted
- Date: 2026-10-07

## Context

Detailed EEHRxF and MyHealth@EU requirements are set through EHDS implementing
acts and national implementation. The PoC cannot claim conformance (finding
F-005) and must not need re-engineering when the detail is settled.

## Decision

- `ehds/adapter.py` defines `ExportAdapter.export(ips_bytes) -> ExportResult`.
  Nothing outside `ehds/` depends on EHDS specifics.
- The shipped `ReadinessPreviewAdapter` evaluates a **register held in
  configuration** (`config/ehds-readiness.yml`). Every statement shown to a user
  comes from that file; items that depend on implementing acts, national rules
  or NCPeH onboarding are declared `unresolved` or `out-of-scope`, never inferred.
- `MyHealthEuAdapter` is a deliberate placeholder that raises
  `NotImplementedError` with guidance.
- The preview consumes the IPS projection retrieved through ITI-43, not bytes
  scraped from the PDF.

## Consequences

- Updating the readiness position is a reviewed configuration change, not a
  code change.
- AT-11 asserts that the preview is non-normative, reports every register item
  and contains unresolved items.
