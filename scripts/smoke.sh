#!/usr/bin/env bash
# End-to-end check: register on auth-service, then call ledger-api with the issued token.
set -euo pipefail
AUTH=${AUTH_URL:-http://localhost:4000}
API=${API_URL:-http://localhost:8000}
EMAIL="smoke+$(date +%s)@example.com"
JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT

json() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

echo "→ register $EMAIL"
TOKEN=$(curl -fsS -c "$JAR" -H 'content-type: application/json' \
  -d "{\"orgName\":\"Smoke Co\",\"email\":\"$EMAIL\",\"password\":\"correct-horse-battery\"}" \
  "$AUTH/auth/register" | json "['accessToken']")

echo "→ ledger-api /me";       curl -fsS -H "authorization: Bearer $TOKEN" "$API/me"; echo
echo "→ create vendor";        curl -fsS -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
                                 -d '{"name":"Acme Supplies","email":"ap@acme-supplies.com"}' "$API/vendors"; echo
echo "→ list vendors";         curl -fsS -H "authorization: Bearer $TOKEN" "$API/vendors"; echo
echo "→ refresh (rotates)";    curl -fsS -b "$JAR" -c "$JAR" -X POST "$AUTH/auth/refresh" | json "['user']['email']"
echo "→ bad token rejected";   code=$(curl -s -o /dev/null -w '%{http_code}' -H 'authorization: Bearer nope' "$API/me")
[[ "$code" == 401 ]] && echo "401 ✓" || { echo "expected 401, got $code"; exit 1; }
echo "smoke test passed"
