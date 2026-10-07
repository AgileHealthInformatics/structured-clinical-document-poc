"""Summarise CI evidence (validator outcomes, digests, pinned versions) as Markdown."""
import hashlib
import json
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1] if len(sys.argv) > 1 else "build")
pins = yaml.safe_load(Path("config/ips-package.yml").read_text())
print("# Evidence report\n")
print(f"- IPS package: `{pins['ips']['package']}#{pins['ips']['version']}` (FHIR {pins['fhir']['version']})")
print(f"- PDF/A: part {pins['pdfa']['part']}, conformance {pins['pdfa']['conformance']}\n")
print("| Artefact | SHA-256 | Validator outcome |\n|---|---|---|")
ev = root / "evidence"
for f in sorted((root / "fixtures").glob("*")):
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    outcome = "-"
    if f.suffix == ".json":
        o = ev / (f.name.removesuffix(".json") + ".outcome.json")
        if o.exists():
            issues = json.loads(o.read_text()).get("issue", [])
            errs = sum(1 for i in issues if i.get("severity") in ("error", "fatal"))
            outcome = f"HL7 validator: {errs} error(s), {len(issues) - errs} other"
    elif f.suffix == ".pdf":
        o = ev / (f.stem + ".verapdf.xml")
        if o.exists():
            outcome = "veraPDF: " + ("compliant" if 'isCompliant="true"' in o.read_text() else "NOT compliant")
    print(f"| `{f.name}` | `{digest}` | {outcome} |")
