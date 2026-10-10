# ADR-008: Corrected IHE binding, atomic issuance and an independent fidelity check

- Status: Accepted
- Date: 2026-10-08
- Release: 0.3.0
- Supersedes: the registration table and replacement rule of [ADR-003](003-xds-mhd-separation.md)

## Context

An independent review of the IPS Preservation Envelope Profile 0.1.0 (derived from this demonstrator) found
defects that were also present in the code. The factual claims were checked against the sources before acting:

- **Format code.** IHE sIPS 1.0.0 and the IHE FormatCode vocabulary define the IPS format code as
  `http://hl7.org/fhir/uv/ips/StructureDefinition/Bundle-uv-ips` in code system `urn:ietf:rfc:3986`. The
  demonstrator used `urn:ihe:pcc:ips:2020`, which appears in neither.
- **XFRM direction.** ITI TF-3 (Rev 20.2) makes the new, transformed document the `sourceObject`. The pages of the
  envelope are rendered from the IPS, so the envelope is the transformation. ADR-003 had it the other way round.
- **Atomicity.** The two DocumentEntries were registered in two submissions, so a failure could leave an envelope
  without its IPS.
- **Replacement.** RPLC was applied to the envelope only, so an IPS-only consumer could not follow the chain.
- **Fidelity.** The rendition check reused the renderer's own view model, so anything the renderer omitted was
  also missing from the expectation, and the fact set omitted qualifiers such as statuses.

## Decision

1. The IPS DocumentEntry carries the sIPS format code (`config/affinity-domain.yml`); the envelope keeps its local
   code.
2. One issuance is one ITI-41 SubmissionSet: both DocumentEntries, `HasMember` for each, `XFRM` envelope → IPS,
   and for a replacement `RPLC` new IPS → previous IPS. The previous envelope is deprecated by the registry as a
   transformation of the replaced IPS (ITI TF-3 behaviour, now including APND).
3. The registry exposes a side-effect-free `validate()`; the repository calls it before storing any bytes, so a
   rejected submission leaves neither registry entries nor stored objects.
4. `fidelity.py` derives the profile's fidelity contract directly from the FHIR resources, checks it section by
   section against the page text, and flags codes that appear in the wrong section or that no entry carries. The
   renderer shows statuses, severities and attestation so that it can meet the contract.
5. The IPS carries a practitioner author and a legal attester; the organisation is the custodian. The issuance
   record keeps these roles, the renderer, the replacement reason and the validation evidence.
6. A standalone offline Checker (`scdpoc check`) and deterministic test vectors (`conformance/vectors`, regenerated
   with `scdpoc build-vectors`) implement the profile's conformance kit.

## Consequences

- IPS bytes and envelopes for the fixtures changed; `fixtures/expected/digests.json` was regenerated deliberately.
- The renderer version is now `scdpoc-render-2`.
- The sRGB ICC output intent is now deterministic (fixed header date, profile ID zeroed), so envelopes and vectors are
  byte-reproducible across runs.
- The Checker is written by the same project as the demonstrator. Its reports are evidence, not independent
  assurance (profile rule CHK-05).
- Not implemented: correction and withdrawal lifecycles, On-Demand DocumentEntries and snapshots, protection of the
  issuance record against alteration, and a deployment preservation declaration. The profile's rule index records
  these.
