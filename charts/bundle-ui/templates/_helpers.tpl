{{- define "bundle-ui.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "bundle-ui.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "bundle-ui.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
app.kubernetes.io/name: {{ include "bundle-ui.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "bundle-ui.selectorLabels" -}}
app.kubernetes.io/name: {{ include "bundle-ui.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "bundle-ui.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}

{{- define "bundle-ui.secretName" -}}
{{- if .Values.secrets.create -}}
{{ include "bundle-ui.fullname" . }}
{{- else -}}
{{ required "secrets.existingSecret must name a Secret, or set secrets.create" .Values.secrets.existingSecret }}
{{- end -}}
{{- end -}}

{{- define "bundle-ui.postgresHost" -}}
{{ include "bundle-ui.fullname" . }}-postgresql
{{- end -}}

{{- define "bundle-ui.claimName" -}}
{{- .Values.postgresql.persistence.existingClaim | default (printf "%s-postgresql" (include "bundle-ui.fullname" .)) -}}
{{- end -}}

{{/* Environment shared by the web and sync containers */}}
{{- define "bundle-ui.appEnv" -}}
- name: DJANGO_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "bundle-ui.secretName" . }}
      key: DJANGO_SECRET_KEY
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "bundle-ui.secretName" . }}
      key: POSTGRES_PASSWORD
- name: ADMIN_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "bundle-ui.secretName" . }}
      key: ADMIN_PASSWORD
- name: BUNDLE_SERVER_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "bundle-ui.secretName" . }}
      key: BUNDLE_SERVER_TOKEN
{{- end -}}

{{/* Restricted security settings for the app containers, which run as uid 10001 */}}
{{- define "bundle-ui.containerSecurity" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  runAsGroup: 10001
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: ["ALL"]
  seccompProfile:
    type: RuntimeDefault
{{- end -}}
