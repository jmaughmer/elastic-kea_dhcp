# Manual install: Kibana Dev Tools and Fleet Custom Logs

This guide installs everything from the Kibana UI, with no scripts. Elastic Agent collects the logs through Fleet's **Custom Logs** integration.

You will:

1. Create the ingest pipeline and field mappings in **Dev Tools**.
2. Add the **Custom Logs** integration to the agent policy of your Kea servers.
3. Import the dashboard in **Saved Objects**.

Requirements: Elasticsearch and Kibana 8.12 or newer, Fleet set up, and Elastic Agent enrolled on each Kea server. Kea must use its default log layout, file output in `/var/log/kea/` (see [Log format](../README.md#log-format)).

## 1. Pipeline and mappings (Dev Tools)

1. Open [`elasticsearch/dev_tools/install-fleet.txt`](../elasticsearch/dev_tools/install-fleet.txt) and copy the whole file.
2. In Kibana, go to **Management > Dev Tools > Console**.
3. Clear the editor, paste, select everything (Ctrl+A, or Cmd+A on macOS) and press **Ctrl+Enter** (Cmd+Enter) to send all requests.

The file sends three requests:

| Request | What it does | Expected response |
| --- | --- | --- |
| `PUT _ingest/pipeline/logs-kea.dhcp` | Creates the pipeline that parses Kea log lines | `"acknowledged": true` |
| `PUT _component_template/logs-kea.dhcp@custom` | Adds the field mappings to the template Fleet creates for the integration | `"acknowledged": true` |
| `POST _ingest/pipeline/logs-kea.dhcp/_simulate` | Parses one sample line as a check | A document with `"action": "lease-allocated"` and `"ip": "192.0.2.10"` |

`logs-kea.dhcp@custom` is the hook Fleet leaves for your own mappings. If you already keep something in a component template with that name, merge it with the mappings from this file first, because the `PUT` replaces it.

Don't also install this repository's `logs-kea.dhcp` index template (`scripts/install.sh` without `--fleet`). With Fleet, the integration owns that template. If you installed it earlier, remove it before you add the integration:

```
DELETE _index_template/logs-kea.dhcp
```

## 2. Custom Logs integration (Fleet)

1. Go to **Management > Fleet > Agent policies** and open the policy your Kea servers are enrolled in.
2. Click **Add integration** and choose **Custom Logs (Filestream)**. The older **Custom Logs** integration also works; its settings are at the end of this section.
3. Fill in the settings below and save. The agents pick up the change within a minute or so.

**Log file paths.** Add one path per line. The `*` after `.log` also reads Kea's own rotated files (`.log.1`, `.log.2`, ...), so nothing is lost when Kea rotates a file in the middle of a read.

```
/var/log/kea/kea-dhcp4.log*
/var/log/kea/kea-dhcp6.log*
/var/log/kea/kea-dhcp-ddns.log*
/var/log/kea/kea-ctrl-agent.log*
```

Drop the services you don't run.

**Dataset name:** `kea.dhcp`

This name must be exact. It makes the integration write to the `logs-kea.dhcp-<namespace>` data stream, which is what the mappings, the data view and the dashboard expect.

**Namespace:** leave it as `default`, or use your own. The dashboard reads every namespace.

**Ingest pipeline.** Without it the logs arrive unparsed. If your version of the integration has an **Ingest pipeline** field under *Advanced options*, enter `logs-kea.dhcp` there. Otherwise add this line to the **Custom configurations** box:

```yaml
pipeline: logs-kea.dhcp
```

**Parsers.** In debug mode, Kea writes packet dumps over several lines. Every Kea event starts with its timestamp, so this rule joins the extra lines onto the event they belong to:

```yaml
- multiline:
    type: pattern
    pattern: '^\d{4}-\d{2}-\d{2} '
    negate: true
    match: after
```

If your version has no separate **Parsers** box, put it in **Custom configurations** under a `parsers:` key instead.

**Processors.** Kea writes local time with no time zone. `add_locale` records the server's time zone so the pipeline converts the timestamps correctly. Without it, the pipeline assumes UTC.

```yaml
- add_locale: ~
```

Again, if your version has no **Processors** box, put it in **Custom configurations** under a `processors:` key.

### Older "Custom Logs" integration

Use the same paths, dataset name and namespace. The pipeline, multiline and processor settings all go in **Custom configurations**:

```yaml
pipeline: logs-kea.dhcp
multiline.type: pattern
multiline.pattern: '^\d{4}-\d{2}-\d{2} '
multiline.negate: true
multiline.match: after
processors:
  - add_locale: ~
```

### File permissions

Elastic Agent runs as root by default and can read `/var/log/kea/`. If you run the agent unprivileged, give its user read access to that directory.

## 3. Dashboard (Saved Objects)

1. Download [`kibana/kea-dhcp-dashboard.ndjson`](../kibana/kea-dhcp-dashboard.ndjson).
2. Go to **Management > Stack Management > Saved Objects**. Switch to the right space first if you use spaces.
3. Click **Import**, pick the file and keep **Automatically overwrite conflicts** selected. Then click **Import**.

This adds:
- the **Kea DHCP logs** data view (`logs-kea.dhcp-*`)
- the **Kea DHCP errors and warnings** saved search
- 14 Lens visualizations
- the **[Kea DHCP] Overview** dashboard

## 4. Check it works

1. In **Discover**, pick the **Kea DHCP logs** data view. New events should show up within a minute of Kea logging something.
2. Search `tags : "kea_parse_failure"`. You should get no results. Any results are lines the pipeline couldn't parse. Their `event.original` and `error.message` show why.
3. Pick a lease event. It should have `event.action`, `client.mac` and `kea.lease.address` filled in.
4. Open **Dashboards > [Kea DHCP] Overview**.

## Troubleshooting

**Fields like `kea.lease.address` show as text, or the dashboard panels are empty even though Discover has data.**
The data stream was created before the mappings existed. Start a new backing index so the mappings apply:

```
POST logs-kea.dhcp-default/_rollover
```

Use your namespace in place of `default`. Older documents keep their old mappings.

**Documents have `message` but no `event.code` or `log.level`.**
The integration isn't sending events through the pipeline. Check the `pipeline: logs-kea.dhcp` setting from step 2.

**Times are off by a few hours.**
`add_locale` is missing from the integration's processors, so the pipeline treated Kea's local time as UTC.

**Packet dumps show up as many one-line events.**
The multiline parser is missing from the integration.

## Upgrading

When this repository changes the pipeline or mappings, paste the updated `install-fleet.txt` into Dev Tools again and send it. To pick up mapping changes, roll the data stream over as shown above. Re-import the dashboard NDJSON the same way, with overwrite on.
