#!/usr/bin/env bash
# Scripted end-to-end walkthrough against a running demonstrator.
# Usage: scripts/demo.sh [base-url] [patient-key]
set -euo pipefail
BASE="${1:-http://localhost:8080}"
KEY="${2:-amara-okafor}"
py() { python3 -c "import json,sys; d=json.load(sys.stdin); $1"; }

echo "1  patients:";  curl -fsS "$BASE/api/demo/patients" | py 'print("\n".join("   "+p["key"]+"  "+p["identifier"] for p in d))'
DRAFT=$(curl -fsS -X POST "$BASE/api/demo/compose/$KEY" | py 'print(d["draftId"]); print("2-4 composed v%s, gate: %s" % (d["version"], d["validation"]["gate"]), file=sys.stderr)')
curl -fsS -X POST "$BASE/api/demo/package/$DRAFT" | py 'print("5-6 envelope sha256 %s; %s" % (d["envelope"]["sha256"], d["gate"]))'
curl -fsS -X POST "$BASE/api/demo/publish/$DRAFT" | py 'i=d["issuance"]; print("7  published envelope %s and IPS %s" % (i["envelope"]["uniqueId"], i["ips"]["uniqueId"]))'
curl -fsS "$BASE/api/demo/discover/$KEY" | py 'print("8  XDS/MHD discovery equivalent:", d["equivalent"])'
curl -fsS "$BASE/api/demo/retrieve/$KEY" | py 'print("8-9 integrity:", "PASS" if d["integrity"]["passed"] else "FAIL", "| IPS via ITI-68:", d["mhd"]["contentType"])'
curl -fsS "$BASE/api/demo/ehds-preview/$KEY" | py 'print("10 EHDS preview (non-normative):", d["summary"])'
curl -fsS -X POST "$BASE/api/demo/tamper/$KEY?mode=embedded" | py 'print("+  tamper detected:", not d["tampered"]["passed"])'
echo "Download: $BASE/api/demo/package/$DRAFT/envelope.pdf"
