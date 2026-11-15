{{- define "pihole.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "pihole.fullname" -}}
  {{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
  {{- else }}
    {{- $name := default .Chart.Name .Values.nameOverride }}
    {{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
    {{- else }}
      {{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
    {{- end }}
  {{- end }}
{{- end }}

{{- define "pihole.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "pihole.commonSelectorLabels" . }}
  {{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
  {{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "pihole.commonSelectorLabels" -}}
app.kubernetes.io/name: {{ include "pihole.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "pihole.selectorLabels" -}}
{{ include "pihole.commonSelectorLabels" . }}
app.kubernetes.io/component: dns
{{- end }}

{{/* v2 preserves workload identity across every replica count. */}}
{{- define "pihole.isStatefulSet" -}}
true
{{- end }}

{{/* Returns the stable v2 workload kind */}}
{{- define "pihole.workloadKind" -}}
  {{- if eq (include "pihole.isStatefulSet" .) "true" -}}StatefulSet{{- else -}}Deployment{{- end -}}
{{- end }}

{{/* Selector labels for the standalone metrics exporter Deployment */}}
{{- define "pihole.metricsSelectorLabels" -}}
{{ include "pihole.commonSelectorLabels" . }}
app.kubernetes.io/component: metrics
{{- end }}

{{/* Comma-separated PIHOLE_HOSTNAME value for the metrics exporter.
One headless DNS entry per replica. */}}
{{- define "pihole.metricsHostnames" -}}
  {{- $clusterDomain := default "cluster.local" .Values.clusterDomain -}}
  {{- if eq (include "pihole.isStatefulSet" .) "true" -}}
    {{- $fullname := include "pihole.fullname" . -}}
    {{- $ns := .Release.Namespace -}}
    {{- $hostnames := list -}}
    {{- range $i := until (int .Values.replicas) -}}
      {{- $hostnames = append $hostnames (printf "%s-%d.%s-headless.%s.svc.%s" $fullname $i $fullname $ns $clusterDomain) -}}
    {{- end -}}
    {{- join "," $hostnames -}}
  {{- else -}}
    {{- printf "%s-web.%s.svc.%s" (include "pihole.fullname" .) .Release.Namespace $clusterDomain -}}
  {{- end -}}
{{- end }}

{{/* Comma-separated internal HTTP listener port for direct pod access. */}}
{{- define "pihole.metricsPorts" -}}
  {{- if eq (include "pihole.isStatefulSet" .) "true" -}}
    {{- $ports := list -}}
    {{- range until (int .Values.replicas) -}}
      {{- $ports = append $ports (toString $.Values.listeners.http) -}}
    {{- end -}}
    {{- join "," $ports -}}
  {{- else -}}
    {{- .Values.services.web.ports.http | toString -}}
  {{- end -}}
{{- end }}

{{- define "pihole.serviceAccountName" -}}
  {{- if .Values.serviceAccount.create -}}
{{- .Values.serviceAccount.name | default (include "pihole.fullname" .) -}}
  {{- else -}}
{{- .Values.serviceAccount.name | default "default" -}}
  {{- end -}}
{{- end }}

{{- define "pihole.passwordSecretName" -}}
{{- .Values.pihole.web.existingSecret | default (printf "%s-web-password" (include "pihole.fullname" .)) -}}
{{- end }}

{{/* Pass image values and a default tag explicitly; digest always wins. */}}
{{- define "pihole.image" -}}
  {{- printf "%s:%s" .image.repository (.image.tag | default .defaultTag) -}}
  {{- with .image.digest -}}@{{ . }}{{- end -}}
{{- end }}

{{- define "pihole.webListener" -}}
{{- .Values.listeners.http -}}
  {{- if gt (int .Values.listeners.https) 0 -}},{{ .Values.listeners.https }}s{{- end -}}
{{- end }}

{{- define "pihole.loadBalancerFields" -}}
  {{- if eq .type "LoadBalancer" -}}
    {{- with .loadBalancerClass }}
loadBalancerClass: {{ . | quote }}
    {{- end }}
    {{- with .loadBalancerSourceRanges }}
loadBalancerSourceRanges:
      {{- toYaml . | nindent 2 }}
    {{- end }}
  {{- end -}}
{{- end }}

{{/* Emit every non-null native leaf, preserving false, zero and configured-empty. */}}
{{- define "pihole.ftlLeaves" -}}
  {{- range $key, $value := .values -}}
    {{- if not (kindIs "invalid" $value) -}}
      {{- $path := printf "%s_%s" $.prefix $key -}}
      {{- if kindIs "map" $value -}}
      {{- include "pihole.ftlLeaves" (dict "prefix" $path "values" $value) -}}
      {{- else -}}
        {{- $rendered := $value -}}
        {{- if kindIs "slice" $value -}}{{- $rendered = join ";" $value -}}{{- end }}
- name: {{ $path }}
  value: {{ $rendered | toString | quote }}
      {{- end -}}
    {{- end -}}
  {{- end -}}
{{- end }}

{{- define "pihole.ftlEnvironment" -}}
  {{- $ftl := deepCopy .Values.pihole.ftl -}}
  {{- $_ := set $ftl.dns "port" .Values.listeners.dns -}}
  {{- $_ := set $ftl.webserver "port" (include "pihole.webListener" .) -}}
{{- include "pihole.ftlLeaves" (dict "prefix" "FTLCONF" "values" $ftl) -}}
{{- end }}

{{- define "pihole.dnsProbe" -}}
  {{- $root := .root -}}
exec:
  command:
    - /bin/sh
    - -ec
    - |
      if [ -n "$4" ]; then test "$(cat /run/pihole-helm-policy-ready 2>/dev/null)" = "$4"; fi
      response="$(dig +time=1 +tries=1 +norecurse +noall +comments +answer @127.0.0.1 -p "$1" "$2" A)"
      printf '%s\n' "$response" | grep -q 'status: NOERROR'
      printf '%s\n' "$response" | awk -v expected="$3" '$0 !~ /^;/ && $4 == "A" && (expected == "" || $5 == expected) { ok=1 } END { exit !ok }'
    - dns-probe
    - {{ $root.Values.listeners.dns | quote }}
    - {{ $root.Values.probes.dns.name | quote }}
    - {{ $root.Values.probes.dns.expectedAddress | quote }}
    - {{ if and .requirePolicy (eq (include "pihole.hasPolicy" $root) "true") }}{{ include "pihole.policyRevision" $root | quote }}{{ else }}""{{ end }}
{{- end }}

{{/* Comma-separated PIHOLE_PROTOCOL value — "http" repeated once per replica */}}
{{- define "pihole.metricsProtocols" -}}
  {{- if eq (include "pihole.isStatefulSet" .) "true" -}}
    {{- $protocols := list -}}
    {{- range until (int .Values.replicas) -}}
      {{- $protocols = append $protocols "http" -}}
    {{- end -}}
    {{- join "," $protocols -}}
  {{- else -}}
    {{- "http" -}}
  {{- end -}}
{{- end }}

{{- define "pihole.hasPolicy" -}}
  {{- if ne .Values.policy.mode "disabled" -}}true{{- end -}}
{{- end }}

{{- define "pihole.policyDocument" -}}
{{- dict "formatVersion" 1 "owner" (include "pihole.policyOwner" .) "mode" .Values.policy.mode "adoptExisting" .Values.policy.adoptExisting "requirePinnedLists" .Values.policy.requirePinnedLists "lists" .Values.pihole.lists "groups" .Values.pihole.groups "clients" .Values.pihole.clients | toJson -}}
{{- end }}

{{- define "pihole.policyOwner" -}}
{{- .Values.policy.owner | default (printf "%s/%s" .Release.Namespace .Release.Name) -}}
{{- end }}

{{- define "pihole.policyRevision" -}}
  {{- if .Values.policy.existingConfigMap -}}{{ .Values.policy.revision }}{{- else -}}{{ include "pihole.policyDocument" . | sha256sum }}{{- end -}}
{{- end }}

{{- define "pihole.policyConfigMapName" -}}
  {{- if .Values.policy.existingConfigMap -}}
{{ .Values.policy.existingConfigMap }}
  {{- else -}}
    {{- $content := printf "%s%s%s" (include "pihole.policyDocument" .) (.Files.Get "files/import-lists.sh") (.Files.Get "files/policy.jq") -}}
    {{ printf "%s-policy-%s" (include "pihole.fullname" . | trunc 35 | trimSuffix "-") ($content | sha256sum | trunc 20) }}
  {{- end -}}
{{- end }}

{{/* User data is supplied only through environment variables, never shell code. */}}
{{- define "pihole.checkEnvironment" -}}
  {{- $root := .root -}}
  {{- $dns := $root.Values.services.dns -}}
- name: HELM_CHECK_TARGETS
  value: {{ include "pihole.metricsHostnames" $root | quote }}
- name: HELM_CHECK_DNS_PORT
  value: {{ $root.Values.listeners.dns | quote }}
- name: HELM_CHECK_HTTP_PORT
  value: {{ $root.Values.listeners.http | quote }}
- name: HELM_CHECK_NAME
  value: {{ $root.Values.probes.dns.name | quote }}
- name: HELM_CHECK_ANSWER
  value: {{ $root.Values.probes.dns.expectedAddress | quote }}
- name: HELM_CHECK_BLOCKED
  value: {{ $root.Values.validation.blockedDomain | quote }}
- name: HELM_CHECK_BLOCKED_ANSWER
  value: {{ $root.Values.validation.blockedAnswer | quote }}
- name: HELM_CHECK_UPSTREAM
  value: {{ $root.Values.validation.upstreamDomain | quote }}
- name: HELM_CHECK_SERVICE_UDP
  value: {{ if and $dns.enabled (or (not $dns.splitTcpUdp) $dns.udp.enabled) }}{{ printf "%s-dns%s" (include "pihole.fullname" $root) (ternary "-udp" "" $dns.splitTcpUdp) | quote }}{{ else }}""{{ end }}
- name: HELM_CHECK_SERVICE_TCP
  value: {{ if and $dns.enabled (or (not $dns.splitTcpUdp) $dns.tcp.enabled) }}{{ printf "%s-dns%s" (include "pihole.fullname" $root) (ternary "-tcp" "" $dns.splitTcpUdp) | quote }}{{ else }}""{{ end }}
- name: HELM_CHECK_SERVICE_UDP_PORT
  value: {{ ternary $dns.udp.port $dns.ports.udp $dns.splitTcpUdp | quote }}
- name: HELM_CHECK_SERVICE_TCP_PORT
  value: {{ ternary $dns.tcp.port $dns.ports.tcp $dns.splitTcpUdp | quote }}
- name: HELM_CHECK_AUTH
  value: {{ .authentication | quote }}
- name: HELM_CHECK_METRICS
  value: {{ .metrics | quote }}
- name: HELM_CHECK_METRICS_HOST
  value: {{ printf "%s-metrics" (include "pihole.fullname" $root) | quote }}
- name: HELM_CHECK_METRICS_PORT
  value: {{ $root.Values.metrics.port | quote }}
- name: HELM_CHECK_WEB_HOST
  value: {{ if $root.Values.services.web.enabled }}{{ printf "%s-web" (include "pihole.fullname" $root) | quote }}{{ else }}""{{ end }}
- name: HELM_CHECK_WEB_PORT
  value: {{ $root.Values.services.web.ports.http | quote }}
{{- end }}
