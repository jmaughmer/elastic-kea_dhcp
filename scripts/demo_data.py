#!/usr/bin/env python3
"""Load a day of synthetic Kea DHCP logs, for previewing the dashboard.

Usage: ES_URL=http://localhost:9200 scripts/demo_data.py [--hours 24] [--namespace demo]

Raw Kea log lines are generated for two HA servers and sent through the
logs-kea.dhcp pipeline into logs-kea.dhcp-<namespace>. Run scripts/install.sh
first. Delete the data afterwards with: DELETE _data_stream/logs-kea.dhcp-demo
"""
import argparse
import datetime as dt
import json
import os
import random
import urllib.request

ES_URL = os.environ.get("ES_URL", "http://localhost:9200").rstrip("/")
SERVERS = [("dhcp01", 1873), ("dhcp02", 1911)]
THREAD = 140226107447040


def mac(i):
    return "00:16:3e:%02x:%02x:%02x" % ((i >> 16) & 0xFF, (i >> 8) & 0xFF, i & 0xFF)


def v4_label(m, tid):
    return f"[hwtype=1 {m}], cid=[01:{m}], tid={tid:#x}"


def v6_label(m, tid):
    return f"duid=[00:03:00:01:{m}], [hwtype=1 {m}], tid={tid:#x}"


def line(ts, level, logger, pid, code, text):
    stamp = ts.strftime("%Y-%m-%d %H:%M:%S.") + f"{ts.microsecond // 1000:03d}"
    return f"{stamp} {level:<5} [{logger}/{pid}.{THREAD}] {code} {text}"


def generate(hours):
    rnd = random.Random(42)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    start = now - dt.timedelta(hours=hours)
    clients = [(mac(i), f"10.20.{i // 250}.{i % 250 + 2}") for i in range(1, 400)]
    out = []
    t = start
    while t < now:
        host, pid = rnd.choice(SERVERS)
        # Busier during the working day.
        busy = 9 <= t.hour < 18
        t += dt.timedelta(seconds=rnd.expovariate(1 / (6 if busy else 25)))
        m, ip = rnd.choice(clients)
        tid = rnd.getrandbits(32)
        roll = rnd.random()
        if roll < 0.55:
            out.append((host, line(t, "INFO", "kea-dhcp4.leases", pid, "DHCP4_LEASE_ALLOC",
                                   f"{v4_label(m, tid)}: lease {ip} has been allocated for 4000 seconds")))
        elif roll < 0.70:
            out.append((host, line(t, "INFO", "kea-dhcp4.leases", pid, "DHCP4_LEASE_OFFER",
                                   f"{v4_label(m, tid)}: lease {ip} will be offered")))
        elif roll < 0.78:
            out.append((host, line(t, "INFO", "kea-dhcp4.leases", pid, "DHCP4_LEASE_REUSE",
                                   f"{v4_label(m, tid)}: lease {ip} has been reused for 3412 seconds")))
        elif roll < 0.86:
            out.append((host, line(t, "INFO", "kea-dhcp4.leases", pid, "DHCP4_RELEASE",
                                   f"{v4_label(m, tid)}: address {ip} was released properly.")))
        elif roll < 0.93:
            v6 = "2001:db8:20::%x" % (int(ip.split(".")[-1]) + 0x100)
            out.append((host, line(t, "INFO", "kea-dhcp6.leases", pid + 7, "DHCP6_LEASE_ALLOC",
                                   f"{v6_label(m, tid)}: lease for address {v6} and iaid=1 has been allocated for 7200 seconds")))
        elif roll < 0.95:
            bad = f"10.20.1.{rnd.choice([17, 17, 17, 88, 140])}"
            out.append((host, line(t, "INFO", "kea-dhcp4.leases", pid, "DHCP4_DECLINE_LEASE",
                                   f"Received DHCPDECLINE for addr {bad} from client {v4_label(m, tid)}. The lease will be unavailable for 86400 seconds.")))
        elif roll < 0.975:
            # A guest VLAN pool that fills up in the afternoon.
            if 13 <= t.hour < 17:
                gm = mac(5000 + rnd.randrange(40))
                out.append((host, line(t, "WARN", "kea-dhcp4.alloc-engine", pid, "ALLOC_ENGINE_V4_ALLOC_FAIL_SUBNET",
                                       f"{v4_label(gm, tid)}: failed to allocate an IPv4 address in the subnet 10.30.0.0/26, subnet-id 30, shared network (none)")))
                out.append((host, line(t, "WARN", "kea-dhcp4.alloc-engine", pid, "ALLOC_ENGINE_V4_ALLOC_FAIL",
                                       f"{v4_label(gm, tid)}: failed to allocate an IPv4 address after 62 attempt(s)")))
        elif roll < 0.98:
            out.append((host, line(t, "INFO", "kea-dhcp4.leases", pid, "DHCP4_NO_LEASE_INIT_REBOOT",
                                   f"{v4_label(m, tid)}: no lease for address 192.168.1.{rnd.randrange(2, 250)} requested by INIT-REBOOT client")))
        elif roll < 0.981:
            out.append((host, line(t, "ERROR", "kea-dhcp4.ha-hooks", pid, "HA_LEASE_UPDATE_COMMUNICATIONS_FAILED",
                                   f"{host}: failed to communicate with partner server: Connection refused")))
        elif roll < 0.982:
            out.append((host, line(t, "ERROR", "kea-dhcp4.dhcpsrv", pid, "DHCPSRV_MEMFILE_LFC_EXECUTE_FAILED",
                                   "execution of the LFC failed: unable to execute /usr/sbin/kea-lfc")))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--namespace", default="demo")
    args = ap.parse_args()
    stream = f"logs-kea.dhcp-{args.namespace}"
    rows = generate(args.hours)
    for i in range(0, len(rows), 2000):
        bulk = "".join(
            json.dumps({"create": {}}) + "\n"
            + json.dumps({"message": msg, "host": {"name": host}, "event": {"timezone": "UTC"}}) + "\n"
            for host, msg in rows[i:i + 2000]
        )
        req = urllib.request.Request(f"{ES_URL}/{stream}/_bulk", data=bulk.encode(), method="POST",
                                     headers={"Content-Type": "application/x-ndjson"})
        with urllib.request.urlopen(req) as resp:
            if json.loads(resp.read()).get("errors"):
                raise SystemExit("bulk request had errors")
    print(f"indexed {len(rows)} events into {stream}")


if __name__ == "__main__":
    main()
