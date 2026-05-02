#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/observability-validate.XXXXXX")"
FAILED=0

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
  FAILED=1
}

run_check() {
  local label="$1"
  shift
  log "$label"
  if "$@"; then
    pass "$label"
  else
    fail "$label"
  fi
}

require_command() {
  command -v "$1" >/dev/null 2>&1
}

write_dummy_vars() {
  cat > "$TMP_DIR/dummy-vars.yml" <<'YAML'
---
vault_k3s_token: "validate-k3s-token"
vault_opensearch_admin_password: "ValidateAdminPassword123!"
vault_grafana_admin_password: "ValidateGrafanaPassword123!"
vault_database_superuser_password: "ValidateDatabaseSuperuser123!"
vault_database_app_password: "ValidateDatabaseApp123!"
vault_forgejo_admin_password: "ValidateForgejoAdmin123!"
vault_wekan_admin_password: "ValidateWeKanAdmin123!"
vault_alertmanager_smtp_username: "validate-smtp-user"
vault_alertmanager_smtp_password: "validate-smtp-password"
vault_alertmanager_telegram_bot_token: "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ"
vault_alertmanager_telegram_chat_id: "-1001234567890"
vault_alertmanager_slack_api_url: "https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXXXXXX"
vault_alertmanager_webex_bot_token: "validate-webex-token-with-enough-length"
vault_alertmanager_pagerduty_routing_key: "validate-pagerduty-routing-key"
ai_engine_use_built_image: false
YAML

  cat > "$TMP_DIR/postgres-vars.yml" <<'YAML'
---
database:
  type: "postgresql"
  availability_goal: "quorum_ha"
  namespace: "database"
  pool: "database"
  force_recreate: false
  storage_size: "2Gi"
  app_db_name: "app"
  app_user: "appuser"
  app_password: "ValidateDatabaseApp123!"
  scheduling:
    spread_policy: "required"
    topology_key: "kubernetes.io/hostname"
  cluster_name: "app-db-cluster"
  superuser_password: "ValidateDatabaseSuperuser123!"
  port: 5432
  host: "app-db-cluster-rw.database.svc.cluster.local"
  postgresql:
    instances: 2
YAML
}

