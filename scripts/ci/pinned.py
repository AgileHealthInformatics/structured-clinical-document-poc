"""Print a value from the profile's dependency manifest (conformance/dependencies.json).

Usage: python scripts/ci/pinned.py <key> [version|identifier]
CI reads validator versions and download locations from here, so it never selects a version at run time
(profile CON-02, review A-07).
"""
import json
import sys
from pathlib import Path

deps = {d["key"]: d for d in json.loads(Path("conformance/dependencies.json").read_text())["dependencies"]}
print(deps[sys.argv[1]][sys.argv[2] if len(sys.argv) > 2 else "version"])
