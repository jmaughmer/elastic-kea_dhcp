#!/usr/bin/env python3
"""Run the sample Kea logs through the ingest pipeline and compare with golden files.

Usage:
  ES_URL=http://localhost:9200 tests/test_pipeline.py            # verify
  ES_URL=http://localhost:9200 tests/test_pipeline.py --generate # rewrite golden files

Each samples/<name>.log is split into events the way the Filebeat multiline
config does it, simulated against the pipeline, and compared with
tests/expected/<name>.json. Then the events are indexed into a scratch data
stream to catch mapping conflicts.
"""
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
ES_URL = os.environ.get("ES_URL", "http://localhost:9200").rstrip("/")
PIPELINE = ROOT / "elasticsearch/ingest_pipeline/logs-kea.dhcp.json"
EXPECTED = ROOT / "tests/expected"
TEST_STREAM = "logs-kea.dhcp-test"

# Same rule as filebeat/filebeat.yml: a new event starts with a Kea
# timestamp, a syslog timestamp or a bare Kea severity (syslog layout).
EVENT_START = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[ T]|[A-Z][a-z]{2} [ \d]\d |(FATAL|ERROR|WARN|INFO|DEBUG) +\[)"
)


class ESError(Exception):
    pass


def es(method, path, body=None, ndjson=False):
    data = None
    headers = {}
    if body is not None:
        data = body.encode() if ndjson else json.dumps(body).encode()
        headers["Content-Type"] = "application/x-ndjson" if ndjson else "application/json"
    req = urllib.request.Request(ES_URL + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise ESError(f"{method} {path} failed: {e.code} {e.read().decode()}") from None


def events(path):
    out = []
    for line in path.read_text().splitlines():
        if out and not EVENT_START.match(line):
            out[-1] += "\n" + line
        else:
            out.append(line)
    return out


def simulate(pipeline, messages):
    docs = [{"_source": {"message": m}} for m in messages]
    result = es("POST", "/_ingest/pipeline/_simulate", {"pipeline": pipeline, "docs": docs})
    return [d["doc"]["_source"] for d in result["docs"]]


def index_check(messages):
    es("PUT", "/_index_template/logs-kea.dhcp-test", {
        "index_patterns": [TEST_STREAM],
        "data_stream": {},
        "priority": 500,
        "composed_of": ["logs@mappings", "logs@settings", "logs-kea.dhcp@package"],
        "template": {"settings": {"index.default_pipeline": "logs-kea.dhcp", "index.lifecycle.name": None}},
    })
    bulk = "".join(
        json.dumps({"create": {}}) + "\n"
        + json.dumps({"@timestamp": "2026-01-01T00:00:00Z", "message": m}) + "\n"
        for m in messages
    )
    try:
        result = es("POST", f"/{TEST_STREAM}/_bulk?refresh=true", bulk, ndjson=True)
        failed = [i["create"] for i in result["items"] if "error" in i["create"]]
        es("GET", f"/_cluster/health/{TEST_STREAM}?wait_for_status=yellow&timeout=30s")
        parse_failures = es("POST", f"/{TEST_STREAM}/_count",
                            {"query": {"term": {"tags": "kea_parse_failure"}}})["count"]
    finally:
        es("DELETE", f"/_data_stream/{TEST_STREAM}")
        es("DELETE", "/_index_template/logs-kea.dhcp-test")
    return failed, parse_failures


def main():
    generate = "--generate" in sys.argv
    pipeline = json.loads(PIPELINE.read_text())
    EXPECTED.mkdir(exist_ok=True)
    ok = True
    all_messages = []
    for sample in sorted((ROOT / "samples").glob("*.log")):
        messages = events(sample)
        all_messages += messages
        got = simulate(pipeline, messages)
        golden = EXPECTED / (sample.stem + ".json")
        rendered = json.dumps(got, indent=2, sort_keys=True) + "\n"
        if generate:
            golden.write_text(rendered)
            print(f"wrote {golden.relative_to(ROOT)}")
            continue
        if not golden.exists():
            print(f"FAIL {sample.name}: missing {golden.relative_to(ROOT)}")
            ok = False
            continue
        want = json.loads(golden.read_text())
        if got == want:
            print(f"ok   {sample.name} ({len(got)} events)")
            continue
        ok = False
        print(f"FAIL {sample.name}")
        for i, (g, w) in enumerate(zip(got, want)):
            if g != w:
                print(f"  event {i}:\n    got:  {json.dumps(g, sort_keys=True)}\n    want: {json.dumps(w, sort_keys=True)}")
        if len(got) != len(want):
            print(f"  event count {len(got)} != expected {len(want)}")
    if generate:
        return
    failed, parse_failures = index_check(all_messages)
    if failed or parse_failures:
        ok = False
        print(f"FAIL indexing: {len(failed)} rejected, {parse_failures} tagged kea_parse_failure")
        for f in failed[:5]:
            print("  ", json.dumps(f["error"]))
    else:
        print(f"ok   indexed {len(all_messages)} events into {TEST_STREAM}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    try:
        main()
    except ESError as e:
        sys.exit(str(e))
