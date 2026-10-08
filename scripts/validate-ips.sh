#!/usr/bin/env bash
# Validate IPS JSON files with the official HL7 FHIR validator against the
# pinned IPS package (config/ips-package.yml). Requires Java 17+.
# Usage: scripts/validate-ips.sh file.json [file.json ...]
set -euo pipefail
cd "$(dirname "$0")/.."
IG_VERSION=$(python -c "import yaml;print(yaml.safe_load(open('config/ips-package.yml'))['ips']['version'])")
JAR="${SCDPOC_HL7_VALIDATOR_JAR:-.cache/validator_cli.jar}"
if [ ! -f "$JAR" ]; then
  mkdir -p "$(dirname "$JAR")"
  echo "Downloading HL7 FHIR validator to $JAR"
  curl -fsSL -o "$JAR" https://github.com/hapifhir/org.hl7.fhir.core/releases/latest/download/validator_cli.jar
fi
mkdir -p build/evidence
status=0
for f in "$@"; do
  out="build/evidence/$(basename "$f" .json).outcome.json"
  echo "== $f (hl7.fhir.uv.ips#$IG_VERSION)"
  java -jar "$JAR" "$f" -version 4.0.1 -ig "hl7.fhir.uv.ips#$IG_VERSION" \
    -profile http://hl7.org/fhir/uv/ips/StructureDefinition/Bundle-uv-ips -output "$out" || status=1
done
exit $status
