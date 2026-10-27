#!/usr/bin/env python3
"""Run the Dev Tools console file against Elasticsearch, then index the samples
the way Fleet's Custom Logs integration does.

Usage: ES_URL=http://localhost:9200 tests/check_devtools.py

Fleet writes to logs-<dataset>-<namespace> through its own index template,
which includes the logs-kea.dhcp@custom component template, and the
integration's `pipeline: logs-kea.dhcp` setting sends each document through
the pipeline. This check builds the same arrangement under a scratch name.
"""
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_pipeline import ESError, es, events  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONSOLE = ROOT / "elasticsearch/dev_tools/install-fleet.txt"
STREAM = "logs-kea.dhcp-fleettest"
REQUEST = re.compile(r"^(GET|PUT|POST|DELETE) (\S+)$")


def requests(text):
    """Split a Kibana console file into (method, path, body) tuples."""
    out = []
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        m = REQUEST.match(line)
        if m:
            out.append([m.group(1), "/" + m.group(2).lstrip("/"), ""])
        elif out:
            out[-1][2] += line + "\n"
    return [(m, p, json.loads(b) if b.strip() else None) for m, p, b in out]


def main():
    reqs = requests(CONSOLE.read_text())
    for method, path, body in reqs:
        result = es(method, path, body)
        if path.endswith("/_simulate"):
            src = result["docs"][0]["doc"]["_source"]
            if src["event"].get("action") != "lease-allocated" or src["client"]["ip"] != "192.0.2.10":
                sys.exit(f"FAIL simulate in console file returned {json.dumps(src)}")
        print(f"ok   {method} {path}")

    # Stand-in for Fleet's own index template.
    es("PUT", f"/_index_template/{STREAM}", {
        "index_patterns": [STREAM],
        "data_stream": {},
        "priority": 500,
        "composed_of": ["logs@mappings", "logs@settings", "logs-kea.dhcp@custom", "ecs@mappings"],
        "ignore_missing_component_templates": ["ecs@mappings"],
    })
    messages = [m for s in sorted((ROOT / "samples").glob("*.log")) for m in events(s)]
    bulk = "".join(
        json.dumps({"create": {}}) + "\n"
        + json.dumps({"@timestamp": "2026-01-01T00:00:00Z", "message": m,
                      "data_stream": {"type": "logs", "dataset": "kea.dhcp", "namespace": "fleettest"}}) + "\n"
        for m in messages
    )
    try:
        result = es("POST", f"/{STREAM}/_bulk?refresh=true&pipeline=logs-kea.dhcp", bulk, ndjson=True)
        failed = [i["create"]["error"] for i in result["items"] if "error" in i["create"]]
        es("GET", f"/_cluster/health/{STREAM}?wait_for_status=yellow&timeout=30s")
        failures = es("POST", f"/{STREAM}/_count", {"query": {"term": {"tags": "kea_parse_failure"}}})["count"]
        mapping = es("GET", f"/{STREAM}/_mapping/field/kea.lease.address")
        types = {f["mappings"]["kea.lease.address"]["mapping"]["address"]["type"] for f in mapping.values()}
    finally:
        es("DELETE", f"/_data_stream/{STREAM}")
        es("DELETE", f"/_index_template/{STREAM}")
    if failed or failures or types != {"ip"}:
        sys.exit(f"FAIL Fleet-style indexing: {len(failed)} rejected, {failures} parse failures, "
                 f"kea.lease.address mapped as {types}; first error: {failed[:1]}")
    print(f"ok   indexed {len(messages)} events Fleet-style into {STREAM}")


if __name__ == "__main__":
    try:
        main()
    except ESError as e:
        sys.exit(str(e))
