#!/usr/bin/env bash
# End-to-end smoke test: start the server on a throwaway DB, upload every file in samples/ with
# curl, print the status and key fields, and exit non-zero on any unexpected status.
#
#   scripts/smoke.sh            (uses `python` and `uvicorn` from PATH; override with PYTHON=...)
set -u
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"
WORK="$(mktemp -d)"
PORT="${PORT:-$("$PYTHON" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')}"
BASE="http://127.0.0.1:$PORT"
SERVER_PID=""

cleanup() {
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

GEO_API_DB="$WORK/smoke.db" "$PYTHON" -m uvicorn app.main:app --port "$PORT" --log-level warning \
  >"$WORK/server.log" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 50); do
  curl -fs "$BASE/health" >/dev/null 2>&1 && break
  sleep 0.2
done
if ! curl -fs "$BASE/health" >/dev/null 2>&1; then
  echo "server did not start:"; cat "$WORK/server.log"; exit 2
fi
echo "server up on $BASE (temp DB)"

field() { # field <json-file> <python expression over d>
  "$PYTHON" -c 'import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2]))' "$1" "$2"
}

failures=0
while read -r name expected; do
  status=$(curl -s -o "$WORK/out.json" -w '%{http_code}' -F "file=@samples/$name" "$BASE/api/files/")
  if [ "$status" = "$expected" ]; then verdict="ok"; else verdict="UNEXPECTED (wanted $expected)"; failures=$((failures+1)); fi
  printf '%-24s POST -> %s  %s\n' "$name" "$status" "$verdict"
  case "$status" in
    201)
      id=$(field "$WORK/out.json" 'd["id"]')
      echo "    info: $(field "$WORK/out.json" '"format=%s crs=%s features=%s status=%s" % (d["format"], d["crs"], d["feature_count"], d["status"])')"
      g=$(curl -s -o "$WORK/info.json" -w '%{http_code}' "$BASE/api/files/$id/")
      m=$(curl -s -o "$WORK/meas.json" -w '%{http_code}' "$BASE/api/files/$id/measurements/")
      printf '    GET info -> %s, GET measurements -> %s\n' "$g" "$m"
      [ "$g" = 200 ] && [ "$m" = 200 ] || failures=$((failures+1))
      field "$WORK/meas.json" '"\n".join("    #%s %-10s area_m2=%s length_m=%s %s" % (f["index"], f["geometry_type"], f["measurement"]["area_m2"], f["measurement"]["length_m"], f["measurement"]["reason"] or "") for f in d["features"])'
      ;;
    422)
      id=$(field "$WORK/out.json" 'd["error"]["file_id"]')
      echo "    error: $(field "$WORK/out.json" 'd["error"]["code"]')"
      g=$(curl -s -o "$WORK/info.json" -w '%{http_code}' "$BASE/api/files/$id/")
      m=$(curl -s -o "$WORK/meas.json" -w '%{http_code}' "$BASE/api/files/$id/measurements/")
      printf '    GET info -> %s (%s), GET measurements -> %s\n' "$g" "$(field "$WORK/info.json" 'd["status"]')" "$m"
      [ "$g" = 200 ] && [ "$m" = 409 ] || failures=$((failures+1))
      ;;
    *)
      echo "    error: $(field "$WORK/out.json" 'd["error"]["code"]') - $(field "$WORK/out.json" 'd["error"]["message"][:70]')"
      ;;
  esac
done < <("$PYTHON" -c 'from scripts.make_samples import EXPECTED_STATUS as E; [print(k, v) for k, v in E.items()]')

listed=$(curl -s "$BASE/api/files/" | "$PYTHON" -c 'import json,sys; print(len(json.load(sys.stdin)))')
echo "GET /api/files/ lists $listed records"

if [ "$failures" -ne 0 ]; then echo "SMOKE FAILED: $failures unexpected result(s)"; exit 1; fi
echo "SMOKE OK"
