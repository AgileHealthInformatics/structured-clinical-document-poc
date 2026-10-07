# NOTICE

EHDS Structured Clinical Document PoC
Copyright 2026 the EHDS Structured Clinical Document PoC contributors.

Licensed under the Apache License, Version 2.0 (see `LICENSE`).

## Third-party software (runtime)

Installed as dependencies, not vendored. Versions tested are pinned in `requirements.lock`; CI publishes a CycloneDX SBOM with every run.

| Component | Licence | Use |
|---|---|---|
| FastAPI, Pydantic | MIT | HTTP application shell |
| Starlette, Uvicorn, httpx | BSD-3-Clause | ASGI server and HTTP client |
| lxml | BSD-3-Clause | XML (ebRIM, SOAP) |
| ReportLab | BSD | PDF page rendering; Bitstream Vera fonts shipped with ReportLab (Bitstream Vera licence) are embedded in generated PDFs |
| pikepdf (qpdf) | MPL-2.0 (qpdf: Apache-2.0) | PDF/A-3 packaging and Associated Files |
| pypdf | BSD-3-Clause | Page-text extraction for the rendition check |
| Pillow (LittleCMS) | MIT-CMU (LittleCMS: MIT) | Generates the sRGB ICC output-intent profile at run time |
| PyYAML | MIT | Configuration |

## External tools (not distributed; optional or CI only)

| Tool | Licence | Use |
|---|---|---|
| HL7 FHIR Validator (`validator_cli.jar`, hapifhir/org.hl7.fhir.core) | Apache-2.0 | AT-01 conformance validation |
| veraPDF | GPLv3+ / MPLv2+ (dual) | AT-05 PDF/A validation; invoked as a separate process or service |

## Standards content

- `config/ips-profile-digest.json` is derived from the HL7 FHIR IPS Implementation Guide package (`hl7.fhir.uv.ips`), published under CC0-1.0. Provenance (version, tarball SHA-256) is recorded in the file.
- ISO 27269, ISO 19005-3 and other ISO standards are referenced by title only. No ISO text is reproduced.
- IHE profile names, transaction identifiers and metadata UUIDs are used as interoperability identifiers. IHE technical frameworks are not reproduced.
- Clinical codes (SNOMED CT, LOINC, WHO ATC) appear only as individual identifiers in synthetic fixtures. No terminology release files are distributed. Use of these code systems in a real service requires the appropriate licences.

## Synthetic data

All patients, organisations and identifiers in `fixtures/` are fictional. Identifiers use the `2.999` example OID arc. Any resemblance to real persons is coincidental.
