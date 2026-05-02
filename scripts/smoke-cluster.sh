#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/observability-smoke.XXXXXX")"
KUBECTL="${KUBECTL:-kubectl}"
SMOKE_KUBECTL_MODE="${SMOKE_KUBECTL_MODE:-auto}"
SMOKE_SSH_HOST="${SMOKE_SSH_HOST:-}"
SMOKE_SSH_USER="${SMOKE_SSH_USER:-vagrant}"
SMOKE_SSH_PORT="${SMOKE_SSH_PORT:-}"
SMOKE_SSH_KEY="${SMOKE_SSH_KEY:-}"
SMOKE_SSH_STRICT_HOST_KEY_CHECKING="${SMOKE_SSH_STRICT_HOST_KEY_CHECKING:-false}"
SMOKE_REMOTE_KUBECTL="${SMOKE_REMOTE_KUBECTL:-sudo k3s kubectl}"
SMOKE_REMOTE_KUBECONFIG="${SMOKE_REMOTE_KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
SMOKE_TIMEOUT="${SMOKE_TIMEOUT:-300s}"
SMOKE_NODE_TIMEOUT="${SMOKE_NODE_TIMEOUT:-300s}"
SMOKE_POD_STRICT="${SMOKE_POD_STRICT:-true}"
SMOKE_ALLOW_UNHEALTHY_TARGETS="${SMOKE_ALLOW_UNHEALTHY_TARGETS:-false}"
SMOKE_CHECK_INGRESS="${SMOKE_CHECK_INGRESS:-true}"
SMOKE_MASTER_IP="${SMOKE_MASTER_IP:-}"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

log() {
  printf '\n==> %s\n' "$*"
}

pass() {
  printf 'PASS: %s\n' "$*"
}

warn() {
  printf 'WARN: %s\n' "$*"
}

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "$1 is required"
}

load_master_ip() {
  if [ -n "$SMOKE_MASTER_IP" ]; then
    return 0
  fi

  if [ -f "$ROOT_DIR/inventory/group_vars/all/main.yml" ]; then
    SMOKE_MASTER_IP="$(awk -F'"' '/^master_ip:/ { print $2; exit }' "$ROOT_DIR/inventory/group_vars/all/main.yml")"
  fi

  if [ -z "$SMOKE_MASTER_IP" ] && [ -f "$ROOT_DIR/inventory/inventory.ini" ]; then
    SMOKE_MASTER_IP="$(
      awk '
        $0 == "[control_plane]" { in_section = 1; next }
        /^\[/ { in_section = 0 }
        in_section && NF > 0 && $1 !~ /^#/ {
          for (i = 1; i <= NF; i++) {
            if ($i ~ /^ansible_host=/) {
              split($i, host, "=")
              print host[2]
              exit
            }
          }
          print $1
          exit
        }
      ' "$ROOT_DIR/inventory/inventory.ini"
    )"
  fi
}

kubectl_args=()
ssh_args=()
ssh_target=""
kubectl_mode=""

configure_kubectl() {
  case "$SMOKE_KUBECTL_MODE" in
    auto|direct|ssh)
      ;;
    *)
      fail "SMOKE_KUBECTL_MODE must be one of: auto, direct, ssh"
      ;;
  esac

  if [ "$SMOKE_KUBECTL_MODE" = "direct" ] \
    || { [ "$SMOKE_KUBECTL_MODE" = "auto" ] && command -v "$KUBECTL" >/dev/null 2>&1; }; then
    kubectl_mode="direct"
    if [ -n "${KUBECONFIG:-}" ]; then
      kubectl_args+=(--kubeconfig "$KUBECONFIG")
    elif [ -r /etc/rancher/k3s/k3s.yaml ]; then
      kubectl_args+=(--kubeconfig /etc/rancher/k3s/k3s.yaml)
    fi
    return 0
  fi

  require_command ssh
  load_master_ip
  SMOKE_SSH_HOST="${SMOKE_SSH_HOST:-$SMOKE_MASTER_IP}"

  if [ -z "$SMOKE_SSH_HOST" ]; then
    fail "kubectl is missing and no control-plane SSH host could be detected"
  fi

  kubectl_mode="ssh"
  ssh_args=(-o BatchMode=yes -o ConnectTimeout=10)
  if [ "$SMOKE_SSH_STRICT_HOST_KEY_CHECKING" != "true" ]; then
    ssh_args+=(
      -o StrictHostKeyChecking=no
      -o UserKnownHostsFile=/dev/null
      -o GlobalKnownHostsFile=/dev/null
    )
  fi
  if [ -n "$SMOKE_SSH_PORT" ]; then
    ssh_args+=(-p "$SMOKE_SSH_PORT")
  fi
  if [ -n "$SMOKE_SSH_KEY" ]; then
    ssh_args+=(-i "$SMOKE_SSH_KEY")
  fi

  if [[ "$SMOKE_SSH_HOST" == *@* ]] || [ -z "$SMOKE_SSH_USER" ]; then
    ssh_target="$SMOKE_SSH_HOST"
  else
    ssh_target="$SMOKE_SSH_USER@$SMOKE_SSH_HOST"
  fi

  printf 'INFO: local kubectl not found; using SSH kubectl via %s\n' "$ssh_target"
}

