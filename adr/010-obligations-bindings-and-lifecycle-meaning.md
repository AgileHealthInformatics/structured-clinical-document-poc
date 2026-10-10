# ADR-010: Normative obligations, actor bindings, lifecycle meaning and a verifiable attestation

- Status: Accepted
- Date: 2026-10-09
- Release: 0.5.0 (implements IPS Preservation Envelope Profile 0.4.0)
- Amends: [ADR-009](009-assurance-lifecycle-and-conformance-kit.md) (Checker, attestation, MHD lifecycle)

## Context

A third review (of profile 0.3.0, items IMP-01 to IMP-09) found that the profile and its kit were sound in structure
but imprecise where an implementer or assessor needs certainty. Its claims were checked against the code before
acting:

- Several rules mixed MUST and SHOULD in one sentence, and the Checker gave the rule one outcome, so an advisory
  failure could make a claim non-conformant, or a mandatory failure could hide behind a rule-level summary.
- Classes required every transaction of their family: a Receiver that only uses MHD was required to implement XCA.
- MHD 4.2.4 `DocumentReference.status` is `current` or `superseded` only. A withdrawn issuance and a replaced one
  looked identical in the FHIR view, and the receiver could not tell which had happened.
- `creationTime` was the issuance time, MHD `date` repeated it, and the software author was described through
  `authorRole`, which ITI TF Vol 3 defines as the author's role in the documented act.
- The attestation evidence held the SHA-256 of the reviewed draft's bytes. The final IPS necessarily differs (new
  identifier, timestamp, attester), so nobody could check from the issued document that it carried the content that
  was attested.
- I-06 expected ENV-10, but a standard font with a standard encoding has a Unicode mapping; the expectation was
  wrong, and nothing in the manifest said why any expectation held.

## Decision

1. **Obligations, not rules, are the unit of evaluation.** The catalogue lists each rule's obligations with one level.
   The Checker evaluates each obligation; mandatory failures decide the verdict, advisory failures are warnings.
   Anything that refers to an obligation, test or outcome the catalogue does not define is rejected (CHK-10).
2. **Claims declare actor bindings** per class; the Checker requires only their transactions.
3. **Lifecycle meaning is explicit in every layer**: XDS status plus the absence of an RPLC successor for a
   withdrawal; an MHD extension carrying the issuer's lifecycle state; gateway metadata and RPLC associations answered
   by identifier; and the receiver's rendition states whether a summary it holds was replaced or withdrawn.
4. **Times and authors follow the profile's mappings**: content time in `creationTime` and MHD `attachment.creation`,
   submission time in MHD `date`, the software author as an XCN software agent, `legalAuthenticator` only for a person
   attester.
5. **Attestation is verifiable from the issued document**: the attested content digest excludes the elements that
   attestation adds, so it is the same over the reviewed draft and the issued IPS. Packaging and publication refuse
   content whose digest differs from the attested one.
6. **Every vector expectation is justified** in the manifest, and the vector set covers the boundary cases the
   review asked for (I-17, I-18, I-19).

## Consequences

- IPS bytes changed (fixtures gained coverage-matrix elements; the rendition shows content and issuance times), so
  `fixtures/expected/digests.json` and the vectors were regenerated deliberately.
- The renderer is `scdpoc-render-4`. Its Details column is the canonical form of the coverage-matrix elements that
  have no column of their own.
- The demonstrator's own claim is still non-conformant, now also because it makes no security declarations
  (SEC-01..03), which only a real deployment can make.
- Independent validator reports remain outstanding (profile issue IR-01).
