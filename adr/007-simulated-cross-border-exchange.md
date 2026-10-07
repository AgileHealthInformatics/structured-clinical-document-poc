# ADR-007: Simulated cross-border exchange between two synthetic jurisdictions

- Status: Accepted
- Date: 2026-10-07
- Release: 0.2.0

## Context

v0.1 proves the pattern inside one affinity domain. The point of EHDS primary
use is that a summary issued in one Member State is read by a clinician in
another. The design's adapter boundary (ADR-004) was built for this step, but
no real NCPeH or MyHealth@EU connection is possible or appropriate in a public
demonstrator.

## Decision

1. **Two synthetic jurisdictions in one process, talking only over HTTP.**
   Jurisdiction A (issuer, `urn:oid:2.999.1.9`, en-GB) and Jurisdiction B
   (consumer, `urn:oid:2.999.2.9`, de-DE, own patient namespace `2.999.2.1`).
   B never touches A's registry, repository or work store; every interaction
   goes through A's responding gateway.
2. **IHE cross-community transactions, demonstrator subsets.** ITI-55 (XCPD)
   for patient discovery by demographics, returning a match only when exactly
   one candidate agrees on family name, first given name, birth date and
   administrative gender; ITI-38 / ITI-39 (XCA) for query and retrieve.
3. **Home-community gateway policy** (`config/crossborder/communities.yml`):
   only current documents with the IPS format code are discoverable or
   retrievable across the border. The PDF/A envelope and superseded versions are
   refused with `XDSDocumentUniqueIdError`. This turns the PoC's central claim -
   the preservation container is not the cross-border protocol - into an
   enforced rule (XB-AT-04, XB-AT-06).
4. **Consumer-side verification, rendition and custody.** B verifies what it
   received (SHA-1/size against advertised metadata, IPS pre-flight, subject
   equals the XCPD-linked patient, current status), renders it in de-DE from
   synthetic designations, flags untranslated codes and passes free text through
   unchanged, and preserves a write-once custody copy: a PDF/A-3b with the
   received IPS embedded (`Source`) and the local rendition as pages.
5. **Simulated NCP pivot check.** `SimulatedNcpAdapter` checks every clinical
   code against a synthetic agreed catalogue. The IPS is released unchanged as
   the pivot. `MyHealthEuAdapter` remains a placeholder.

## Consequences

- The register gains a `simulated` status (XB-02, XB-03, TERM-05). XB-01 (real
  NCPeH connectivity) stays `out-of-scope`.
- Transaction choice is provisional. MyHealth@EU's established infrastructure
  was built around XCA/XCPD-style gateways; its direction of travel towards
  FHIR-based exchange must be checked before this layer is extended. The gateway
  is isolated in `crossborder/` so an equivalent FHIR implementation (for
  example PDQm and MHD across a gateway) can replace it.
- Not implemented: XCPD deferred mode, revoke and probabilistic matching, XCA
  asynchronous mode, TLS/mutual authentication, SAML/IUA assertions, purpose of
  use, patient consent for cross-border access, real terminology catalogues and
  transcoding services, and translation of free text.
- Matching is deliberately conservative: an ambiguous or partial match
  discloses nothing.
