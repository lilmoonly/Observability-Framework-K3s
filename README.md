# AI-Powered K3s Observability Framework

This repository provisions a reusable K3s observability framework with Ansible and Vagrant. The default lab profile is an 8-node reference environment with dedicated HA-ready app and database pools, while worker placement stays pool-based so the same roles can be reused across larger topologies without rewriting scheduling logic.

The framework is opinionated about platform observability, but application roles can stay lightweight. Forgejo is included as an example workload, not as the core purpose of the project.

## What This Framework Deploys

- A pool-based K3s topology for general, database, logging, monitoring, and AI workloads
- CloudNativePG for PostgreSQL
- OpenSearch and OpenSearch Dashboards for logs and AI anomaly documents
- Fluent Bit for cluster-wide log shipping
- kube-prometheus-stack with Prometheus, Alertmanager, Grafana, kube-state-metrics, and node-exporter
- Traefik ingress with Prometheus metrics enabled
- A custom AI anomaly detector that reads Prometheus and writes anomaly events to OpenSearch
- Vendored Grafana dashboards that work without internet access

## Reference Lab Topology

| Node | Hostname | IP | Purpose |
| --- | --- | --- | --- |
| 1 | `k8s-ctrl` | `192.168.56.10` | K3s control plane and Ansible control target |
| 2 | `app-node-1` | `192.168.56.11` | `general` worker pool |
| 3 | `app-node-2` | `192.168.56.12` | `general` worker pool |
| 4 | `db-node-1` | `192.168.56.13` | `database` worker pool |
| 5 | `db-node-2` | `192.168.56.14` | `database` worker pool |
| 6 | `logging-node` | `192.168.56.15` | `logging` worker pool |
| 7 | `monitor-node` | `192.168.56.16` | `monitoring` worker pool |
| 8 | `ai-node` | `192.168.56.17` | `ai` worker pool |

## Topology Model

The framework now uses named worker pools instead of hardcoded node identities.

- Inventory groups follow the pattern `pool_<name>`, for example `pool_general` or `pool_logging`
- Each host declares `node_pool=<name>`
- Shared pool definitions live in [inventory/group_vars/all/main.yml](inventory/group_vars/all/main.yml) under `cluster_topology.node_pools`
- Workloads target pools like `forgejo.pool`, `database.pool`, `monitoring.pool`, `opensearch.pool`, and `ai_engine.pool`
- Today the control plane is still single-node, but worker pools can now scale horizontally by adding more hosts to the corresponding `pool_*` groups
- Smaller topologies can share infrastructure by pointing multiple workload settings at the same pool, for example `monitoring.pool: general` and `ai_engine.pool: general`
- The default lab now uses two `general` nodes and two `database` nodes so application workloads and CloudNativePG have room for node-level HA without scaling monitoring, logging, or AI yet

## Stack

- K3s
- Ansible
- Vagrant + VirtualBox
- CloudNativePG
- OpenSearch
- OpenSearch Dashboards
- Fluent Bit
- Prometheus
- Grafana
- Alertmanager
- Traefik
- Python + PyOD

## Repository Layout

- [site.yml](site.yml) - master playbook with phase tags
- [inventory/inventory.ini](inventory/inventory.ini) - node inventory
- [inventory/examples/lab.inventory.ini](inventory/examples/lab.inventory.ini) - reference 8-node pool-based inventory
- [inventory/examples/scaled.inventory.ini](inventory/examples/scaled.inventory.ini) - larger worker-pool example
- [inventory/group_vars/all/main.yml](inventory/group_vars/all/main.yml) - framework-wide non-secret configuration
- [inventory/group_vars/all/secrets.yml.example](inventory/group_vars/all/secrets.yml.example) - example secrets file for vault-managed values
- [roles/common](roles/common) - base OS and tooling setup
- [roles/k3s_master](roles/k3s_master) - control plane bootstrap and Traefik metrics
- [roles/k3s_worker](roles/k3s_worker) - worker join, taints, and labels
- [roles/database](roles/database) - CloudNativePG operator and PostgreSQL cluster
- [roles/logging](roles/logging) - OpenSearch, OpenSearch Dashboards, Fluent Bit, OpenSearch exporter
- [roles/monitoring](roles/monitoring) - kube-prometheus-stack and vendored dashboards
- [roles/ai_engine](roles/ai_engine) - anomaly detector service, metrics, and AI dashboard
- [roles/ingress](roles/ingress) - Grafana and OpenSearch ingress
- [roles/forgejo](roles/forgejo) - example app role with metrics scraping

