#!/usr/bin/env python3
"""Generate kibana/kea-dhcp-dashboard.ndjson (data view, saved search, Lens panels, dashboard).

Usage: scripts/build_dashboard.py [--check]

The NDJSON is a Kibana saved-objects export, importable from Stack Management >
Saved Objects or with scripts/install.sh. Edit this file rather than the NDJSON.
--check exits non-zero when the committed NDJSON is out of date (used in CI).
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "kibana/kea-dhcp-dashboard.ndjson"

DATA_VIEW_ID = "logs-kea.dhcp"
DASHBOARD_ID = "kea-dhcp-overview"
SEARCH_ID = "kea-dhcp-errors-warnings"
LAYER = "layer1"

# Oldest migration versions whose document shape matches what this script
# writes. Kibana runs any newer migrations on import, so one export works on
# every release from 8.9 on, including 9.x.
MIGRATION_VERSIONS = {"index-pattern": "8.0.0", "search": "8.0.0", "lens": "8.9.0", "dashboard": "8.9.0"}

LEASE_ACTIONS = 'event.action : ("lease-allocated" or "lease-reused" or "lease-released" or "lease-declined" or "lease-offered" or "allocation-failed")'
PROBLEM_LEVELS = 'log.level : ("error" or "fatal" or "warn")'


def kql(query):
    return {"query": query, "language": "kuery"}


# Lens column builders.

def count(label, query=None):
    col = {
        "label": label,
        "customLabel": True,
        "dataType": "number",
        "operationType": "count",
        "isBucketed": False,
        "scale": "ratio",
        "sourceField": "___records___",
        "params": {"emptyAsNull": False},
    }
    if query:
        col["filter"] = kql(query)
    return col


def unique_count(label, field, query=None):
    col = {
        "label": label,
        "customLabel": True,
        "dataType": "number",
        "operationType": "unique_count",
        "isBucketed": False,
        "scale": "ratio",
        "sourceField": field,
        "params": {"emptyAsNull": False},
    }
    if query:
        col["filter"] = kql(query)
    return col


def last_seen(label="Last seen"):
    return {
        "label": label,
        "customLabel": True,
        "dataType": "date",
        "operationType": "max",
        "isBucketed": False,
        "scale": "ratio",
        "sourceField": "@timestamp",
        "params": {},
    }


def date_histogram():
    return {
        "label": "@timestamp",
        "dataType": "date",
        "operationType": "date_histogram",
        "isBucketed": True,
        "scale": "interval",
        "sourceField": "@timestamp",
        "params": {"interval": "auto", "includeEmptyRows": True, "dropPartials": False},
    }


def terms(label, field, order_by, size=10, data_type="string", other=True):
    return {
        "label": label,
        "customLabel": True,
        "dataType": data_type,
        "operationType": "terms",
        "isBucketed": True,
        "scale": "ordinal",
        "sourceField": field,
        "params": {
            "size": size,
            "orderBy": {"type": "column", "columnId": order_by},
            "orderDirection": "desc",
            "otherBucket": other,
            "missingBucket": False,
            "parentFormat": {"id": "terms"},
            "include": [],
            "exclude": [],
            "includeIsRegex": False,
            "excludeIsRegex": False,
        },
    }


# Lens visualization builders. Each returns (visualizationType, columns, visualization).

def metric(column):
    vis = {"layerId": LAYER, "layerType": "data", "metricAccessor": "m"}
    return "lnsMetric", {"m": column}, vis


def xy(series_type, metric_col, split_col=None, legend="right"):
    columns = {"x": date_histogram()}
    layer = {
        "layerId": LAYER,
        "layerType": "data",
        "seriesType": series_type,
        "xAccessor": "x",
        "accessors": ["y"],
        "position": "top",
        "showGridlines": False,
    }
    if split_col:
        columns["s"] = split_col
        layer["splitAccessor"] = "s"
    columns["y"] = metric_col
    vis = {
        "legend": {"isVisible": True, "position": legend},
        "valueLabels": "hide",
        "fittingFunction": "Zero",
        "preferredSeriesType": series_type,
        "layers": [layer],
        "axisTitlesVisibilitySettings": {"x": False, "yLeft": False, "yRight": True},
    }
    return "lnsXY", columns, vis


def donut(group_col, metric_col):
    vis = {
        "shape": "donut",
        "layers": [{
            "layerId": LAYER,
            "layerType": "data",
            "primaryGroups": ["g"],
            "metrics": ["m"],
            "numberDisplay": "percent",
            "categoryDisplay": "default",
            "legendDisplay": "default",
            "nestedLegend": False,
        }],
    }
    return "lnsPie", {"g": group_col, "m": metric_col}, vis


def table(columns):
    vis = {
        "layerId": LAYER,
        "layerType": "data",
        "columns": [{"columnId": cid, "isTransposed": False} for cid in columns],
    }
    return "lnsDatatable", columns, vis


def lens(obj_id, title, description, spec, query=""):
    vis_type, columns, vis = spec
    return {
        "type": "lens",
        "id": obj_id,
        "attributes": {
            "title": title,
            "description": description,
            "visualizationType": vis_type,
            "state": {
                "datasourceStates": {
                    "formBased": {
                        "layers": {
                            LAYER: {
                                "columns": columns,
                                "columnOrder": list(columns),
                                "incompleteColumns": {},
                                "sampling": 1,
                            }
                        }
                    }
                },
                "visualization": vis,
                "query": kql(query),
                "filters": [],
                "internalReferences": [],
                "adHocDataViews": {},
            },
        },
        "references": [{
            "type": "index-pattern",
            "id": DATA_VIEW_ID,
            "name": f"indexpattern-datasource-layer-{LAYER}",
        }],
    }


# (id, title, description, spec, query, grid x, y, w, h)
PANELS = [
    ("kea-dhcp-kpi-leases", "Leases allocated",
     "DHCP4_LEASE_ALLOC / DHCP6_LEASE_ALLOC / DHCP6_PD_LEASE_ALLOC events, including renewals.",
     metric(count("Leases allocated", 'event.action : "lease-allocated"')), "", 0, 0, 8, 6),
    ("kea-dhcp-kpi-clients", "Active clients",
     "Distinct client MAC addresses that were allocated or reused a lease.",
     metric(unique_count("Active clients", "client.mac", 'event.action : ("lease-allocated" or "lease-reused")')), "", 8, 0, 8, 6),
    ("kea-dhcp-kpi-releases", "Leases released",
     "Clients that released their lease (DHCPRELEASE / RELEASE).",
     metric(count("Leases released", 'event.action : "lease-released"')), "", 16, 0, 8, 6),
    ("kea-dhcp-kpi-declines", "Leases declined",
     "DHCPDECLINE / DECLINE: a client found the offered address already in use. Usually an address conflict.",
     metric(count("Leases declined", 'event.action : "lease-declined"')), "", 24, 0, 8, 6),
    ("kea-dhcp-kpi-alloc-fail", "Allocation failures",
     "ALLOC_ENGINE_*_ALLOC_FAIL*: Kea had no address to give. Often a full pool or an unmatched client class.",
     metric(count("Allocation failures", 'event.action : "allocation-failed"')), "", 32, 0, 8, 6),
    ("kea-dhcp-kpi-problems", "Errors and warnings",
     "Log entries at WARN, ERROR or FATAL severity.",
     metric(count("Errors and warnings", PROBLEM_LEVELS)), "", 40, 0, 8, 6),

    ("kea-dhcp-lease-activity", "Lease activity over time",
     "Lease lifecycle events by action.",
     xy("bar_stacked", count("Events"), terms("Action", "event.action", "y", size=10)),
     LEASE_ACTIONS, 0, 6, 32, 12),
    ("kea-dhcp-severity", "Log severity",
     "Share of log entries by Kea severity.",
     donut(terms("Severity", "log.level", "m", size=5, other=False), count("Events")),
     "", 32, 6, 16, 12),

    ("kea-dhcp-alloc-fail-time", "Allocation failures over time",
     "Allocation failures by Kea service (kea-dhcp4 / kea-dhcp6).",
     xy("bar_stacked", count("Allocation failures"), terms("Service", "service.name", "y", size=5)),
     'event.action : "allocation-failed"', 0, 18, 24, 12),
    ("kea-dhcp-volume-by-server", "Log volume by server",
     "Events per DHCP server. In an HA pair both servers should track each other.",
     xy("line", count("Events"), terms("Server", "host.name", "y", size=10)),
     "", 24, 18, 24, 12),

    ("kea-dhcp-top-clients", "Top clients",
     "Clients with the most lease allocations and renewals.",
     table({
         "c": terms("Client MAC", "client.mac", "n", size=20, other=False),
         "n": count("Leases", 'event.action : ("lease-allocated" or "lease-reused")'),
         "a": unique_count("Addresses", "client.ip"),
         "t": last_seen(),
     }),
     'event.action : ("lease-allocated" or "lease-reused" or "lease-released")', 0, 30, 24, 14),
    ("kea-dhcp-top-codes", "Top Kea message IDs",
     "Most frequent Kea log message IDs. See the Kea messages manual for each ID.",
     table({
         "c": terms("Message ID", "event.code", "n", size=20, other=False),
         "n": count("Events"),
         "t": last_seen(),
     }),
     "", 24, 30, 24, 14),

    ("kea-dhcp-alloc-fail-clients", "Clients failing allocation",
     "Clients Kea could not give an address to.",
     table({
         "c": terms("Client MAC", "client.mac", "n", size=20, other=False),
         "n": count("Failures"),
         "t": last_seen(),
     }),
     'event.action : "allocation-failed"', 0, 44, 24, 12),
    ("kea-dhcp-declined-addresses", "Declined addresses",
     "Addresses clients declined because another device answered on them. Kea holds each one for decline-probation-period.",
     table({
         "c": terms("Address", "kea.lease.address", "n", size=20, data_type="ip", other=False),
         "n": count("Declines"),
         "t": last_seen(),
     }),
     'event.action : "lease-declined"', 24, 44, 24, 12),
]


def data_view():
    return {
        "type": "index-pattern",
        "id": DATA_VIEW_ID,
        "attributes": {
            "title": "logs-kea.dhcp-*",
            "name": "Kea DHCP logs",
            "timeFieldName": "@timestamp",
        },
        "references": [],
    }


def saved_search():
    source = {
        "query": kql(PROBLEM_LEVELS),
        "filter": [],
        "indexRefName": "kibanaSavedObjectMeta.searchSourceJSON.index",
    }
    return {
        "type": "search",
        "id": SEARCH_ID,
        "attributes": {
            "title": "Kea DHCP errors and warnings",
            "description": "Kea log entries at WARN, ERROR or FATAL severity.",
            "columns": ["host.name", "log.level", "log.logger", "event.code", "message"],
            "sort": [["@timestamp", "desc"]],
            "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps(source)},
        },
        "references": [{
            "name": "kibanaSavedObjectMeta.searchSourceJSON.index",
            "type": "index-pattern",
            "id": DATA_VIEW_ID,
        }],
    }


def dashboard():
    panels, refs = [], []
    layout = [(p[0], "lens", p[5], p[6], p[7], p[8]) for p in PANELS]
    layout.append((SEARCH_ID, "search", 0, 56, 48, 15))
    for i, (obj_id, obj_type, x, y, w, h) in enumerate(layout, 1):
        idx = f"p{i}"
        panels.append({
            "type": obj_type,
            "panelIndex": idx,
            "gridData": {"x": x, "y": y, "w": w, "h": h, "i": idx},
            # Metric tiles already show their title inside the tile.
            "embeddableConfig": {"enhancements": {}, **({"hidePanelTitles": True} if obj_id.startswith("kea-dhcp-kpi-") else {})},
            "panelRefName": f"panel_{idx}",
        })
        refs.append({"name": f"{idx}:panel_{idx}", "type": obj_type, "id": obj_id})
    return {
        "type": "dashboard",
        "id": DASHBOARD_ID,
        "attributes": {
            "title": "[Kea DHCP] Overview",
            "description": "Lease activity, allocation failures, address conflicts and server health from ISC Kea DHCP logs.",
            "panelsJSON": json.dumps(panels),
            "optionsJSON": json.dumps({"useMargins": True, "syncColors": False, "syncCursor": True, "syncTooltips": False, "hidePanelTitles": False}),
            "timeRestore": True,
            "timeFrom": "now-24h",
            "timeTo": "now",
            "refreshInterval": {"pause": False, "value": 60000},
            "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps({"query": kql(""), "filter": []})},
        },
        "references": refs,
    }


def main():
    objects = [data_view(), saved_search()]
    objects += [lens(p[0], p[1], p[2], p[3], p[4]) for p in PANELS]
    objects.append(dashboard())
    for o in objects:
        o["coreMigrationVersion"] = "8.8.0"
        o["typeMigrationVersion"] = MIGRATION_VERSIONS[o["type"]]
    rendered = "".join(json.dumps(o, sort_keys=True) + "\n" for o in objects)
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != rendered:
            sys.exit(f"{OUT.relative_to(ROOT)} is stale; run scripts/build_dashboard.py")
        return
    OUT.write_text(rendered)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(objects)} objects)")


if __name__ == "__main__":
    main()
