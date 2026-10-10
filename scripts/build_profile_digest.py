#!/usr/bin/env python3
"""Build config/ips-profile-digest.json from an HL7 FHIR IPS package tarball.

The HL7 IPS package is licensed CC0-1.0. This script extracts only the
top-level required elements (min >= 1) and canonical URLs of the profiles
the composer uses, so the built-in pre-flight validator can check them
without network access. It is NOT a substitute for the HL7 FHIR validator.

Usage: python scripts/build_profile_digest.py path/to/hl7.fhir.uv.ips-<ver>.tgz
"""
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

PROFILES = ["Bundle", "Composition", "Patient", "Condition", "AllergyIntolerance",
            "MedicationStatement", "Medication", "Immunization", "Organization",
            "Observation-results-laboratory-pathology"]

def main(tgz: str) -> None:
    raw = Path(tgz).read_bytes()
    tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
    pkg = json.load(tf.extractfile("package/package.json"))
    out = {"source": {"package": pkg["name"], "version": pkg["version"], "license": pkg.get("license"),
                      "tarball_sha256": hashlib.sha256(raw).hexdigest()},
           "profiles": {}}
    for p in PROFILES:
        sd = json.load(tf.extractfile(f"package/StructureDefinition-{p}-uv-ips.json"))
        req = []
        for e in sd["snapshot"]["element"]:
            eid = e["id"]
            if eid.count(".") == 1 and ":" not in eid and e.get("min", 0) >= 1:
                req.append({"path": eid.split(".", 1)[1], "min": e["min"], "max": e.get("max")})
        out["profiles"][sd["type"] if p != "Observation-results-laboratory-pathology" else "Observation-lab"] = {
            "url": sd["url"], "required": req}
    target = Path(__file__).resolve().parent.parent / "config" / "ips-profile-digest.json"
    target.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target}")

if __name__ == "__main__":
    main(sys.argv[1])
