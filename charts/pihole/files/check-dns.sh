#!/bin/bash
set -Eeuo pipefail
trap 'echo "Validation failed at line $LINENO" >&2' ERR
HELM_CHECK_WEB_PORT=${HELM_CHECK_WEB_PORT:?}
query() {
    local server=$1 port=$2 tcp=$3 name=$4 expected=$5 response attempt
    local -a flags=(+notcp)
    [[ "$tcp" != true ]] || flags=(+tcp)
    # A replaced StatefulSet pod can leave a cached discovery address briefly.
    # Retry transport failures within the Pod deadline; semantic DNS failures
    # and incorrect filtering answers below still fail immediately.
    for attempt in {1..12}; do
        if response=$(dig +time=2 +tries=1 +noall +comments +answer "${flags[@]}" "@$server" -p "$port" "$name" A 2>&1); then break; fi
        if [[ "$attempt" == 12 ]]; then
            printf 'DNS transport failed: server=%s port=%s tcp=%s name=%s\n%s\n' "$server" "$port" "$tcp" "$name" "$response" >&2
            return 1
        fi
        sleep 1
    done
    if [[ "$expected" == NXDOMAIN ]]; then
        grep -q 'status: NXDOMAIN' <<< "$response"
    else
        grep -q 'status: NOERROR' <<< "$response"
        awk -v expected="$expected" '$0 !~ /^;/ && $4 == "A" && (expected == "" || $5 == expected) { ok=1 } END { exit !ok }' <<< "$response"
    fi
}
check_dns() {
    local server=$1 port=$2 protocol=$3
    query "$server" "$port" "$protocol" "$HELM_CHECK_NAME" "$HELM_CHECK_ANSWER"
    if [[ -n "$HELM_CHECK_BLOCKED" ]]; then query "$server" "$port" "$protocol" "$HELM_CHECK_BLOCKED" "$HELM_CHECK_BLOCKED_ANSWER"; fi
    if [[ -n "$HELM_CHECK_UPSTREAM" ]]; then query "$server" "$port" "$protocol" "$HELM_CHECK_UPSTREAM" ''; fi
}
authenticate() {
    local target=$1 port=$2 sid response
    response=$(jq -nc --rawfile password /credentials/password '{password:$password}' | curl -fsS --max-time 10 \
        -H 'Content-Type: application/json' --data-binary @- "http://$target:$port/api/auth")
    jq -e '.session.valid == true' >/dev/null <<< "$response"
    sid=$(jq -er .session.sid <<< "$response")
    curl -fsS --max-time 5 -X DELETE -H "sid: $sid" "http://$target:$port/api/auth" >/dev/null
}
if [[ -n "$HELM_CHECK_SERVICE_UDP" ]]; then check_dns "$HELM_CHECK_SERVICE_UDP" "$HELM_CHECK_SERVICE_UDP_PORT" false; fi
if [[ -n "$HELM_CHECK_SERVICE_TCP" ]]; then check_dns "$HELM_CHECK_SERVICE_TCP" "$HELM_CHECK_SERVICE_TCP_PORT" true; fi
if [[ "$HELM_CHECK_METRICS" == true ]]; then
    metrics=$(curl -fsS --max-time 15 "http://$HELM_CHECK_METRICS_HOST:$HELM_CHECK_METRICS_PORT/metrics")
fi
for target in ${HELM_CHECK_TARGETS//,/ }; do
    check_dns "$target" "$HELM_CHECK_DNS_PORT" false
    check_dns "$target" "$HELM_CHECK_DNS_PORT" true
    if [[ "$HELM_CHECK_AUTH" == true ]]; then authenticate "$target" "$HELM_CHECK_HTTP_PORT"; fi
    if [[ "$HELM_CHECK_METRICS" == true ]]; then
        awk -v target="$target" '/^pihole_status\{/ && index($0, "hostname=\"" target "\"") && $NF == 1 {ok=1} END {exit !ok}' <<< "$metrics"
    fi
done
if [[ "$HELM_CHECK_AUTH" == true && -n "$HELM_CHECK_WEB_HOST" ]]; then authenticate "$HELM_CHECK_WEB_HOST" "$HELM_CHECK_WEB_PORT"; fi
echo 'All configured per-replica and Service DNS, filtering, authentication and metrics checks passed'
