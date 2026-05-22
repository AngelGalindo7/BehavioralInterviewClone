#!/usr/bin/env bash
# Snapshot a Grafana panel's config + last-1h query results to a tagged
# folder. Used to freeze "before optimisation X" data so it can be compared
# against the "after" run reproducibly, even if the dashboard JSON changes
# in the meantime.
#
# Output layout:
#   snapshots/<tag>/panel_<id>.json
#     { meta:  {tag, dashboard_uid, panel_id, panel_title, from, to, captured_at},
#       panel: <panel config block from the dashboard JSON>,
#       data:  <response from /api/ds/query for the panel's targets> }
#
# Usage:
#   GRAFANA_URL=http://localhost:3000 \
#   GRAFANA_TOKEN=glsa_... \
#   ./snapshot_panel.sh <tag> [panel_id]
#
# Examples:
#   ./snapshot_panel.sh before-llm-streaming         # snapshot every panel
#   ./snapshot_panel.sh after-llm-streaming  6       # snapshot panel id=6
#
# Optional env overrides:
#   DASHBOARD_UID   default: behavioral-dummy
#   TIME_FROM       default: now-1h   (Grafana range expression)
#   TIME_TO         default: now
#   SNAPSHOT_DIR    default: ./snapshots (relative to PWD)
#   LOKI_NAME       default: Loki     (datasource name in Grafana)

set -euo pipefail

usage() {
  cat <<USAGE
Usage: GRAFANA_URL=... GRAFANA_TOKEN=... $0 <tag> [panel_id]

Required:
  GRAFANA_URL     Base URL of the Grafana server (e.g. http://localhost:3000)
  GRAFANA_TOKEN   Service-account token (Viewer scope is sufficient)
  <tag>           Free-form label for this snapshot (e.g. before-llm-streaming)

Optional:
  panel_id        Specific panel to snapshot. Omit to snapshot every panel.

Optional env:
  DASHBOARD_UID   default: behavioral-dummy
  TIME_FROM       default: now-1h   (Grafana range expression)
  TIME_TO         default: now
  SNAPSHOT_DIR    default: ./snapshots
  LOKI_NAME       default: Loki
USAGE
  exit 1
}

[[ $# -ge 1 ]] || usage
[[ -n "${GRAFANA_URL:-}" && -n "${GRAFANA_TOKEN:-}" ]] || usage
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v jq   >/dev/null || { echo "jq is required"   >&2; exit 1; }

TAG="$1"
PANEL_ID="${2:-}"
DASHBOARD_UID="${DASHBOARD_UID:-behavioral-dummy}"
TIME_FROM="${TIME_FROM:-now-1h}"
TIME_TO="${TIME_TO:-now}"
SNAPSHOT_DIR="${SNAPSHOT_DIR:-./snapshots}"
LOKI_NAME="${LOKI_NAME:-Loki}"

OUT_DIR="${SNAPSHOT_DIR}/${TAG}"
mkdir -p "${OUT_DIR}"

CAPTURED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
GRAFANA_URL="${GRAFANA_URL%/}"

# Resolve the Loki datasource UID once. /api/ds/query requires the modern
# {type, uid} datasource shape per query, but our dashboard JSON still uses
# the legacy "datasource": "Loki" string form, so we inject the resolved
# UID into each target below.
LOKI_UID="$(curl -fsS -H "Authorization: Bearer ${GRAFANA_TOKEN}" \
  "${GRAFANA_URL}/api/datasources/name/${LOKI_NAME}" | jq -r '.uid')"
if [[ -z "${LOKI_UID}" || "${LOKI_UID}" == "null" ]]; then
  echo "Could not resolve Loki datasource UID for name '${LOKI_NAME}'" >&2
  exit 1
fi

DASH_JSON="$(curl -fsS -H "Authorization: Bearer ${GRAFANA_TOKEN}" \
  "${GRAFANA_URL}/api/dashboards/uid/${DASHBOARD_UID}")"

if [[ -n "${PANEL_ID}" ]]; then
  PANEL_IDS="${PANEL_ID}"
else
  PANEL_IDS="$(echo "${DASH_JSON}" | jq -r '.dashboard.panels[].id')"
fi

for pid in ${PANEL_IDS}; do
  PANEL_CONFIG="$(echo "${DASH_JSON}" | jq --argjson pid "${pid}" \
    '.dashboard.panels[] | select(.id == $pid)')"
  if [[ -z "${PANEL_CONFIG}" || "${PANEL_CONFIG}" == "null" ]]; then
    echo "panel id=${pid} not found in dashboard ${DASHBOARD_UID} — skipping" >&2
    continue
  fi
  PANEL_TITLE="$(echo "${PANEL_CONFIG}" | jq -r '.title // "untitled"')"

  QUERY_BODY="$(echo "${PANEL_CONFIG}" | jq \
    --arg from "${TIME_FROM}" --arg to "${TIME_TO}" --arg loki_uid "${LOKI_UID}" '{
      queries: [.targets[] | . + {datasource: {type: "loki", uid: $loki_uid}}],
      from: $from,
      to: $to
    }')"

  DATA="$(curl -fsS -H "Authorization: Bearer ${GRAFANA_TOKEN}" \
    -H "Content-Type: application/json" \
    -X POST -d "${QUERY_BODY}" \
    "${GRAFANA_URL}/api/ds/query" \
    || echo '{"error":"query_failed"}')"

  OUT="${OUT_DIR}/panel_${pid}.json"
  jq -n \
    --arg tag "${TAG}" \
    --arg dashboard_uid "${DASHBOARD_UID}" \
    --argjson panel_id "${pid}" \
    --arg panel_title "${PANEL_TITLE}" \
    --arg from "${TIME_FROM}" \
    --arg to "${TIME_TO}" \
    --arg captured_at "${CAPTURED_AT}" \
    --argjson panel "${PANEL_CONFIG}" \
    --argjson data "${DATA}" \
    '{meta: {tag: $tag, dashboard_uid: $dashboard_uid, panel_id: $panel_id, panel_title: $panel_title, from: $from, to: $to, captured_at: $captured_at}, panel: $panel, data: $data}' \
    > "${OUT}"
  echo "wrote ${OUT}  (${PANEL_TITLE})"
done
