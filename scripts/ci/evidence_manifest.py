"""Evidence manifest (profile CHK-06): binds conformance evidence to one reproducible release.

Usage: python scripts/ci/evidence_manifest.py <build-dir> <commit> > manifest.json
Lists the release identifier, the rule catalogue and dependency manifest digests, the vector set and its
digests, every Checker report and live/vector result with its digest, the external validator outputs with
the pinned versions, and the preconditions of each kind of test.
"""
import hashlib
import json
import platform
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path

build, commit = Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "unknown"
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
version = next(line.split('"')[1] for line in Path("src/scdpoc/__init__.py").read_text().splitlines()
               if line.startswith("__version__"))
deps = json.loads(Path("conformance/dependencies.json").read_text())
vectors = json.loads(Path("conformance/vectors/manifest.json").read_text())
ev = build / "evidence"


def _v(name: str) -> str:
    try:
        return pkg_version(name)
    except PackageNotFoundError:
        return "not installed"


rules = json.loads(Path("conformance/rules.json").read_text())
manifest = {
    "release": {"implementation": f"scdpoc {version}", "commit": commit, "sourceRevision": commit},
    "specification": {"profileVersion": rules["profileVersion"], "generatedFrom": rules.get("generatedFrom")},
    "environment": {"python": platform.python_version(), "platform": platform.platform(),
                    "packages": {p: _v(p) for p in ("reportlab", "pikepdf", "pillow", "fastapi", "lxml", "pyyaml")}},
    "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
    "profile": vectors["profile"], "profileVersion": vectors["profileVersion"],
    "ruleCatalogueSha256": sha(Path("conformance/rules.json")),
    "dependencyManifestSha256": sha(Path("conformance/dependencies.json")),
    "validators": {d["key"]: d["version"] for d in deps["dependencies"] if d["key"] in ("hl7-validator", "verapdf")},
    "vectors": {"manifestSha256": sha(Path("conformance/vectors/manifest.json")),
                "envelopes": {k: v["envelopeSha256"] for k, v in vectors["vectors"].items()}},
    "reports": {str(p.relative_to(ev)): sha(p) for p in sorted(ev.rglob("*.json")) if p.name != "manifest.json"},
    "validatorOutputs": {str(p.relative_to(ev)): sha(p) for p in sorted(ev.glob("*.outcome.json"))}
    | {str(p.relative_to(ev)): sha(p) for p in sorted(ev.glob("*.verapdf.xml"))},
    "fixtures": {p.name: sha(p) for p in sorted((build / "fixtures").glob("*"))},
    "preconditions": {
        "artefact": "Checker run offline on the vector files; ENV-01 and SRC-01 need the pinned validators",
        "vectors": "conformance/vectors as committed; regenerated vectors must reproduce the same digests",
        "live": "fresh in-process demonstrator per scenario, synthetic data, temporary data directory; "
                "SCDPOC_REQUIRE_HL7_VALIDATOR and SCDPOC_REQUIRE_VERAPDF set in the validators job",
        "claim": "conformance/claims/demonstrator.json",
    },
    "independence": "All evidence is produced by the project's own Checker and scenarios (CHK-05).",
}
print(json.dumps(manifest, indent=2))
