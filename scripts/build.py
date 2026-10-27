#!/usr/bin/env python3
"""Generate the JSON copy of the ingest pipeline from its YAML source.

Usage: scripts/build.py [--check]

--check exits non-zero when the committed JSON is out of date (used in CI).
"""
import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "elasticsearch/ingest_pipeline/logs-kea.dhcp.yml"
DST = SRC.with_suffix(".json")


def main():
    rendered = json.dumps(yaml.safe_load(SRC.read_text()), indent=2) + "\n"
    if "--check" in sys.argv:
        if not DST.exists() or DST.read_text() != rendered:
            sys.exit(f"{DST.relative_to(ROOT)} is stale; run scripts/build.py")
        return
    DST.write_text(rendered)
    print(f"wrote {DST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