validate_yaml_files() {
  if ! require_command ruby; then
    warn "ruby is not installed; skipping YAML parser check"
    return 0
  fi

  local yaml_files
  yaml_files="$(find "$ROOT_DIR" \
    -path "$ROOT_DIR/.git" -prune -o \
    -type f \( -name '*.yml' -o -name '*.yaml' \) \
    ! -name '*.j2' \
    ! -path "$ROOT_DIR/roles/wekan/chart/templates/*" \
    -print)"

  if [ -z "$yaml_files" ]; then
    warn "no YAML files found"
    return 0
  fi

  while IFS= read -r file; do
    if ! ruby -e 'require "yaml"; YAML.load_stream(File.read(ARGV[0]))' "$file"; then
      printf 'Invalid YAML: %s\n' "$file" >&2
      return 1
    fi
  done <<EOF
$yaml_files
EOF
}

validate_json_files() {
  if ! require_command python3; then
    warn "python3 is not installed; skipping JSON parser check"
    return 0
  fi

  local json_files
  json_files="$(find "$ROOT_DIR" -type f -name '*.json' -print)"

  if [ -z "$json_files" ]; then
    warn "no JSON files found"
    return 0
  fi

  while IFS= read -r file; do
    python3 - "$file" <<'PY' || return 1
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    json.load(fh)
PY
  done <<EOF
$json_files
EOF
}

render_template() {
  local src="$1"
  local dest="$2"
  local role_path_value="$3"
  shift 3

  ANSIBLE_LOCAL_TEMP="$TMP_DIR/ansible-local" \
  ANSIBLE_REMOTE_TEMP="$TMP_DIR/ansible-remote" \
  ansible localhost \
    -i localhost, \
    -c local \
    -m template \
    -a "src=$src dest=$dest" \
    -e "@$ROOT_DIR/inventory/group_vars/all/main.yml" \
    -e "@$TMP_DIR/dummy-vars.yml" \
    -e "role_path=$role_path_value" \
    "$@" >/dev/null
}

validate_rendered_yaml() {
  if ! require_command ruby; then
    warn "ruby is not installed; skipping rendered YAML parser check"
    return 0
  fi

  while IFS= read -r file; do
    ruby -e 'require "yaml"; YAML.load_stream(File.read(ARGV[0]))' "$file" || return 1
  done <<EOF
$(find "$TMP_DIR/rendered" -type f \( -name '*.yml' -o -name '*.yaml' \) -print)
EOF
}

validate_jinja_templates() {
  if ! require_command ansible; then
    warn "ansible is not installed; skipping Jinja render checks"
    return 0
  fi

  write_dummy_vars
  mkdir -p "$TMP_DIR/rendered"

  render_template "$ROOT_DIR/roles/monitoring/templates/alertmanager-config.yaml.j2" "$TMP_DIR/rendered/alertmanager.yaml" "$ROOT_DIR/roles/monitoring"
  render_template "$ROOT_DIR/roles/monitoring/templates/prometheusrule-platform.yaml.j2" "$TMP_DIR/rendered/prometheusrule-platform.yaml" "$ROOT_DIR/roles/monitoring"
  render_template "$ROOT_DIR/roles/monitoring/templates/prometheusrule-database.yaml.j2" "$TMP_DIR/rendered/prometheusrule-database-mongodb.yaml" "$ROOT_DIR/roles/monitoring"
  render_template "$ROOT_DIR/roles/monitoring/templates/prometheusrule-recording.yaml.j2" "$TMP_DIR/rendered/prometheusrule-recording-mongodb.yaml" "$ROOT_DIR/roles/monitoring"
  render_template "$ROOT_DIR/roles/monitoring/templates/prometheusrule-slo.yaml.j2" "$TMP_DIR/rendered/prometheusrule-slo.yaml" "$ROOT_DIR/roles/monitoring"
  render_template "$ROOT_DIR/roles/monitoring/templates/grafana-community-dashboards-configmap.yaml.j2" "$TMP_DIR/rendered/grafana-community-dashboards.yaml" "$ROOT_DIR/roles/monitoring"
  render_template "$ROOT_DIR/roles/monitoring/templates/grafana-platform-dashboards-configmap.yaml.j2" "$TMP_DIR/rendered/grafana-platform-dashboards.yaml" "$ROOT_DIR/roles/monitoring"

  render_template "$ROOT_DIR/roles/monitoring/templates/prometheusrule-database.yaml.j2" "$TMP_DIR/rendered/prometheusrule-database-postgresql.yaml" "$ROOT_DIR/roles/monitoring" -e "@$TMP_DIR/postgres-vars.yml"
  render_template "$ROOT_DIR/roles/monitoring/templates/prometheusrule-recording.yaml.j2" "$TMP_DIR/rendered/prometheusrule-recording-postgresql.yaml" "$ROOT_DIR/roles/monitoring" -e "@$TMP_DIR/postgres-vars.yml"

  render_template "$ROOT_DIR/roles/ai_engine/templates/deployment.yaml.j2" "$TMP_DIR/rendered/ai-deployment.yaml" "$ROOT_DIR/roles/ai_engine"
  render_template "$ROOT_DIR/roles/ai_engine/templates/servicemonitor.yaml.j2" "$TMP_DIR/rendered/ai-servicemonitor.yaml" "$ROOT_DIR/roles/ai_engine"
  render_template "$ROOT_DIR/roles/ai_engine/templates/prometheusrule-ai.yaml.j2" "$TMP_DIR/rendered/prometheusrule-ai.yaml" "$ROOT_DIR/roles/ai_engine"
  render_template "$ROOT_DIR/roles/ai_engine/templates/grafana-dashboard-configmap.yaml.j2" "$TMP_DIR/rendered/ai-dashboard.yaml" "$ROOT_DIR/roles/ai_engine"

  render_template "$ROOT_DIR/roles/database/templates/postgres-cluster.yaml.j2" "$TMP_DIR/rendered/postgres-cluster.yaml" "$ROOT_DIR/roles/database" -e "@$TMP_DIR/postgres-vars.yml"
  render_template "$ROOT_DIR/roles/database/templates/mongodb-community-required.yaml.j2" "$TMP_DIR/rendered/mongodb-community-required.yaml" "$ROOT_DIR/roles/database"
  render_template "$ROOT_DIR/roles/database/templates/mongodb-community-preferred.yaml.j2" "$TMP_DIR/rendered/mongodb-community-preferred.yaml" "$ROOT_DIR/roles/database"
  render_template "$ROOT_DIR/roles/database/templates/mongodb-community-none.yaml.j2" "$TMP_DIR/rendered/mongodb-community-none.yaml" "$ROOT_DIR/roles/database"
  render_template "$ROOT_DIR/roles/database/templates/mongodb-servicemonitor.yaml.j2" "$TMP_DIR/rendered/mongodb-servicemonitor.yaml" "$ROOT_DIR/roles/database"

  render_template "$ROOT_DIR/roles/ingress/templates/grafana-ingress.yaml.j2" "$TMP_DIR/rendered/grafana-ingress.yaml" "$ROOT_DIR/roles/ingress"
  render_template "$ROOT_DIR/roles/ingress/templates/prometheus-ingress.yaml.j2" "$TMP_DIR/rendered/prometheus-ingress.yaml" "$ROOT_DIR/roles/ingress"
  render_template "$ROOT_DIR/roles/ingress/templates/opensearch-ingress.yaml.j2" "$TMP_DIR/rendered/opensearch-ingress.yaml" "$ROOT_DIR/roles/ingress"

  validate_rendered_yaml
}

validate_shell_syntax() {
  local shell_files
  shell_files="$(find "$ROOT_DIR/scripts" -type f -name '*.sh' -print 2>/dev/null || true)"

  if [ -z "$shell_files" ]; then
    warn "no shell scripts found"
    return 0
  fi

  while IFS= read -r file; do
    bash -n "$file" || return 1
  done <<EOF
$shell_files
EOF
}

validate_helm_chart() {
  if ! require_command helm; then
    warn "helm is not installed; skipping WeKan chart lint"
    return 0
  fi

  helm lint "$ROOT_DIR/roles/wekan/chart"
}

validate_ansible_syntax() {
  if ! require_command ansible-playbook; then
    warn "ansible-playbook is not installed; skipping Ansible syntax checks"
    return 0
  fi

  ANSIBLE_LOCAL_TEMP="$TMP_DIR/ansible-local" \
  ANSIBLE_REMOTE_TEMP="$TMP_DIR/ansible-remote" \
  ansible-playbook -i "$ROOT_DIR/inventory/inventory.ini" "$ROOT_DIR/cleanup.yml" --syntax-check || return 1

  if ANSIBLE_LOCAL_TEMP="$TMP_DIR/ansible-local" ANSIBLE_REMOTE_TEMP="$TMP_DIR/ansible-remote" ansible-doc -t module kubernetes.core.helm >/dev/null 2>&1 \
    && ANSIBLE_LOCAL_TEMP="$TMP_DIR/ansible-local" ANSIBLE_REMOTE_TEMP="$TMP_DIR/ansible-remote" ansible-doc -t module kubernetes.core.k8s >/dev/null 2>&1 \
    && ANSIBLE_LOCAL_TEMP="$TMP_DIR/ansible-local" ANSIBLE_REMOTE_TEMP="$TMP_DIR/ansible-remote" ansible-doc -t module kubernetes.core.k8s_info >/dev/null 2>&1; then
    ANSIBLE_LOCAL_TEMP="$TMP_DIR/ansible-local" \
    ANSIBLE_REMOTE_TEMP="$TMP_DIR/ansible-remote" \
    ansible-playbook -i "$ROOT_DIR/inventory/inventory.ini" "$ROOT_DIR/site.yml" --syntax-check
  else
    warn "kubernetes.core collection is missing; skipping site.yml syntax check"
    return 0
  fi
}

validate_ansible_lint() {
  if ! require_command ansible-lint; then
    warn "ansible-lint is not installed; skipping lint"
    return 0
  fi

  ansible-lint "$ROOT_DIR/site.yml" "$ROOT_DIR/cleanup.yml"
}

cd "$ROOT_DIR" || exit 1

run_check "YAML syntax" validate_yaml_files
run_check "JSON dashboards and templates" validate_json_files
run_check "Shell script syntax" validate_shell_syntax
run_check "Jinja template render smoke tests" validate_jinja_templates
run_check "WeKan Helm chart lint" validate_helm_chart
run_check "Ansible syntax checks" validate_ansible_syntax
run_check "ansible-lint when available" validate_ansible_lint

if [ "$FAILED" -ne 0 ]; then
  printf '\nValidation failed.\n' >&2
  exit 1
fi

printf '\nAll local validation checks passed.\n'
