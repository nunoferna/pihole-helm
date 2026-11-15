#!/bin/bash
# Local, bounded policy reconciliation. No SQL or generated executable input.
set -euo pipefail
umask 077
if [[ ${1:-} != --bounded ]]; then
    exec timeout "${HELM_POLICY_TIMEOUT:-180}" /bin/bash "$0" --bounded "$@"
fi
ready=/run/pihole-helm-policy-ready
exec 9>/etc/pihole/.helm-policy.lock
flock -n 9 || { echo 'Another policy operation holds the local lock'; exit 1; }
exec > /etc/pihole/helm-import.log 2>&1
# Scheduled checks of the same immutable revision must not withdraw every
# healthy replica merely because randomized refresh times happen to overlap.
trap 'status=$?; if [[ "$status" != 0 ]]; then rm -f "$ready"; fi' EXIT
if [[ ${2:-} != --refresh ]] || [[ $(cat "$ready" 2>/dev/null || true) != "$HELM_POLICY_EXPECTED_REVISION" ]]; then
    rm -f "$ready"
fi
policy=${HELM_POLICY_FILE:-/etc/pihole-helm/policy.json}
filters=${HELM_POLICY_FILTER:-/etc/pihole-helm/policy.jq}
revision=$(sha256sum "$policy" | cut -d ' ' -f 1)
[[ "$revision" == "$HELM_POLICY_EXPECTED_REVISION" ]] || { echo 'Policy artifact checksum mismatch'; exit 1; }
owner=$(jq -er .owner "$policy")
[[ "$owner" == "$HELM_POLICY_OWNER" ]] || { echo 'Policy artifact belongs to another release'; exit 1; }
work=$(mktemp -d /run/pihole-policy.XXXXXX)
sid=""
api="http://127.0.0.1:${HELM_HTTP_PORT}/api"
state=/etc/pihole/.helm-policy-state.json
cleanup() {
    local status=$?
    if [[ "$status" != 0 ]]; then rm -f "$ready"; fi
    if [[ -s "$work/session" ]]; then sid=$(cat "$work/session"); fi
    if [[ -n "$sid" ]]; then
        curl -fsS --max-time 2 -X DELETE -H "sid: $sid" "$api/auth" >/dev/null || true
    fi
    rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 143' TERM INT
fail() { echo "$1"; exit 1; }
jq -ce -f "$filters" "$policy" > "$work/desired.json" || fail 'Policy input validation failed'
if [[ ${HELM_POLICY_REQUIRE_PINNED_LISTS:-false} == true ]]; then
    jq -e '.requirePinnedLists == true' "$policy" >/dev/null || fail 'Selected artifact does not enforce the required pinned-list profile'
fi
# Download and validate all pinned bodies before touching the live API. Cached
# verified content makes provider outages irrelevant to retained-volume startup.
mkdir -p /etc/pihole/helm-snapshots
chmod 755 /etc/pihole/helm-snapshots
while IFS= read -r item; do
    hash=$(jq -r .sha256 <<< "$item")
    target="/etc/pihole/helm-snapshots/$hash.txt"
    if [[ -f "$target" ]] && [[ $(sha256sum "$target" | cut -d ' ' -f 1) == "$hash" ]]; then continue; fi
    download=$(mktemp /etc/pihole/helm-snapshots/.download.XXXXXX)
    if ! curl -fsSL --proto '=https' --proto-redir '=https' --connect-timeout 5 --max-time 45 --max-filesize 67108864 \
        --output "$download" "$(jq -r .url <<< "$item")"; then
        rm -f "$download"; fail 'Pinned list download failed; live policy was not changed'
    fi
    if [[ $(sha256sum "$download" | cut -d ' ' -f 1) != "$hash" ]]; then
        rm -f "$download"; fail 'Pinned list checksum mismatch; live policy was not changed'
    fi
    chmod 444 "$download"; sync "$download"; mv "$download" "$target"; sync "$target"
done < <(jq -c '[.[]|select(.snapshot!=null)|.snapshot]|unique[]' "$work/desired.json")
mode=$(jq -r .mode "$policy")
adopt=$(jq -r .adoptExisting "$policy")
if [[ ! -e "$state" ]]; then
    jq -nc --arg owner "$owner" '{formatVersion:1,owner:$owner,revision:null,gravityDirty:false,entries:[]}' > "$state"
fi
jq -e --arg owner "$owner" '
  .formatVersion == 1 and .owner == $owner and (.entries | type == "array") and
  all(.entries[]; (.kind|type=="string") and (.key|type=="string") and
    (.claim|type=="string" and test("^[a-f0-9-]{36}$")) and
    (.id == null or (.id|type=="number")) and (.date_added == null or (.date_added|type=="number"))) and
  ([.entries[] | [.kind,.key]] | length == (unique|length))
' "$state" >/dev/null || fail 'Ownership journal is invalid or belongs to another release'

# Persist intent before changing the API; atomically replace and fsync the journal.
save_state() { chmod 600 "$state.next"; sync "$state.next"; mv "$state.next" "$state"; sync "$state"; }
entry_path() {
    local kind=$1 encoded
    encoded=$(jq -nr --arg key "$2" '$key|@uri')
    case "$kind" in
        groups|clients|domains/*) printf '%s/%s' "$kind" "$encoded" ;;
        lists/*) printf 'lists/%s?type=%s' "$encoded" "${kind#lists/}" ;;
        *) fail 'Unsupported journal entry type' ;;
    esac
}
collection_path() {
    case "$1" in lists/*) printf 'lists?type=%s' "${1#lists/}" ;; *) printf '%s' "$1" ;; esac
}
authenticate() {
    local response
    response=$(jq -nc --rawfile password /etc/pihole/cli_pw '{password:($password|rtrimstr("\n"))}' |
        curl -fsS --max-time 3 -H 'Content-Type: application/json' --data-binary @- "$api/auth") || return 1
    jq -e '.session.valid == true' >/dev/null <<< "$response" || return 1
    jq -er .session.sid <<< "$response" > "$work/session.next" || return 1
    mv "$work/session.next" "$work/session"
}
request() {
    local method=$1 path=$2 code attempt request_sid
    for attempt in 1 2; do
        request_sid=$(cat "$work/session")
        if [[ $# -eq 3 ]]; then
            code=$(printf '%s' "$3" | curl -sS --max-time 10 -o "$work/response" -w '%{http_code}' -X "$method" \
                -H "sid: $request_sid" -H 'Content-Type: application/json' --data-binary @- "$api/$path") || return 1
        else
            code=$(curl -sS --max-time 10 -o "$work/response" -w '%{http_code}' -X "$method" -H "sid: $request_sid" "$api/$path") || return 1
        fi
        if [[ "$code" == 401 && "$attempt" == 1 ]]; then
            # The local API rejects authentication before invoking a mutation.
            # Retry only that explicit rejection, never an ambiguous transport
            # failure. Persist the SID across Bash command-substitution shells.
            authenticate || return 1
            continue
        fi
        [[ "$code" =~ ^2[0-9][0-9]$ ]] || { echo "Policy API request failed: HTTP $code" >&2; return 1; }
        if [[ -s "$work/response" ]]; then
            jq -e '(.error // null) == null and ((.processed.errors // [])|length == 0)' "$work/response" >/dev/null || return 1
            cat "$work/response"
        fi
        return 0
    done
    return 1
}
until [[ -s /etc/pihole/cli_pw ]] && authenticate; do
    sleep 1
done
snapshot() {
    local kind
    : > "$work/snapshot.ndjson"
    for kind in groups clients lists/block lists/allow domains/allow/exact domains/allow/regex domains/deny/exact domains/deny/regex; do
        request GET "$(collection_path "$kind")" | jq -c --arg kind "$kind" '
          (.groups // .clients // .lists // .domains)[] |
          {kind:$kind,key:(if $kind=="groups" then .name elif $kind=="clients" then .client elif ($kind|startswith("lists/")) then .address else .domain end),record:.}' >> "$work/snapshot.ndjson"
    done
    jq -s . "$work/snapshot.ndjson" > "$work/current.json"
}
current_entry() { jq -c --arg kind "$1" --arg key "$2" '[.[]|select(.kind==$kind and .key==$key)] | if length>1 then error("Ambiguous API identity") else .[0] // null end' "$work/current.json"; }
journal_entry() { jq -c --arg kind "$1" --arg key "$2" '[.entries[]|select(.kind==$kind and .key==$key)][0] // null' "$state"; }
owned() {
    jq -en --argjson j "$1" --argjson c "$2" '
      $j != null and $c != null and
      ($j.id == null or ($j.id == $c.record.id and $j.date_added == $c.record.date_added)) and
      (($c.record.comment // "" | startswith("[helm-policy:" + $j.claim + "] ")) or
       ($j.adopting == true and $j.id == $c.record.id and $j.date_added == $c.record.date_added and
        $j.previousComment == $c.record.comment))' >/dev/null
}
snapshot

if jq -e '.requirePinnedLists == true' "$policy" >/dev/null; then
    # Owned obsolete feeds may be removed by reconciliation. Every other
    # enabled feed must be one of this revision's verified local snapshots.
    jq -e --slurpfile desired "$work/desired.json" --slurpfile state "$state" --arg mode "$mode" '
      all(.[]|select((.kind|startswith("lists/")) and .record.enabled);
        . as $c |
        any($desired[0][]; .kind==$c.kind and .key==$c.key and .snapshot!=null) or
        ($mode=="reconcile" and any($state[0].entries[]; .kind==$c.kind and .key==$c.key) and
         (any($desired[0][]; .kind==$c.kind and .key==$c.key)|not)))
    ' "$work/current.json" >/dev/null || fail 'Pinned-list mode refuses enabled unmanaged or unverified feeds; disable them explicitly first'
fi

# Validate ALL collisions and identities before the first API mutation.
while IFS= read -r item; do
    kind=$(jq -r .kind <<< "$item"); key=$(jq -r .key <<< "$item")
    case "$kind" in groups|clients|lists/block|lists/allow|domains/allow/exact|domains/allow/regex|domains/deny/exact|domains/deny/regex) ;; *) fail 'Unknown journal type' ;; esac
    [[ "$kind:$key" != groups:Default ]] || fail 'Built-in Default group cannot be owned'
    current=$(current_entry "$kind" "$key")
    if [[ "$kind" == groups && "$current" != null ]] && [[ $(jq .record.id <<< "$current") == 0 ]]; then fail 'Built-in group ID 0 cannot be owned'; fi
    if [[ "$current" != null ]] && ! owned "$item" "$current"; then fail 'Owned record identity changed; refusing unsafe overwrite/deletion'; fi
done < <(jq -c '.entries[]' "$state")
while IFS= read -r item; do
    kind=$(jq -r .kind <<< "$item"); key=$(jq -r .key <<< "$item")
    current=$(current_entry "$kind" "$key"); journal=$(journal_entry "$kind" "$key")
    if [[ "$kind" == groups && "$current" != null ]] && [[ $(jq .record.id <<< "$current") == 0 ]]; then fail 'Built-in group ID 0 cannot be adopted'; fi
    if [[ "$current" != null && "$journal" == null && "$adopt" != true ]]; then fail 'Policy collides with an unmanaged record; explicit adoption required'; fi
done < <(jq -c '.[]' "$work/desired.json")

# Removed owned groups cannot survive silently through dangling desired references.
jq -ce --arg mode "$mode" --slurpfile state "$state" --slurpfile desired "$work/desired.json" '
  ([.[]|select(.kind=="groups")|select(.key as $k|
    $mode=="additive" or (any($state[0].entries[]; .kind=="groups" and .key==$k)|not) or
    any($desired[0][]; .kind=="groups" and .key==$k))|.key] +
   [$desired[0][]|select(.kind=="groups")|.key]) | unique' "$work/current.json" > "$work/group-names.json"
jq -e --slurpfile names "$work/group-names.json" 'all(.[]; all(.groups[]?; . as $g | $names[0]|index($g)!=null))' "$work/desired.json" >/dev/null || fail 'Policy references an unknown or removed group'

# Group deletion must not cascade into records outside this release's ownership.
if [[ "$mode" == reconcile ]]; then
    while IFS= read -r item; do
        kind=$(jq -r .kind <<< "$item"); key=$(jq -r .key <<< "$item")
        [[ "$kind" == groups ]] || continue
        jq -e --arg key "$key" 'any(.[]; .kind=="groups" and .key==$key)' "$work/desired.json" >/dev/null && continue
        current=$(current_entry "$kind" "$key")
        [[ "$current" != null ]] || continue
        id=$(jq .record.id <<< "$current")
        jq -e --argjson id "$id" --slurpfile journal "$state" '
          all(.[]|select(.kind!="groups" and ((.record.groups // [])|index($id)!=null));
            . as $c|any($journal[0].entries[]; .kind==$c.kind and .key==$c.key))' "$work/current.json" >/dev/null || fail 'Removed group is referenced by unmanaged records'
    done < <(jq -c '.entries[]' "$state")
fi

while IFS= read -r item; do
    kind=$(jq -r .kind <<< "$item"); key=$(jq -r .key <<< "$item")
    current=$(current_entry "$kind" "$key"); journal=$(journal_entry "$kind" "$key")
    if [[ "$journal" == null ]]; then
        claim=$(cat /proc/sys/kernel/random/uuid)
        journal=$(jq -nc --arg kind "$kind" --arg key "$key" --arg claim "$claim" --argjson current "$current" \
            '{kind:$kind,key:$key,claim:$claim,id:$current.record.id,date_added:$current.record.date_added,adopting:($current!=null),previousComment:$current.record.comment}')
    elif [[ "$current" == null ]]; then
        journal=$(jq '.id=null|.date_added=null|.adopting=false|del(.previousComment)' <<< "$journal")
    fi
    jq --argjson entry "$journal" '.entries = ([.entries[]|select(.kind!=$entry.kind or .key!=$entry.key)] + [$entry])' "$state" > "$state.next"
    save_state
    groups=$(jq -c .groups <<< "$item")
    body=$(jq -c --arg claim "$(jq -r .claim <<< "$journal")" '.body | .comment=("[helm-policy:"+$claim+"] "+.comment)' <<< "$item")
    if [[ "$groups" != null ]]; then
        ids=$(jq -ce --argjson names "$groups" '[ $names[] as $name | [.[]|select(.kind=="groups" and .key==$name)|.record.id] | if length==1 then .[0] else error("Ambiguous group") end ] | sort' "$work/current.json")
        body=$(jq -c --argjson ids "$ids" '.groups=$ids' <<< "$body")
    fi
    changed=false
    if [[ "$current" == null ]] || ! jq -en --argjson c "$current" --argjson body "$body" 'all($body|keys[]; . as $k | if $k=="groups" then ($body[$k]|sort)==($c.record[$k]|sort) else $body[$k]==$c.record[$k] end)' >/dev/null; then
        changed=true
        rm -f "$ready"
        # Persist the rebuild intent BEFORE mutating a list. An unchanged list
        # must not make every pod restart depend on remote list providers.
        if [[ "$kind" == lists/* ]]; then
            jq '.gravityDirty=true' "$state" > "$state.next"; save_state
        fi
    fi
    if [[ "$current" == null ]]; then
        request POST "$(collection_path "$kind")" "$body" >/dev/null || fail 'Policy create failed'
    elif [[ "$changed" == true ]]; then
        request PUT "$(entry_path "$kind" "$key")" "$body" >/dev/null || fail 'Policy update failed'
    fi
    # Fetch only the affected record, avoiding a full collection GET per rule.
    updated=$(request GET "$(entry_path "$kind" "$key")" | jq -ce --arg kind "$kind" --arg key "$key" \
        '[(.groups // .clients // .lists // .domains)[]|select((if $kind=="groups" then .name elif $kind=="clients" then .client elif ($kind|startswith("lists/")) then .address else .domain end)==$key)] |
         if length==1 then {kind:$kind,key:$key,record:.[0]} else error("API identity mismatch") end')
    owned "$journal" "$updated" || fail 'API ownership verification failed'
    jq -en --argjson c "$updated" --argjson body "$body" 'all($body|keys[]; . as $k | if $k=="groups" then ($body[$k]|sort)==($c.record[$k]|sort) else $body[$k]==$c.record[$k] end)' >/dev/null || fail 'API did not retain desired record fields'
    jq --argjson c "$updated" '. = ([.[]|select(.kind!=$c.kind or .key!=$c.key)] + [$c])' "$work/current.json" > "$work/current.next"
    mv "$work/current.next" "$work/current.json"
    jq --argjson c "$updated" '(.entries[]|select(.kind==$c.kind and .key==$c.key)) |= (.id=$c.record.id | .date_added=$c.record.date_added | .adopting=false | del(.previousComment))' "$state" > "$state.next"
    save_state
done < <(jq -c 'sort_by(if .kind=="groups" then 0 else 1 end)[]' "$work/desired.json")

if [[ "$mode" == reconcile ]]; then
    # Dependent records precede groups; write-ahead intents survive interruption.
    while IFS= read -r item; do
        kind=$(jq -r .kind <<< "$item"); key=$(jq -r .key <<< "$item")
        jq -e --arg kind "$kind" --arg key "$key" 'any(.[]; .kind==$kind and .key==$key)' "$work/desired.json" >/dev/null && continue
        current=$(current_entry "$kind" "$key")
        if [[ "$current" != null ]]; then rm -f "$ready"; fi
        if [[ "$kind" == lists/* && "$current" != null ]]; then
            jq '.gravityDirty=true' "$state" > "$state.next"; save_state
        fi
        if [[ "$current" != null ]]; then
            owned "$item" "$current" || fail 'Deletion ownership verification failed'
            [[ "$kind:$key" != groups:Default ]] || fail 'Built-in Default cannot be removed'
            request DELETE "$(entry_path "$kind" "$key")" >/dev/null || fail 'Policy deletion failed'
        fi
        jq --arg kind "$kind" --arg key "$key" '.entries |= map(select(.kind!=$kind or .key!=$key))' "$state" > "$state.next"
        save_state
    done < <(jq -c '.entries | sort_by(if .kind=="groups" then 1 else 0 end)[]' "$state")
fi

# Scheduled refresh uses the same validation, content verification and lock.
if [[ ${2:-} == --refresh ]] && ! jq -e '.requirePinnedLists == true' "$policy" >/dev/null; then
    jq '.gravityDirty=true' "$state" > "$state.next"; save_state
fi
# A failed/interrupted list rebuild stays dirty even after the last list is gone.
if jq -e '.gravityDirty == true' "$state" >/dev/null; then
    pihole -g || fail 'Gravity rebuild failed'
fi
jq --arg revision "$revision" '.revision=$revision | .gravityDirty=false' "$state" > "$state.next"
save_state
printf '%s\n' "$revision" > "$ready.next"
mv "$ready.next" "$ready"
echo "Policy revision $revision applied successfully"