kube() {
  if [ "$kubectl_mode" = "ssh" ]; then
    local remote_command="$SMOKE_REMOTE_KUBECTL"

    if [ -n "$SMOKE_REMOTE_KUBECONFIG" ]; then
      remote_command+=" --kubeconfig $(printf '%q' "$SMOKE_REMOTE_KUBECONFIG")"
    fi

    local arg
    for arg in "$@"; do
      remote_command+=" $(printf '%q' "$arg")"
    done

    ssh "${ssh_args[@]}" "$ssh_target" "$remote_command"
    return
  fi

  "$KUBECTL" "${kubectl_args[@]}" "$@"
}

check_cluster_access() {
  log "Cluster access"
  kube version --client=true >/dev/null
  kube get nodes >/dev/null
  pass "kubectl can reach the cluster"
}

check_nodes_ready() {
  log "Node readiness"
  kube wait --for=condition=Ready node --all --timeout="$SMOKE_NODE_TIMEOUT"
  kube get nodes -o wide
  pass "all nodes are Ready"
}

check_pods_healthy() {
  log "Pod health"
  local pods_json="$TMP_DIR/pods.json"
  kube get pods -A -o json > "$pods_json"

  python3 - "$pods_json" "$SMOKE_POD_STRICT" <<'PY'
import json
import sys

path = sys.argv[1]
strict = sys.argv[2].lower() == "true"

with open(path, "r", encoding="utf-8") as fh:
    payload = json.load(fh)

bad = []
warnings = []

for item in payload.get("items", []):
    meta = item.get("metadata", {})
    status = item.get("status", {})
    ns = meta.get("namespace", "")
    name = meta.get("name", "")
    phase = status.get("phase", "")
    owner_refs = meta.get("ownerReferences", [])
    owner_kinds = {ref.get("kind", "") for ref in owner_refs}

    if phase == "Succeeded":
        continue

    if phase != "Running":
        bad.append(f"{ns}/{name}: phase={phase}")
        continue

    not_ready = [
        container.get("name", "")
        for container in status.get("containerStatuses", [])
        if not container.get("ready", False)
    ]
    if not_ready:
        target = bad if strict else warnings
        target.append(f"{ns}/{name}: containers not ready={','.join(not_ready)} owners={','.join(sorted(owner_kinds))}")

if warnings:
    print("Pod readiness warnings:")
    for item in warnings:
        print(f"  - {item}")

if bad:
    print("Unhealthy pods:")
    for item in bad:
        print(f"  - {item}")
    sys.exit(1)

print(f"Checked {len(payload.get('items', []))} pods")
PY
  pass "pods are healthy"
}

check_prometheus_rules() {
  log "PrometheusRule resources"

  if ! kube get crd prometheusrules.monitoring.coreos.com >/dev/null 2>&1; then
    warn "PrometheusRule CRD is not installed; monitoring phase may not be deployed yet"
    return 0
  fi

  local expected_rules=(
    observability-framework-platform-alerts
    observability-framework-database-alerts
    observability-framework-recording-rules
    observability-framework-slo-alerts
  )

  if kube get namespace ai-engine >/dev/null 2>&1; then
    expected_rules+=(observability-framework-ai-alerts)
  fi

  for rule in "${expected_rules[@]}"; do
    kube get prometheusrule "$rule" -n monitoring >/dev/null
  done

  kube get prometheusrule -n monitoring
  pass "framework PrometheusRules exist"
}

check_servicemonitors() {
  log "ServiceMonitor resources"

  if ! kube get crd servicemonitors.monitoring.coreos.com >/dev/null 2>&1; then
    warn "ServiceMonitor CRD is not installed; monitoring phase may not be deployed yet"
    return 0
  fi

  local count
  count="$(kube get servicemonitor -A --no-headers 2>/dev/null | wc -l | tr -d ' ')"
  if [ "${count:-0}" -lt 1 ]; then
    fail "no ServiceMonitor resources found"
  fi

  kube get servicemonitor -A
  pass "ServiceMonitor resources exist"
}