## Deployment Phases

The full deployment is split into nine tagged phases in [site.yml](site.yml).

| Phase | Tag | Role | Purpose |
| --- | --- | --- | --- |
| 1 | `phase1`, `common` | `common` | Base packages, kernel settings, Helm, networking fixes |
| 2 | `phase2`, `k3s_master` | `k3s_master` | K3s control plane and Traefik metrics |
| 3 | `phase3`, `k3s_worker` | `k3s_worker` | Worker join, node labels, taints |
| 4 | `phase4`, `database` | `database` | CloudNativePG operator and PostgreSQL cluster |
| 5 | `phase5`, `logging` | `logging` | OpenSearch, OpenSearch Dashboards, Fluent Bit, exporter |
| 6 | `phase6`, `monitoring` | `monitoring` | Prometheus, Grafana, Alertmanager, vendored dashboards |
| 7 | `phase7`, `ai_engine` | `ai_engine` | AI anomaly detector, ServiceMonitor, AI dashboard |
| 8 | `phase8`, `ingress` | `ingress` | Grafana and OpenSearch ingress |
| 9 | `phase9`, `forgejo` | `forgejo` | Example app deployment and ingress |

## Quick Start

### Prerequisites

- Vagrant
- VirtualBox
- Ansible on the host machine
- Docker on the host machine when `ai_engine.build.enabled=true`
- Enough local resources to run 8 Ubuntu VMs

### 1. Start the VMs

```bash
vagrant up
```

### 2. Review Configuration

Adjust the framework settings in [inventory/group_vars/all/main.yml](inventory/group_vars/all/main.yml) and choose an inventory layout that matches your topology.

Important settings live under:

- `artifact_versions`
- `cluster_topology`
- `opensearch`
- `monitoring`
- `database`
- `ai_engine`
- `ingress`
- `forgejo`

The default inventory is [inventory/inventory.ini](inventory/inventory.ini). You can also copy one of the pool-based examples from [inventory/examples/lab.inventory.ini](inventory/examples/lab.inventory.ini) or [inventory/examples/scaled.inventory.ini](inventory/examples/scaled.inventory.ini) and adapt it to your environment.

### 2a. Deterministic Artifact Pins

Phase 3 packaging work now centralizes the main version pins in [inventory/group_vars/all/main.yml](inventory/group_vars/all/main.yml) under `artifact_versions`.

This currently pins:

- Helm CLI version
- Python Kubernetes client version used by Ansible hosts
- Helm chart versions for OpenSearch, OpenSearch Dashboards, Fluent Bit, kube-prometheus-stack, CloudNativePG, and Forgejo
- The K3s binary version via `k3s_version`

This means reruns no longer drift just because an upstream chart repository published a newer release between installs.

### 2b. Create the Secrets File

Copy the example secrets file and encrypt it before the first deploy:

```bash
cp inventory/group_vars/all/secrets.yml.example inventory/group_vars/all/secrets.yml
ansible-vault encrypt inventory/group_vars/all/secrets.yml
```

This file is intentionally ignored by git and should hold:

- K3s cluster join token
- OpenSearch admin password
- Grafana admin password
- PostgreSQL passwords
- Forgejo admin password

The playbook now fails early if this file still contains placeholders or the old demo credentials.

