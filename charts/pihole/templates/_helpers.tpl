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
{{ include "pihole.selectorLabels" . }}
  {{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
  {{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "pihole.selectorLabels" -}}
app.kubernetes.io/name: {{ include "pihole.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Returns "true" when replicas > 1, causing the workload to be rendered as a StatefulSet */}}
{{- define "pihole.isStatefulSet" -}}
  {{- if gt (int .Values.replicas) 1 -}}true{{- end -}}
{{- end }}

{{/* Returns the workload kind: StatefulSet when replicas > 1, Deployment otherwise */}}
{{- define "pihole.workloadKind" -}}
  {{- if eq (include "pihole.isStatefulSet" .) "true" -}}StatefulSet{{- else -}}Deployment{{- end -}}
{{- end }}

{{/* Selector labels for the standalone metrics exporter Deployment */}}
{{- define "pihole.metricsSelectorLabels" -}}
app.kubernetes.io/name: {{ include "pihole.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: metrics
{{- end }}

{{/* Comma-separated PIHOLE_HOSTNAME value for the metrics exporter.
StatefulSet: one headless DNS entry per replica.
Deployment:  the web ClusterIP service FQDN. */}}
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

{{/* Comma-separated PIHOLE_PORT value — container port (80) for StatefulSet pod-direct access;
web Service port for Deployment ClusterIP access. */}}
{{- define "pihole.metricsPorts" -}}
  {{- if eq (include "pihole.isStatefulSet" .) "true" -}}
    {{- $ports := list -}}
    {{- range until (int .Values.replicas) -}}
      {{- $ports = append $ports "80" -}}
    {{- end -}}
    {{- join "," $ports -}}
  {{- else -}}
    {{- .Values.pihole.web.service.ports.http | toString -}}
  {{- end -}}
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
