#!/usr/bin/env bash
# Run the full test suite against throwaway Elasticsearch and Kibana containers.
#
# Usage: scripts/test-local.sh [--keep] [--no-kibana]
#
#   --keep        leave the containers running afterwards (Kibana stays on
#                 http://localhost:$KIBANA_PORT with demo data loaded)
#   --no-kibana   skip the Kibana dashboard import check
#
# Environment:
#   STACK_VERSION  Elastic Stack version to test (default 8.15.3; 9.x works too)
#   ES_PORT        host port for Elasticsearch (default 9200)
#   KIBANA_PORT    host port for Kibana (default 5601)
#   CONTAINER_CLI  container engine (default podman)
#   ES_IMAGE / KIBANA_IMAGE  override the image names (default docker.elastic.co)
#
# Host requirements (AlmaLinux 10, including under WSL):
#   dnf install -y podman python3 python3-pyyaml curl
set -euo pipefail

cd "$(dirname "$0")/.."

STACK_VERSION="${STACK_VERSION:-8.15.3}"
ES_PORT="${ES_PORT:-9200}"
KIBANA_PORT="${KIBANA_PORT:-5601}"
CONTAINER_CLI="${CONTAINER_CLI:-podman}"
ES_IMAGE="${ES_IMAGE:-docker.elastic.co/elasticsearch/elasticsearch:${STACK_VERSION}}"
KIBANA_IMAGE="${KIBANA_IMAGE:-docker.elastic.co/kibana/kibana:${STACK_VERSION}}"
KEEP=false
KIBANA=true
for arg in "$@"; do
  case "$arg" in
    --keep) KEEP=true ;;
    --no-kibana) KIBANA=false ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

NET=kea-test
ES_NAME=kea-test-es
KB_NAME=kea-test-kibana
ES_URL="http://localhost:${ES_PORT}"
KIBANA_URL="http://localhost:${KIBANA_PORT}"

for cmd in "$CONTAINER_CLI" curl python3; do
  command -v "$cmd" >/dev/null || { echo "missing required command: $cmd" >&2; exit 1; }
done
python3 -c 'import yaml' 2>/dev/null || {
  echo "missing python3 yaml module (dnf install python3-pyyaml)" >&2
  exit 1
}

cleanup() {
  if ! $KEEP; then
    "$CONTAINER_CLI" rm -f "$KB_NAME" "$ES_NAME" >/dev/null 2>&1 || true
    "$CONTAINER_CLI" network rm "$NET" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

wait_for() {
  local what=$1 check=$2 tries=$3
  for _ in $(seq "$tries"); do
    if eval "$check" >/dev/null 2>&1; then
      echo "ok: $what is up"
      return 0
    fi
    sleep 5
  done
  echo "FAILED: $what did not come up" >&2
  exit 1
}

step() { printf '\n== %s\n' "$*"; }

step "Generated files are current"
python3 scripts/build.py --check
python3 scripts/build_dashboard.py --check
bash -n scripts/install.sh
echo "ok"

step "Starting Elasticsearch ${STACK_VERSION}"
"$CONTAINER_CLI" rm -f "$KB_NAME" "$ES_NAME" >/dev/null 2>&1 || true
"$CONTAINER_CLI" network create "$NET" >/dev/null 2>&1 || true
"$CONTAINER_CLI" run -d --name "$ES_NAME" --network "$NET" -p "${ES_PORT}:9200" \
  -e discovery.type=single-node \
  -e xpack.security.enabled=false \
  -e cluster.routing.allocation.disk.threshold_enabled=false \
  -e ES_JAVA_OPTS="-Xms1g -Xmx1g" \
  "$ES_IMAGE" >/dev/null
if $KIBANA; then
  "$CONTAINER_CLI" run -d --name "$KB_NAME" --network "$NET" -p "${KIBANA_PORT}:5601" \
    -e ELASTICSEARCH_HOSTS="http://${ES_NAME}:9200" \
    "$KIBANA_IMAGE" >/dev/null
fi
wait_for Elasticsearch "curl -fs '${ES_URL}/_cluster/health?wait_for_status=yellow&timeout=5s'" 60

step "Pipeline tests"
ES_URL="$ES_URL" KIBANA_URL="" ./scripts/install.sh
ES_URL="$ES_URL" python3 tests/test_pipeline.py

if $KIBANA; then
  step "Dashboard import (Kibana ${STACK_VERSION})"
  wait_for Kibana "curl -fs '${KIBANA_URL}/api/status' | grep -q '\"level\":\"available\"'" 72
  ES_URL="$ES_URL" KIBANA_URL="$KIBANA_URL" ./scripts/install.sh
  ES_URL="$ES_URL" python3 scripts/demo_data.py --hours 24
  KIBANA_URL="$KIBANA_URL" python3 tests/check_kibana.py
fi

step "All tests passed"
if $KEEP && $KIBANA; then
  echo "Containers left running. Dashboard: ${KIBANA_URL}/app/dashboards#/view/kea-dhcp-overview"
  echo "Remove with: $CONTAINER_CLI rm -f $KB_NAME $ES_NAME && $CONTAINER_CLI network rm $NET"
fi
