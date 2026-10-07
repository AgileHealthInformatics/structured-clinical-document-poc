# Terminology

No terminology release files are distributed with this project.

The synthetic fixtures use individual codes from:

| System | URI | Used for |
|---|---|---|
| SNOMED CT | `http://snomed.info/sct` | Problems, allergy substances, reactions |
| LOINC | `http://loinc.org` | Document type, section codes, results |
| WHO ATC | `http://www.whocc.no/atc` | Medicines and vaccines |
| UCUM | `http://unitsofmeasure.org` | Result units |

Notes for adopters:

- Using SNOMED CT in a real service requires an appropriate licence (Affiliate licence or national release centre membership). LOINC and ATC have their own terms of use.
- The built-in pre-flight does not check value-set bindings. The HL7 FHIR validator checks bindings when a terminology server is available to it.
- Cross-border display may require designations and mappings from catalogues mandated for EHDS/MyHealth@EU exchange; that requirement is tracked as `TERM-04` (unresolved) in `config/ehds-readiness.yml`.
- To add a terminology service, make it an optional runtime dependency with documented provenance; do not commit release content.
