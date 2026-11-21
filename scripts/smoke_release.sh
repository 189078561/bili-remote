#!/usr/bin/env bash
set -euo pipefail
image=${1:?pass the local Bili Remote image}
source_dir=$(cd "$(dirname "$0")/.." && pwd)
test_dir=$(mktemp -d)
project="bili-release-test-${test_dir##*.}"
project=${project,,}
cd "$test_dir"
cp "$source_dir/docker-compose.yml" compose.yaml
export BILI_REMOTE_IMAGE="$image" BILI_REMOTE_BIND=127.0.0.1 BILI_REMOTE_PORT=0
# Named volumes are unique to this Compose project and start with the app UID's permissions.
for name in data downloads imports staging releases torrents images release-info; do
  volume=${name//-/_}
  if [[ "$name" = downloads ]]; then variable=DOWNLOAD; else variable=${volume^^}; fi
  printf '%s_ROOT_HOST=%s\n' "$variable" "$volume" >> .env
done
cat >> compose.yaml <<'YAML'

volumes:
  data: {}
  downloads: {}
  imports: {}
  staging: {}
  releases: {}
  torrents: {}
  images: {}
  release_info: {}
YAML
printf 'APP_SECRET=%s\nADMIN_PASSWORD=%s\n' "$(openssl rand -hex 32)" "$(openssl rand -hex 20)" >> .env
chmod 600 .env
compose=(docker compose -p "$project" -f compose.yaml)
cleanup() {
  "${compose[@]}" down --volumes >/dev/null
  rm -f -- .env compose.yaml
  rmdir "$test_dir"
}
trap cleanup EXIT
"${compose[@]}" up -d --no-build --wait --wait-timeout 150
"${compose[@]}" exec -T bili-remote python - <<'PY'
import json
from urllib.error import HTTPError
from urllib.request import urlopen

from bili_remote.main import app

services = app.state.services
services.v2.configure()
endpoint = services.metadata.ptgen_endpoint(services.database.get_settings())
assert services.v2.resolver.providers["ptgen"].endpoint == endpoint
assert endpoint == "http://pt-gen:23333/api/v1/info"
assert urlopen("http://localhost:8080/healthz", timeout=5).status == 200
for route in ("info", "search"):
    try:
        urlopen(endpoint.rsplit("/", 1)[0] + "/" + route, timeout=5)
    except HTTPError as exc:
        assert exc.code == 400 and json.load(exc).get("error"), route
    else:
        raise AssertionError("PT-Gen should require an input for " + route)
print("Fresh install OK: application, PT-Gen detail and search endpoints")
PY
