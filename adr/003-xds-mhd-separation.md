# ADR-003: Two DocumentEntries per issuance; XDS.b as the sharing model, MHD as a façade

- Status: Accepted
- Date: 2026-10-07

## Context

Consumers that want computable data must not have to unpack a PDF (finding
F-001), and the envelope must not be labelled with the IPS format code (F-006).
XDS.b is the mature, Final Text sharing model; MHD and sIPS are Trial
Implementation.

## Decision

Each issuance registers two DocumentEntries, in two ITI-41 submissions:

| Order | DocumentEntry | mimeType | formatCode | Associations |
|---|---|---|---|---|
| 1 | PDF/A-3b envelope (preserved package) | `application/pdf` | `urn:scdpoc:format:pdfa3b-ips-envelope:v1` (local, scheme 2.999.1.5) | HasMember; RPLC → previous envelope |
| 2 | IPS projection | `application/fhir+json` | `urn:ihe:pcc:ips:2020` | HasMember; **XFRM → envelope** |

- The envelope is registered first: preservation precedes exchange. The IPS
  projection is declared a transformation (XFRM) of the preserved package, which
  matches the authority rule (it is byte-identical to the envelope's Associated
  File and could be re-derived from it).
- Replacement uses RPLC on the envelope. The registry deprecates the replaced
  envelope **and its transformations**, so the old IPS projection is deprecated
  with it; both remain retrievable.
- MHD (ITI-67/68) is a façade over the same registry and repository, mapping
  each DocumentEntry to a DocumentReference (`relatesTo` carries `replaces` /
  `transforms`). The grouping with the XDS Consumer is in-process in this
  release.
- The affinity domain policy (`config/affinity-domain.yml`) enforces that each
  mimeType carries its own governed formatCode; a PDF labelled with the IPS
  format code is rejected with `XDSRegistryMetadataError`.

## Consequences

- An IPS-only consumer filters by formatCode and never receives `application/pdf`.
- Two submissions are not atomic. If the second fails, the envelope is
  registered without its projection; the demo reports the failure. A production
  design would use a single submission or compensating logic.
- The registry/repository are demonstrator implementations, not
  Connectathon-tested actors. Replace them with production XDS infrastructure
  (or an IHE-compliant library) behind the same ITI transactions.
