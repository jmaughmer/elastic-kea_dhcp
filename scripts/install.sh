#!/usr/bin/env bash
# Install the Kea DHCP ingest pipeline, index templates and Kibana dashboard.
#
# Environment:
#   ES_URL        Elasticsearch URL (default http://localhost:9200)
#   KIBANA_URL    Kibana URL (default http://localhost:5601); set to "" to skip Kibana
#   KIBANA_SPACE  Kibana space id (default: the default space)
#   ES_API_KEY    Encoded API key, or
#   ES_USER / ES_PASS  basic auth credentials (used for both ES and Kibana)
#
# Options:
#   --fleet   For Elastic Agent: install the field mappings as the
#             logs-kea.dhcp@custom component template, which Fleet's own
#             logs-kea.dhcp template composes, and skip this repo's index
#             template so it does not compete with Fleet's.
set -euo pipefail

cd "$(dirname "$0")/.."

ES_URL="${ES_URL:-http://localhost:9200}"
KIBANA_URL="${KIBANA_URL-http://localhost:5601}"
FLEET=false
[[ "${1:-}" == "--fleet" ]] && FLEET=true

auth=()
if [[ -n "${ES_API_KEY:-}" ]]; then
  auth=(-H "Authorization: ApiKey ${ES_API_KEY}")
elif [[ -n "${ES_USER:-}" ]]; then
  auth=(-u "${ES_USER}:${ES_PASS:-}")
fi

call() {
  local method=$1 url=$2 file=$3
  shift 3
  local out
  out=$(curl -sS --fail-with-body "${auth[@]}" -X "$method" "$url" "$@" --data-binary "@$file") || {
    echo "FAILED: $method $url" >&2
    echo "$out" >&2
    exit 1
  }
  echo "ok: $method $url"
}

json=(-H 'Content-Type: application/json')

call PUT "$ES_URL/_ingest/pipeline/logs-kea.dhcp" \
  elasticsearch/ingest_pipeline/logs-kea.dhcp.json "${json[@]}"

if $FLEET; then
  call PUT "$ES_URL/_component_template/logs-kea.dhcp@custom" \
    elasticsearch/index_template/logs-kea.dhcp@package.json "${json[@]}"
else
  call PUT "$ES_URL/_component_template/logs-kea.dhcp@package" \
    elasticsearch/index_template/logs-kea.dhcp@package.json "${json[@]}"
  call PUT "$ES_URL/_index_template/logs-kea.dhcp" \
    elasticsearch/index_template/logs-kea.dhcp.json "${json[@]}"
fi

if [[ -n "$KIBANA_URL" ]]; then
  space_path=""
  [[ -n "${KIBANA_SPACE:-}" ]] && space_path="/s/${KIBANA_SPACE}"
  out=$(curl -sS --fail-with-body "${auth[@]}" -X POST \
    "$KIBANA_URL$space_path/api/saved_objects/_import?overwrite=true" \
    -H 'kbn-xsrf: true' -F file=@kibana/kea-dhcp-dashboard.ndjson) || {
    echo "FAILED: Kibana import" >&2
    echo "$out" >&2
    exit 1
  }
  if [[ "$out" != *'"success":true'* ]]; then
    echo "FAILED: Kibana import" >&2
    echo "$out" >&2
    exit 1
  fi
  echo "ok: imported Kibana dashboard"
fi
