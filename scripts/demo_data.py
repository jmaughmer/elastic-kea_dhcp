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
            if rnd.random() < 0.5:
                out.append((host, line(t, "INFO", "kea-dhcp4.leases", pid, "DHCP4_DECLINE_LEASE",
                                       f"Received DHCPDECLINE for addr {bad} from client {v4_label(m, tid)}. The lease will be unavailable for 86400 seconds.")))
            else:
                # Found by the ping check hook before the address was offered.
                out.append((host, line(t, "INFO", "kea-dhcp4.leases", pid, "DHCP4_SERVER_INITIATED_DECLINE",
                                       f"{v4_label(m, tid)}: Lease for addr {bad} has been found to be already in use. The lease will be unavailable for 86400 seconds.")))
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
            partner, addr = ("dhcp02", "192.0.2.3") if host == "dhcp01" else ("dhcp01", "192.0.2.2")
            out.append((host, line(t, "WARN", "kea-dhcp4.ha-hooks", pid, "HA_LEASE_UPDATE_COMMUNICATIONS_FAILED",
                                   f"{host}: failed to send lease update {v4_label(m, tid)} to {partner} (https://{addr}:8001/): Connection refused")))
        elif roll < 0.982:
            out.append((host, line(t, "ERROR", "kea-dhcp4.dhcpsrv", pid, "DHCPSRV_MEMFILE_LFC_EXECUTE_FAILED",
                                   "execution of the LFC failed: unable to execute /usr/sbin/kea-lfc")))
    return out + scheduled(rnd, start, now)


HA_URL = {"dhcp01": "https://192.0.2.2:8001/", "dhcp02": "https://192.0.2.3:8001/"}


def transition(ts, host, pid, old, new, partner_state):
    return (host, line(ts, "INFO", "kea-dhcp4.ha-hooks", pid, "HA_STATE_TRANSITION",
                       f"{host}: server transitions from {old} to {new} state, partner state is {partner_state}"))


def ha_start(ts, host, pid, role, partner, partner_state):
    """A server joining its partner: WAITING, SYNCING, READY, HOT-STANDBY."""
    s = dt.timedelta(seconds=1)
    return [
        (host, line(ts, "INFO", "kea-dhcp4.ha-hooks", pid, "HA_SERVICE_STARTED",
                    f"{host}: started high availability service in hot-standby mode as {role} server")),
        (host, line(ts, "INFO", "kea-dhcp4.ha-hooks", pid, "HA_LOCAL_DHCP_DISABLE",
                    f"local DHCP service is disabled while the {host} is in the WAITING state")),
        transition(ts + s, host, pid, "WAITING", "SYNCING", partner_state),
        (host, line(ts + s, "INFO", "kea-dhcp4.ha-hooks", pid, "HA_SYNC_SUCCESSFUL",
                    f"{host}: lease database synchronization with {partner} ({HA_URL[partner]}) completed successfully in 812.4 ms")),
        transition(ts + 2 * s, host, pid, "SYNCING", "READY", partner_state),
        transition(ts + 5 * s, host, pid, "READY", "HOT-STANDBY", "HOT-STANDBY" if partner_state != "WAITING" else "READY"),
        (host, line(ts + 5 * s, "INFO", "kea-dhcp4.ha-hooks", pid, "HA_LOCAL_DHCP_ENABLE",
                    f"local DHCP service is enabled while the {host} is in the HOT-STANDBY state")),
    ]


def reload(ts, host, pid, ok=True):
    """`systemctl reload kea-dhcp4` (SIGHUP), as the Ansible role does on a config change."""
    s = dt.timedelta(milliseconds=40)
    conf = "/etc/kea/kea-dhcp4.conf"
    out = [(host, line(ts, "INFO", "kea-dhcp4.dhcp4", pid, "DHCP4_DYNAMIC_RECONFIGURATION",
                       f"initiate server reconfiguration using file: {conf}, after receiving SIGHUP signal or config-reload command"))]
    if not ok:
        return out + [(host, line(ts + s, "ERROR", "kea-dhcp4.dhcp4", pid, "DHCP4_DYNAMIC_RECONFIGURATION_FAIL",
                                  f"dynamic server reconfiguration failed with file: {conf}"))]
    return out + [
        (host, line(ts + s, "WARN", "kea-dhcp4.dhcpsrv", pid, "DHCPSRV_MT_DISABLED_QUEUE_CONTROL",
                    "disabling dhcp queue control when multi-threading is enabled.")),
        (host, line(ts + s, "WARN", "kea-dhcp4.dhcp4", pid, "DHCP4_RESERVATIONS_LOOKUP_FIRST_ENABLED",
                    "Multi-threading is enabled and host reservations lookup is always performed first.")),
        (host, line(ts + 2 * s, "INFO", "kea-dhcp4.dhcp4", pid, "DHCP4_CONFIG_COMPLETE",
                    "DHCPv4 server has completed configuration: added IPv4 subnets: 3; DDNS: enabled")),
        (host, line(ts + 2 * s, "INFO", "kea-dhcp4.dhcp4", pid, "DHCP4_DYNAMIC_RECONFIGURATION_SUCCESS",
                    f"dynamic server reconfiguration succeeded with file: {conf}")),
    ]


