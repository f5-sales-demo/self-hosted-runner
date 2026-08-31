{{- define "renovate.config" -}}{{ .Files.Get "generated/renovate.json" }}{{- end -}}
{{- define "renovate.configName" -}}renovate-config-{{ include "renovate.config" . | sha256sum | trunc 16 }}{{- end -}}
{{- define "renovate.appName" -}}renovate-app-{{ dict "appId" .Values.githubApp.appId "installationId" .Values.githubApp.installationId "botId" .Values.githubApp.botId "botLogin" .Values.githubApp.botLogin | toJson | sha256sum | trunc 16 }}{{- end -}}
