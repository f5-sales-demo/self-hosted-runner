#!/usr/bin/env bash
set -euo pipefail

[[ ${1:-} == validate || ${1:-} == install ]] || { echo "usage: $0 <validate|install>" >&2; exit 2; }
: "${EKS_CLUSTER_NAME:?EKS_CLUSTER_NAME is required}"
region=${AWS_REGION:-us-east-1}
[[ "$region" == us-east-1 ]] || { echo "AWS_REGION must be us-east-1" >&2; exit 1; }

cilium_version=1.18.2
metrics_chart_version=3.13.0
autoscaler_chart_version=9.59.0
autoscaler_repository=registry.k8s.io/autoscaling/cluster-autoscaler
autoscaler_tag=v1.35.0

helm repo add cilium https://helm.cilium.io/ --force-update >/dev/null
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ --force-update >/dev/null
helm repo add autoscaler https://kubernetes.github.io/autoscaler --force-update >/dev/null
helm repo update >/dev/null

common=(--namespace kube-system)
cilium_args=(
  --version "$cilium_version"
  --set cni.chainingMode=aws-cni
  --set cni.exclusive=false
  --set routingMode=native
  --set enableIPv4Masquerade=false
  --set operator.replicas=2
  --set operator.nodeSelector.runner-profile=system
  --set operator.tolerations[0].key=CriticalAddonsOnly
  --set operator.tolerations[0].operator=Exists
  --set operator.tolerations[0].effect=NoSchedule
)
metrics_args=(
  --version "$metrics_chart_version"
  --set nodeSelector.runner-profile=system
  --set tolerations[0].key=CriticalAddonsOnly
  --set tolerations[0].operator=Exists
  --set tolerations[0].effect=NoSchedule
  --set priorityClassName=system-cluster-critical
)
autoscaler_args=(
  --version "$autoscaler_chart_version"
  --set-string autoDiscovery.clusterName="$EKS_CLUSTER_NAME"
  --set awsRegion="$region"
  --set rbac.serviceAccount.name=cluster-autoscaler
  --set rbac.serviceAccount.create=true
  --set-string image.repository="$autoscaler_repository"
  --set-string image.tag="$autoscaler_tag"
  --set nodeSelector.runner-profile=system
  --set tolerations[0].key=CriticalAddonsOnly
  --set tolerations[0].operator=Exists
  --set tolerations[0].effect=NoSchedule
  --set priorityClassName=system-cluster-critical
  --set-string extraArgs.expendable-pods-priority-cutoff=-1001
  --set extraArgs.scan-interval=10s
  --set extraArgs.scale-down-unneeded-time=60m
)

autoscaler_manifest=$(helm template cluster-autoscaler autoscaler/cluster-autoscaler "${common[@]}" "${autoscaler_args[@]}")
for resource in resourceclaims resourceslices deviceclasses; do
  grep -Eq "^[[:space:]]+- $resource$" <<<"$autoscaler_manifest" || {
    echo "Cluster Autoscaler chart RBAC is missing Kubernetes 1.35 resource: $resource" >&2
    exit 1
  }
done

if [[ $1 == validate ]]; then
  helm template cilium cilium/cilium "${common[@]}" "${cilium_args[@]}" >/dev/null
  helm template metrics-server metrics-server/metrics-server "${common[@]}" "${metrics_args[@]}" >/dev/null
  echo "pinned Cilium, Metrics Server, and Cluster Autoscaler charts render successfully"
  exit 0
fi

: "${KUBECONFIG:?KUBECONFIG must point to the protected EKS config}"
: "${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID must identify the target account}"
case "$(stat -c '%a' "$KUBECONFIG")" in 400|600) ;; *) echo "KUBECONFIG must have mode 0400 or 0600" >&2; exit 1;; esac
[[ "$(aws sts get-caller-identity --query Account --output text)" == "$AWS_ACCOUNT_ID" ]]
[[ "$(kubectl config current-context)" == "$EKS_CLUSTER_NAME" ]]

helm upgrade --install cilium cilium/cilium "${common[@]}" "${cilium_args[@]}" --wait --timeout 15m
helm upgrade --install metrics-server metrics-server/metrics-server "${common[@]}" "${metrics_args[@]}" --wait --timeout 10m
helm upgrade --install cluster-autoscaler autoscaler/cluster-autoscaler "${common[@]}" "${autoscaler_args[@]}" --wait --timeout 10m
kubectl -n kube-system rollout status daemonset/cilium --timeout=15m
kubectl -n kube-system rollout status deployment/metrics-server --timeout=10m
kubectl -n kube-system rollout status deployment/cluster-autoscaler-aws-cluster-autoscaler --timeout=10m
