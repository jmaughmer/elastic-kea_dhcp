#!/usr/bin/env python3
"""Check that every saved object in the dashboard NDJSON was imported into Kibana.

Usage: KIBANA_URL=http://localhost:5601 tests/check_kibana.py
"""
import json
import os
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
KIBANA_URL = os.environ.get("KIBANA_URL", "http://localhost:5601").rstrip("/")


def main():
    lines = (ROOT / "kibana/kea-dhcp-dashboard.ndjson").read_text().splitlines()
    wanted = [{"type": o["type"], "id": o["id"]} for o in map(json.loads, lines)]
    req = urllib.request.Request(
        KIBANA_URL + "/api/saved_objects/_bulk_get",
        data=json.dumps(wanted).encode(),
        method="POST",
        headers={"kbn-xsrf": "true", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        objects = json.loads(resp.read())["saved_objects"]
    bad = [f"{o['type']}/{o['id']}: {o['error']}" for o in objects if "error" in o]
    print(f"{len(objects) - len(bad)} of {len(wanted)} saved objects present")
    if bad:
        sys.exit("\n".join(bad))


if __name__ == "__main__":
    main()
