# ADR-002: PDF/A-3b as the preservation envelope, with the IPS as an Associated File

- Status: Accepted
- Date: 2026-10-07

## Context

Records need a durable, human-intelligible artefact. PDF/A-3 (ISO 19005-3)
permits embedded files and the Associated Files mechanism, so a single file can
carry the rendition and its exact structured source (finding F-003).

## Decision

- Conformance level **3b** (visual reproducibility). Level 3a/3u would add
  tagging and Unicode-mapping obligations the PoC does not need to prove.
- The IPS JSON is embedded once, referenced from the catalog `/AF` array and the
  `EmbeddedFiles` name tree, with `/AFRelationship /Source`, MIME subtype
  `application/fhir+json`, and `/Params` (`Size`, `ModDate`, `CheckSum`).
  `Source` is correct because the pages are generated from the IPS.
- All fonts are embedded (Bitstream Vera, shipped with ReportLab); a generated
  sRGB ICC output intent is added; Info dictionary and XMP are kept consistent.
- Output is deterministic (ReportLab invariant mode, `deterministic_id`), so the
  same IPS and renderer version give byte-identical envelopes (AT-03).

## Consequences

- Successful generation is not conformance (F-010). Each envelope is checked by
  a built-in pre-flight (subset) and, where configured, by veraPDF. The UI never
  presents the pre-flight as a conformance claim.
- The envelope is a **preservation** container, not an exchange format. The IPS
  is exchanged directly (ADR-003). The PoC does not claim PDF/A is an EHDS
  exchange format.
- Embedded content is part of the disclosure: authorisation applies to the
  whole container, and the UI always shows the embedded-file inventory.