### 3. Deploy Everything

From the repository root:

```bash
ansible-playbook site.yml --ask-vault-pass
```

You can also run explicitly with the inventory file:

```bash
ansible-playbook -i inventory/inventory.ini site.yml --ask-vault-pass
```

## Rerun From a Specific Phase

The project supports phase-by-phase reruns.

Examples:

```bash
ansible-playbook site.yml --tags monitoring
ansible-playbook site.yml --tags ai_engine
ansible-playbook site.yml --tags phase5,phase6
ansible-playbook site.yml --tags k3s_master,database,logging,monitoring,forgejo
```

## Access URLs

Add these entries to your host machine `/etc/hosts`:

```text
192.168.56.10  forgejo.local
192.168.56.10  grafana.local
192.168.56.10  opensearch.local
```

Then use:

- Grafana: [http://grafana.local](http://grafana.local)
- OpenSearch Dashboards: [http://opensearch.local](http://opensearch.local)
- Forgejo: [http://forgejo.local](http://forgejo.local)

## Observability Coverage

### Logging

- Fluent Bit runs as a DaemonSet and ships container logs to OpenSearch
- OpenSearch stores operational logs and AI anomaly documents
- OpenSearch Dashboards provides a UI for search and exploration

### Metrics

Prometheus scrapes:

- Kubernetes core components from kube-prometheus-stack
- node-exporter
- kube-state-metrics
- AI engine metrics
- Traefik metrics
- CloudNativePG metrics via PodMonitor
- Fluent Bit metrics
- OpenSearch exporter metrics
- Forgejo metrics

### Grafana Dashboards

Grafana is configured with vendored dashboard JSON files, so production deployments do not require outbound internet access.

Included dashboard groups:

- `Infrastructure`
- `Platform Services`
- `AI Anomaly Overview`

Infrastructure dashboards:

- Kubernetes / Views / Global
- Kubernetes / Views / Nodes
- Kubernetes / Views / Pods
- Kubernetes / Views / Namespaces
- Kubernetes / System / API Server
- Kubernetes / System / CoreDNS
- Prometheus

Platform Services dashboards:

- CloudNativePG
- Traefik Official Kubernetes Dashboard
- Fluent Bit
- OpenSearch Exporter Overview

Application dashboards:

- A custom `AI Anomaly Overview` dashboard is provisioned by the AI role
- Forgejo metrics are scraped, but no bundled Forgejo dashboard is shipped because Forgejo is only an example workload in this framework

## AI Engine

The AI engine is a Python service deployed on the configured AI pool. It is configured in [inventory/group_vars/all/main.yml](inventory/group_vars/all/main.yml) and implemented in [main.py](roles/ai_engine/files/app/main.py).

Current packaging note:

- the framework now builds a pinned `ai-engine` image locally from [roles/ai_engine/files/app](roles/ai_engine/files/app), exports it, and imports it into the AI pool before deployment
- if you already publish that image from CI, you can disable the local build path with `ai_engine.build.enabled: false` and point `ai_engine.image` at your registry artifact instead
- the remaining Phase 3 gap is making this image flow fully offline-friendly through a mirrored or preloaded artifact source

What it does:

- Queries Prometheus on a schedule
- Builds a multivariate feature vector from recent cluster, namespace, pod, ingress, and control-plane metrics
- Computes a rolling median baseline and residuals so gradual growth is less likely to be treated as an incident
- Trains a PyOD Isolation Forest model on recent residual history
- Combines model output with rule-based checks for obvious incidents
- Scores the newest time window against that learned baseline
- Exposes its own Prometheus metrics for Grafana
- Assigns anomaly severity and suppresses duplicate events during noisy periods
- Writes confirmed anomaly events to OpenSearch

Current features:

- `cluster_cpu_usage_pct`
- `cluster_memory_usage_pct`
- `cluster_disk_usage_pct`
- `cluster_network_receive_bytes_per_sec`
- `cluster_network_transmit_bytes_per_sec`
- `running_pods`
- `pending_pods`
- `failed_pods`
- `pod_restart_delta_15m`
- `node_not_ready_count`
- `node_memory_pressure_count`
- `node_disk_pressure_count`
- `node_pid_pressure_count`
- `apiserver_5xx_rate`
- `apiserver_p99_latency_seconds`
- `traefik_request_rate`
- `traefik_5xx_rate`
- `traefik_p95_latency_seconds`
- `max_namespace_cpu_usage_cores`
- `max_namespace_memory_working_set_bytes`
- `max_namespace_restarts_15m`
- `max_pod_cpu_usage_cores`
- `max_pod_memory_working_set_bytes`
- `max_pod_restarts_15m`

Operational behavior:

- The detector has a warm-up period before it can score anomalies
- With the default 5-minute step and `min_training_samples: 36`, it needs 37 aligned samples before the first real evaluation
- That is roughly 3 hours of Prometheus history
- During warm-up, Grafana shows detector readiness and sample progress
- The detector applies both model-based anomaly checks and simple incident rules
- Severity is assigned as `warning` or `critical`
- Duplicate anomalies are suppressed for a configurable window to avoid flooding OpenSearch
- Only true anomalies are written to OpenSearch
- Anomaly documents are stored in indices named like `ai-anomalies-YYYY.MM.DD`

Useful AI metrics exposed by the service:

- `ai_anomaly_ready`
- `ai_anomaly_available_samples`
- `ai_anomaly_required_samples`
- `ai_anomaly_score_normalized`
- `ai_anomaly_threshold`
- `ai_anomaly_last_success_timestamp_seconds`
- `ai_anomaly_last_published_timestamp_seconds`
- `ai_anomaly_rule_hits`
- `ai_anomaly_severity_level`
- `ai_anomaly_suppression_events_total`
- `ai_feature_value`
- `ai_feature_baseline`
- `ai_feature_residual`
- `ai_feature_zscore`

## Verification

Basic cluster verification:

```bash
kubectl get nodes -L workload
kubectl get pods -A
kubectl get servicemonitor,podmonitor -A
```

AI engine verification:

```bash
kubectl logs -n ai-engine deploy/pyod-anomaly-detector --tail=100
kubectl exec -n opensearch statefulset/opensearch-cluster-master -- \
  curl -sk -u admin:'<configured-admin-password>' https://localhost:9200/_cat/indices/ai-anomalies-*?v
```

## Operational Notes

- Community dashboards are vendored under [roles/monitoring/files/grafana](roles/monitoring/files/grafana) for offline-safe provisioning
- The database role is safe to rerun for monitoring changes because [inventory/group_vars/all/main.yml](inventory/group_vars/all/main.yml) now uses `database.force_recreate: false` by default
- If you intentionally want to destroy and recreate the PostgreSQL cluster, set `database.force_recreate: true`
- Traefik metrics are enabled through a K3s `HelmChartConfig`
- OpenSearch is monitored through a separate exporter instead of modifying the OpenSearch image
- The AI engine can be stopped without deleting it by scaling the deployment to zero:

```bash
kubectl scale deploy/pyod-anomaly-detector -n ai-engine --replicas=0
```

## Production Roadmap

The concrete production-hardening plan now lives in [ROADMAP.md](ROADMAP.md).

The current execution order is:

1. Security hardening
2. Topology abstraction and horizontal scaling
3. Deterministic packaging and offline installs
4. Readiness and rerun hardening
5. Alerting and SLOs
6. CI and smoke validation
7. Profiles, docs, and runbooks

## Next Customization Points

- Add application-specific roles beyond Forgejo
- Extend the AI engine with more service-level or workload-level anomaly models
- Add platform-specific alerting packs beyond the base roadmap
- Add more reusable application roles beyond the example Forgejo workload
