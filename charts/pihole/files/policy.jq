# Normalize and validate the complete input before the first API mutation.
def text: type == "string" and length > 0 and (test("[\u0000-\u001f]") | not);
def names: type == "array" and all(.[]; text) and length == (unique | length);
def valid_item:
  (.key | text) and (.body.comment | type == "string") and
  (.kind == "clients" or (.body.enabled | type == "boolean")) and
  (.groups == null or (.groups | names));
def entry($kind; $key; $body; $groups): {kind:$kind,key:$key,body:$body,groups:$groups};
if .formatVersion != 1 or (.owner | text | not) or
   (.requirePinnedLists != null and (.requirePinnedLists | type != "boolean")) or
   (.adoptExisting | type != "boolean") or (.mode != "reconcile" and .mode != "additive") or
   (.groups | type != "array") or (.clients | type != "array") or (.lists | type != "object") or
   ([.lists | keys[]] - ["adlists","whitelist","blacklist","regex_whitelist","regex_blacklist"] | length > 0) or
   any(.lists[]; type != "array") then error("Invalid policy document") else . end |
. as $p |
[
  ($p.groups[] | entry("groups"; .name; {name,comment:(.description // .comment // ""),enabled:(if has("enabled") then .enabled else true end)}; null)),
  ($p.clients[] | entry("clients"; .id; {client:.id,comment:(.comment // "")}; (.groups // ["Default"]))),
  ($p.lists.adlists[] | if (.type // "block") as $t | $t != "block" and $t != "allow" then error("Invalid adlist type") else . end |
    if has("sha256") and ((.sha256|type!="string") or (.sha256|test("^[a-f0-9]{64}$")|not) or (.address|test("^https://[^[:space:]]+$")|not)) then error("Pinned adlists require HTTPS and a lowercase SHA-256") else . end |
    if $p.requirePinnedLists == true and (if has("enabled") then .enabled else true end) and (has("sha256")|not) then error("Enabled adlists must be SHA-256 pinned") else . end |
    . as $list | (if has("sha256") then "file:///etc/pihole/helm-snapshots/"+.sha256+".txt" else .address end) as $address |
    entry("lists/" + (.type // "block"); $address; {address:$address,comment:(.comment // ""),enabled:(if has("enabled") then .enabled else true end)}; (.groups // ["Default"])) |
    if $list.sha256 then .snapshot={url:$list.address,sha256:$list.sha256} else . end),
  ([{key:"whitelist",kind:"domains/allow/exact"},{key:"blacklist",kind:"domains/deny/exact"},
    {key:"regex_whitelist",kind:"domains/allow/regex"},{key:"regex_blacklist",kind:"domains/deny/regex"}][] as $list |
    $p.lists[$list.key][] | .domain as $domain |
    if ($list.kind | endswith("exact")) and ($domain | test("^[a-zA-Z0-9_.-]+$") | not) then
      error("Exact domains must use ASCII/Punycode DNS names") else . end |
    (if ($list.kind | endswith("exact")) then $domain | ascii_downcase else $domain end) as $key |
    entry($list.kind; $key; {domain:$key,comment:(.comment // ""),enabled:(if has("enabled") then .enabled else true end)}; (.groups // ["Default"])))
] |
if (all(.[]; valid_item) | not) or
   (map([.kind,.key]) | length != (unique | length)) or
   any(.[]; .kind == "groups" and .key == "Default") then error("Invalid, duplicate or protected policy entry") else . end
