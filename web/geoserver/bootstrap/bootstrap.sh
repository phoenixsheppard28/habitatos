#!/bin/sh
set -eu

base="http://geoserver:8080/geoserver"
auth="${GEOSERVER_ADMIN_USER}:${GEOSERVER_ADMIN_PASSWORD}"

echo "Waiting for GeoServer REST API..."
attempt=0
until curl --silent --fail --user "$auth" "$base/rest/about/version.json" >/dev/null; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 90 ]; then
    echo "GeoServer did not become ready in time." >&2
    exit 1
  fi
  sleep 4
done

request() {
  method="$1"; path="$2"; content_type="$3"; data="$4"
  status=$(curl --silent --output /tmp/geoserver-response --write-out '%{http_code}' --user "$auth" \
    --request "$method" --header "Content-Type: $content_type" --data "$data" "$base$path")
  case "$status" in 200|201|202) ;; 409) ;; *) cat /tmp/geoserver-response >&2; echo "REST $method $path failed with HTTP $status" >&2; exit 1;; esac
}

request POST "/rest/workspaces" "application/json" '{"workspace":{"name":"habitat"}}'
request POST "/rest/workspaces/habitat/datastores" "application/json" "{\"dataStore\":{\"name\":\"habitat\",\"connectionParameters\":{\"entry\":[{\"@key\":\"dbtype\",\"$\":\"postgis\"},{\"@key\":\"host\",\"$\":\"postgis\"},{\"@key\":\"port\",\"$\":\"5432\"},{\"@key\":\"database\",\"$\":\"habitat\"},{\"@key\":\"schema\",\"$\":\"public\"},{\"@key\":\"user\",\"$\":\"habitat\"},{\"@key\":\"passwd\",\"$\":\"${POSTGRES_PASSWORD}\"}]}}}"

for layer in movement_points study_boundary rainfall_zones vegetation_extent; do
  request POST "/rest/workspaces/habitat/datastores/habitat/featuretypes" "application/json" "{\"featureType\":{\"name\":\"$layer\",\"nativeName\":\"$layer\",\"srs\":\"EPSG:4326\",\"enabled\":true}}"
  request POST "/rest/workspaces/habitat/styles?name=$layer" "application/vnd.ogc.sld+xml" "$(cat "/bootstrap/$layer.sld")"
  request PUT "/rest/layers/habitat:$layer" "application/json" "{\"layer\":{\"defaultStyle\":{\"name\":\"habitat:$layer\"}}}"
done

for layer in movement_points rainfall_zones; do
  request PUT "/rest/workspaces/habitat/datastores/habitat/featuretypes/$layer" "application/json" "{\"featureType\":{\"metadata\":{\"entry\":[{\"@key\":\"time\",\"dimensionInfo\":{\"enabled\":true,\"attribute\":\"observed_at\",\"presentation\":\"LIST\",\"units\":\"ISO8601\",\"defaultValue\":{\"strategy\":\"MAXIMUM\"}}}]}}}"
done

echo "Habitat sample workspace is published."
