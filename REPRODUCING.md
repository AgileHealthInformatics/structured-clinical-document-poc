# Reproducing a release and its evidence

Everything in a release can be rebuilt from the source tree and compared byte for byte, except the outputs of the
external validators, which are recorded with their pinned versions.

## Environment

- Python 3.12 or 3.13, `pip install -r requirements.lock -e ".[dev]"` (pinned dependency versions)
- Library versions that affect PDF bytes are recorded in `fixtures/expected/digests.json` (`libraries`)
- External validators, pinned in `conformance/dependencies.json`: HL7 FHIR validator 7.0.1 and veraPDF 1.30.3
  (`scripts/ci/install-hl7-validator.sh`, `scripts/ci/install-verapdf.sh`)

## Rebuild and compare

```bash
scdpoc build-fixtures --out build/fixtures       # IPS digests must equal fixtures/expected/digests.json
scdpoc build-vectors --out build/vectors         # envelope digests must equal conformance/vectors/manifest.json
diff -r build/vectors conformance/vectors
pytest                                          # includes the digest and vector reproducibility tests
```

IPS digests are normative regression values. PDF digests depend on ReportLab, pikepdf, qpdf and LittleCMS versions
and are informative; the vector envelopes are reproducible with the library versions recorded in the digests file.

## Evidence

```bash
scdpoc check-vectors --out build/evidence/vectors.json --reports build/evidence/checker
scdpoc live-check --out build/evidence/live.json
scdpoc check conformance/vectors/V-01/envelope.pdf --projection conformance/vectors/V-01/projection.json \
  --record conformance/vectors/V-01/issuance-record.json --claim conformance/claims/demonstrator.json \
  --evidence build/evidence/live.json --evidence build/evidence/vectors.json
python scripts/ci/evidence_manifest.py build "$(git rev-parse HEAD)" > build/evidence/manifest.json
```

The evidence manifest binds every report to the source revision, the specification version, the rule-catalogue and
dependency-manifest digests, the vector digests, the validator versions and the build environment. CI produces the
same bundle for every commit (`.github/workflows/ci.yml`, artefact `evidence-<commit>`).

## The rule catalogue

`conformance/rules.json` and `conformance/dependencies.json` are copied from a release of the profile repository,
whose own build verifies them and checks that a second build is byte-identical (`build.py --check-reproducible`).
Their SHA-256 values are in the profile's `release-manifest.json`.

## What is not reproduced here

Independent validator reports for this release (profile issue IR-01) and any assessment by a party independent of
this project.