check_prometheus_targets() {
  log "Prometheus scrape targets"

  if ! kube get namespace monitoring >/dev/null 2>&1; then
    warn "monitoring namespace does not exist; skipping Prometheus target check"
    return 0
  fi

  local pod
  pod="$(kube get pod -n monitoring -l app.kubernetes.io/name=prometheus -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  if [ -z "$pod" ]; then
    warn "Prometheus pod not found; skipping target check"
    return 0
  fi

  local targets_json="$TMP_DIR/prometheus-targets.json"
  kube exec -n monitoring "$pod" -c prometheus -- wget -qO- http://localhost:9090/api/v1/targets > "$targets_json"

  python3 - "$targets_json" "$SMOKE_ALLOW_UNHEALTHY_TARGETS" <<'PY'
import json
import sys

path = sys.argv[1]
allow_unhealthy = sys.argv[2].lower() == "true"

with open(path, "r", encoding="utf-8") as fh:
    payload = json.load(fh)

targets = payload.get("data", {}).get("activeTargets", [])
unhealthy = [target for target in targets if target.get("health") != "up"]

print(f"Active targets: {len(targets)}")
print(f"Unhealthy targets: {len(unhealthy)}")

if not targets:
    sys.exit("Prometheus has zero active targets")

if unhealthy:
    for target in unhealthy[:20]:
        labels = target.get("labels", {})
        print(
            "  - "
            + " ".join(
                f"{key}={value}"
                for key, value in {
                    "job": labels.get("job"),
                    "namespace": labels.get("namespace"),
                    "pod": labels.get("pod"),
                    "instance": labels.get("instance"),
                    "health": target.get("health"),
                }.items()
                if value
            )
        )
    if not allow_unhealthy:
        sys.exit("Prometheus has unhealthy scrape targets")
PY
  pass "Prometheus targets look healthy"
}

check_grafana_dashboards() {
  log "Grafana dashboard ConfigMaps"

  if ! kube get namespace monitoring >/dev/null 2>&1; then
    warn "monitoring namespace does not exist; skipping dashboard check"
    return 0
  fi

  local dashboards_json="$TMP_DIR/dashboards.json"
  kube get configmap -A -l grafana_dashboard=1 -o json > "$dashboards_json"

  python3 - "$dashboards_json" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    payload = json.load(fh)

items = payload.get("items", [])
if not items:
    sys.exit("no Grafana dashboard ConfigMaps found")

for item in items:
    meta = item.get("metadata", {})
    data = item.get("data", {})
    print(f"{meta.get('namespace')}/{meta.get('name')}: {len(data)} dashboards")
PY
  pass "Grafana dashboard ConfigMaps exist"
}

check_database_backend() {
  log "Database backend status"

  if kube get crd mongodbcommunity.mongodbcommunity.mongodb.com >/dev/null 2>&1 \
    && kube get mongodbcommunity -A --no-headers >/dev/null 2>&1; then
    kube get mongodbcommunity -A
  fi

  if kube get crd clusters.postgresql.cnpg.io >/dev/null 2>&1 \
    && kube get cluster.postgresql.cnpg.io -A --no-headers >/dev/null 2>&1; then
    kube get cluster.postgresql.cnpg.io -A
  fi

  pass "database CR status queried"
}

check_ingress_http() {
  if [ "$SMOKE_CHECK_INGRESS" != "true" ]; then
    warn "SMOKE_CHECK_INGRESS=false; skipping HTTP ingress checks"
    return 0
  fi

  if ! command -v curl >/dev/null 2>&1; then
    warn "curl is not installed; skipping HTTP ingress checks"
    return 0
  fi

  load_master_ip
  if [ -z "$SMOKE_MASTER_IP" ]; then
    warn "SMOKE_MASTER_IP is not set and master_ip was not found; skipping HTTP ingress checks"
    return 0
  fi

  log "Ingress HTTP checks"
  local ingress_json="$TMP_DIR/ingress.json"
  kube get ingress -A -o json > "$ingress_json"

  python3 - "$ingress_json" > "$TMP_DIR/ingress-hosts.txt" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    payload = json.load(fh)

for item in payload.get("items", []):
    for rule in item.get("spec", {}).get("rules", []):
        host = rule.get("host")
        if host:
            print(host)
PY

  if [ ! -s "$TMP_DIR/ingress-hosts.txt" ]; then
    warn "no ingress hosts found"
    return 0
  fi

  while IFS= read -r host; do
    [ -n "$host" ] || continue
    local code
    code="$(curl -sk -o /dev/null -w "%{http_code}" -H "Host: $host" "http://$SMOKE_MASTER_IP/" || true)"
    case "$code" in
      200|204|301|302|307|308|401|403)
        printf '%s -> HTTP %s\n' "$host" "$code"
        ;;
      *)
        fail "$host returned unexpected HTTP $code through ingress"
        ;;
    esac
  done < "$TMP_DIR/ingress-hosts.txt"

  pass "ingress HTTP checks passed"
}

require_command python3
configure_kubectl

check_cluster_access
check_nodes_ready
check_pods_healthy
check_servicemonitors
check_prometheus_rules
check_prometheus_targets
check_grafana_dashboards
check_database_backend
check_ingress_http

printf '\nCluster smoke checks passed.\n'
