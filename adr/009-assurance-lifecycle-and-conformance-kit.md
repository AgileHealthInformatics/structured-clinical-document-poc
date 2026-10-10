# ADR-009: Honest assurance, a complete fidelity contract, lifecycle states and a catalogue-driven Checker

- Status: Accepted
- Date: 2026-10-08
- Release: 0.4.0 (implements IPS Preservation Envelope Profile 0.3.0)
- Supersedes: the automatic legal attester of [ADR-008](008-corrected-ihe-binding-and-fidelity.md); the MHD
  resource-id convention of [ADR-003](003-xds-mhd-separation.md)

## Context

A second independent review (of profile 0.2.0) concluded that the main remaining weakness was "a technically
well-formed, cryptographically intact and correctly exchanged clinical document whose visible meaning or claimed
clinical authority has not been adequately assured". Its claims were checked before acting:

- The demonstrator did record a synthetic practitioner as legal attester on every issuance. A generated identifier is
  not evidence that anyone attested anything.
- The fidelity contract covered `dosage.text` but not dose, route or timing; matching on whitespace-stripped text let
  `5 mg` match inside `25 mg` and `confirmed` inside `unconfirmed`, so a changed value could pass.
- MHD 4.2.4 slices `DocumentReference.identifier` by type into `entryUUID` and `uniqueId` and makes
  `masterIdentifier` a typed uniqueId. The façade derived the resource id from the entryUUID and carried an untyped
  identifier.
- The 0.2.0 Checker only reported the rules it evaluated and could not say "incomplete".
- While pinning versions it emerged that ITI-57 is defined by the XDS Metadata Update supplement (Rev 1.14, Trial
  Implementation), not the ITI TF Final Text.

## Decision

1. **Assurance.** A composed summary is a *preserved snapshot*: author = Device, no attester, no
   `legalAuthenticator`, and the pages say "Machine-generated preserved snapshot - not clinically attested".
   Attestation is a separate action that creates a new draft; the issuance record holds the evidence (attester,
   time, method, statement, digest of the reviewed draft) and a preservation event records it. The demonstrator's
   attestation is performed by a synthetic user and says so.
2. **Fidelity.** `fidelity.py` derives a fact per populated contract element, in the canonical text forms of the
   profile (implemented independently of the renderer), and matches them whitespace-tolerantly but on token
   boundaries, within each entry's region, recursively over sections. Results report the facets separately and state
   that text checks cannot establish clinical semantic equivalence.
3. **MHD identity.** Resource ids are random, server-assigned and stored in a mapping table; registry identifiers are
   typed slices searchable with `identifier`. The demonstrator's own clients find documents by identifier search and
   follow `content.attachment.url`.
4. **Lifecycle.** `lifecycle.py` holds the profile's state-transition table and refuses unlisted transitions.
   Withdrawal is one ITI-57 Update Document Set with an UpdateAvailabilityStatus association per entry, validated in
   full before any change is applied. Corrections and withdrawals record a notification-required event naming the
   communities the gateway audit shows received the projection.
5. **Preservation.** Events live in an append-only JSON-lines log, each linked to its predecessor by SHA-256, with a
   chain-head anchor file. The anchor is local, so the Protected Preservation option is not claimed. Fixity checking is
   a separate function that reads only files and can run without the server.
6. **Conformance kit.** The specification build emits `rules.json`; this repository vendors it and the Checker
   evaluates exactly the rules applicable to a claim, by method (artefact, claim, live, vectors, inspection), and
   gives a verdict. Live scenarios drive a fresh in-process instance through the real SOAP and FHIR endpoints.

## Consequences

- The demonstrator's own claim is evaluated as **non-conformant** in environments without the external validators
  (SRC-04, ENV-08) and because terminology versions are not recorded (PRES-01). This is the intended, honest result.
- Fixture IPS digests changed (Device author, new elements); the renderer version is `scdpoc-render-3`.
- Code that built MHD URLs from entryUUIDs must search by identifier instead.
- The live scenarios are a driver for this implementation; another implementation needs its own driver for the same
  scenario definitions.