def api_call(ts, host, pid, user, command):
    return [
        (host, line(ts, "INFO", "kea-dhcp4.auth", pid, "HTTP_CLIENT_REQUEST_AUTHORIZED",
                    f"received HTTP request authorized for '{user}'")),
        (host, line(ts, "INFO", "kea-dhcp4.commands", pid, "COMMAND_RECEIVED", f"Received command '{command}'")),
    ]


def scheduled(rnd, start, now):
    """HA, API and configuration events that follow a timetable rather than client traffic."""
    pids = dict(SERVERS)
    out = []
    # Both servers come up together at the start of the window.
    out += ha_start(start, "dhcp01", pids["dhcp01"], "primary", "dhcp02", "WAITING")
    out += ha_start(start, "dhcp02", pids["dhcp02"], "standby", "dhcp01", "WAITING")

    # Heartbeats each minute in both directions (Kea's default is every 10 s).
    t = start
    while t < now:
        for host in ("dhcp01", "dhcp02"):
            out.append((host, line(t, "INFO", "kea-dhcp4.commands", pids[host], "COMMAND_RECEIVED",
                                   "Received command 'ha-heartbeat'")))
        t += dt.timedelta(minutes=1)

    # phpIPAM: the reservation sync every 15 minutes, the DHCP page now and then.
    t = start
    while t < now:
        out += api_call(t, "dhcp01", pids["dhcp01"], "phpipam", "reservation-get-page")
        if rnd.random() < 0.2:
            out += api_call(t + dt.timedelta(seconds=1), "dhcp01", pids["dhcp01"], "phpipam", "reservation-add")
        if rnd.random() < 0.15:
            out += api_call(t + dt.timedelta(minutes=4), "dhcp01", pids["dhcp01"], "phpipam", "config-get")
        t += dt.timedelta(minutes=15)

    # The primary fails 7 hours ago and is back after 20 minutes.
    down = now - dt.timedelta(hours=7)
    for i in range(3):
        out.append(("dhcp02", line(down + dt.timedelta(seconds=10 * i), "WARN", "kea-dhcp4.ha-hooks", pids["dhcp02"],
                                   "HA_HEARTBEAT_COMMUNICATIONS_FAILED",
                                   f"dhcp02: failed to send heartbeat to dhcp01 ({HA_URL['dhcp01']}): Connection refused")))
    lost = down + dt.timedelta(seconds=30)
    out.append(("dhcp02", line(lost, "WARN", "kea-dhcp4.ha-hooks", pids["dhcp02"], "HA_COMMUNICATION_INTERRUPTED",
                               "dhcp02: communication with dhcp01 is interrupted")))
    out.append(transition(lost + dt.timedelta(seconds=60), "dhcp02", pids["dhcp02"], "HOT-STANDBY", "PARTNER-DOWN", "UNDEFINED"))
    back = down + dt.timedelta(minutes=20)
    out += ha_start(back, "dhcp01", pids["dhcp01"], "primary", "dhcp02", "PARTNER-DOWN")
    out.append(transition(back + dt.timedelta(seconds=3), "dhcp02", pids["dhcp02"], "PARTNER-DOWN", "HOT-STANDBY", "READY"))

    # A configuration change rolled out 5 hours ago, one server at a time, and
    # a bad one 3 hours ago that dhcp02 rejected before it was fixed.
    out += reload(now - dt.timedelta(hours=5), "dhcp01", pids["dhcp01"])
    out += reload(now - dt.timedelta(hours=5, minutes=-2), "dhcp02", pids["dhcp02"])
    out += reload(now - dt.timedelta(hours=3), "dhcp02", pids["dhcp02"], ok=False)
    out += reload(now - dt.timedelta(hours=2, minutes=48), "dhcp02", pids["dhcp02"])

    # Someone tries the API with an old password, then without a client certificate.
    burst = now - dt.timedelta(hours=4)
    for i in range(12):
        out.append(("dhcp02", line(burst + dt.timedelta(seconds=20 * i), "INFO", "kea-dhcp4.auth", pids["dhcp02"],
                                   "HTTP_CLIENT_REQUEST_NOT_AUTHORIZED",
                                   "received HTTP request with not matching authentication header")))
    for i in range(4):
        out.append(("dhcp02", line(burst + dt.timedelta(minutes=10, seconds=15 * i), "INFO", "kea-dhcp4.http", pids["dhcp02"],
                                   "HTTP_CONNECTION_HANDSHAKE_FAILED",
                                   "TLS handshake with 198.51.100.7 failed with peer did not return a certificate (SSL routines)")))
    return [r for r in out if r[1][:19] <= now.strftime("%Y-%m-%d %H:%M:%S")]


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
