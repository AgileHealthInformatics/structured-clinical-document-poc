# ADR-001: The FHIR IPS Bundle is the authoritative structured source

- Status: Accepted
- Date: 2026-10-07

## Context

The pattern holds two representations of one clinical statement: a computable
HL7 FHIR IPS document and a human-readable rendition. If they can diverge, the
preserved document is untrustworthy (finding F-004, severity Critical).

## Decision

1. The IPS document Bundle is composed first and serialised **once**
   (`ips/composer.py::serialise`). Those exact bytes are validated, embedded and
   exchanged; they are never regenerated.
2. Everything a human sees - the Composition section narratives, the HTML page
   and the PDF pages - is produced from one view model (`ips/view.py`) that is
   derived only from the coded resources. Rendering is one-way: IPS → view → pages.
3. The PDF/A envelope is the authoritative preserved package for the issuance
   event. If the two representations differ, the package is invalid and is not
   published.

## Consequences

- Packaging re-extracts the Associated File and byte-compares it with the
  validated IPS (AT-04), and checks that every clinical fact derived from the
  embedded IPS appears in the PDF page text (integrity check IC-7).
- The rendition check is text-based. It detects omitted or altered facts; it
  does not judge layout or clinical presentation quality.
- Adopters replacing the renderer must keep the one-way rule and the IC-7 check.
