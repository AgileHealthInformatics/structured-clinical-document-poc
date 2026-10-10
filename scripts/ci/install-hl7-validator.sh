#!/usr/bin/env bash
# Install the HL7 FHIR validator at the version fixed by conformance/dependencies.json and verify it.
# Usage: scripts/ci/install-hl7-validator.sh <jar-path>
set -euo pipefail
JAR="${1:-.cache/validator_cli.jar}"
VERSION=$(python scripts/ci/pinned.py hl7-validator version)
URL=$(python scripts/ci/pinned.py hl7-validator identifier)
mkdir -p "$(dirname "$JAR")"
curl -fsSL -o "$JAR" "$URL"
# The validator prints its version in its banner; refuse anything else.
java -jar "$JAR" -help 2>&1 | head -5 | tee /dev/stderr | grep -q "$VERSION" || {
  echo "HL7 FHIR validator at $URL does not report version $VERSION" >&2; exit 1; }
echo "HL7 FHIR validator $VERSION installed at $JAR"
