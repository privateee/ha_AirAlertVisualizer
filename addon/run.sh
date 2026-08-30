#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091
source /usr/lib/bashio/bashio.sh

export DRONEVIS_HOST="0.0.0.0"
export DRONEVIS_PORT="8099"                       # matches ingress_port
export DRONEVIS_DB_PATH="/data/dronevis.db"       # /data is the add-on's persistent store

export DRONEVIS_CHANNELS="$(bashio::config 'channels' | paste -sd, -)"
export DRONEVIS_POLL_INTERVAL="$(bashio::config 'poll_interval_seconds')"
export DRONEVIS_BACKFILL_PAGES="$(bashio::config 'backfill_pages')"
export DRONEVIS_AREA_LABEL="$(bashio::config 'area_label')"
export DRONEVIS_AREA_CENTER="$(bashio::config 'area_center_lat'),$(bashio::config 'area_center_lon')"
export DRONEVIS_AREA_RADIUS_KM="$(bashio::config 'area_radius_km')"
export DRONEVIS_MAP_THEME="$(bashio::config 'map_theme')"
export DRONEVIS_TILE_URL="$(bashio::config 'tile_url')"
export DRONEVIS_TILE_URL_DARK="$(bashio::config 'tile_url_dark' '')"
[ "${DRONEVIS_TILE_URL_DARK}" = "null" ] && export DRONEVIS_TILE_URL_DARK=""
export DRONEVIS_LOG_LEVEL="$(bashio::config 'log_level')"

bashio::log.info "DroneVisualizer -> channels: ${DRONEVIS_CHANNELS}"
bashio::log.info "Open it from the sidebar (ingress) or Add-on 'Open Web UI'."

exec dronevis run
