#!/usr/bin/env bash
# Validate PDF/A-3b envelopes with veraPDF. Uses $SCDPOC_VERAPDF_CLI (or
# 'verapdf' on PATH). Writes machine-readable reports to build/evidence/.
# Usage: scripts/validate-pdfa.sh file.pdf [file.pdf ...]
set -euo pipefail
cd "$(dirname "$0")/.."
VERAPDF="${SCDPOC_VERAPDF_CLI:-verapdf}"
command -v "$VERAPDF" >/dev/null || { echo "veraPDF CLI not found; set SCDPOC_VERAPDF_CLI" >&2; exit 2; }
mkdir -p build/evidence
status=0
for f in "$@"; do
  out="build/evidence/$(basename "$f" .pdf).verapdf.xml"
  "$VERAPDF" --flavour 3b --format xml "$f" > "$out" || true
  if grep -q 'isCompliant="true"' "$out"; then echo "PASS $f"; else echo "FAIL $f (see $out)"; status=1; fi
done
exit $status
