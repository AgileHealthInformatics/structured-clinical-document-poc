# Security policy

## Scope and posture

This repository is a **public demonstrator that must only ever process synthetic data**. Its controls are deliberately demonstration-grade:

- no authentication or authorisation on any endpoint;
- no TLS termination, no ATNA audit, no consent or purpose-of-use enforcement;
- integrity is demonstrated with SHA-256 / SHA-1 digests, not signatures or seals.

It does **not** satisfy EHDS, GDPR, NIS2, clinical-safety (e.g. DCB0129/DCB0160 or equivalents) or production identity and access-control requirements. Do not deploy it where real patient data could reach it, and do not expose it to untrusted networks without understanding that every document it holds is readable by anyone who can reach it.

## Safety rails that are in scope

- Fixtures are loaded only from `fixtures/synthetic-patients/` by a restricted key; arbitrary uploads are not accepted.
- Patient identifiers must use the synthetic assigning authority (`2.999.1.1`) and the `SYN-` prefix; the registry rejects other domains.
- Fixtures are scanned for NHS-number-like, PPSN-like and SSN-like values.
- XML parsing disables external entities and network access.
- Repository objects are write-once.

## Reporting a vulnerability

Please report issues privately using GitHub's **Report a vulnerability** (private security advisory) on this repository rather than a public issue. Include the release or commit, the endpoint or file, and reproduction steps using synthetic data only. **Never include real patient data in a report.**

Reports about weaknesses in the safety rails above, about ways real data could be accepted, or about misleading conformance claims are especially welcome.
