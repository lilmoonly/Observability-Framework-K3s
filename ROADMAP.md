# Production Roadmap

This roadmap turns the current observability lab into a production-grade, reusable framework for Kubernetes-based microservice platforms.

The sequencing matters. Several items depend on earlier hardening work, so this plan is intentionally ordered to reduce rework.

## Current Baseline

The framework already provides:

- phased Ansible deployment for K3s, database, logging, monitoring, AI anomaly detection, ingress, and an example application
- vendored Grafana dashboards for offline-safe provisioning
- metrics scraping for Kubernetes, Traefik, CloudNativePG, Fluent Bit, OpenSearch exporter, Forgejo, and the AI engine
- a base PrometheusRule pack for platform, logging, ingress, storage, PostgreSQL, and MongoDB health
- an AI dashboard that explains detector health, contributors, rule checks, and recent anomaly outcomes

What still keeps it in "advanced lab" territory rather than "production framework":

- live internet/bootstrap dependencies
- some soft readiness gates and best-effort checks
- basic Alertmanager routing and SLO coverage
- no CI or automated smoke validation

## Milestone Plan

| Milestone | Focus | Outcome |
| --- | --- | --- |
| `v1.1` | Secrets and security hardening | Safe credential handling and tighter platform defaults |
| `v1.2` | Topology abstraction | Framework works beyond the fixed 6-node lab |
| `v1.3` | Deterministic packaging | Repeatable, offline-friendly deployments |
| `v1.4` | Readiness and rerun hardening | Fail-fast deploy flow with stronger idempotency |
| `v1.5` | Alerting and SLOs | Production-grade incident detection beyond dashboards |
| `v1.6` | CI and smoke validation | Automated confidence in changes and upgrades |
| `v1.7` | Profiles, docs, and runbooks | Clear lab/prod separation and operational guidance |

## v1.1 Security Hardening

### Goals

- Remove plaintext secrets from the repo.
- Eliminate weak default credentials.
- Reduce accidental leakage in task output and file permissions.

### Scope

- move cluster/application secrets out of committed shared vars and into an encrypted secrets file
- add `no_log: true` to tasks that may print credentials
- tighten kubeconfig permissions and any generated secret-bearing files
- document the secure bootstrap flow in the README

### Exit Criteria

- repository can be shared without exposing real credentials
- first deploy requires supplying secrets out-of-band
- no secret values appear in normal Ansible output
- playbook fails early when placeholder or demo secrets are still in use

## v1.2 Topology Abstraction

### Goals

- Remove the hard dependency on the fixed 6-VM layout.
- Support larger or smaller clusters through inventory and pool definitions.

### Scope

- replace hardcoded node names with pool-based labels and taints
- define inventory profiles for `lab`, `compact`, and `scaled`
- generate workload placement from config instead of explicit hostnames
- allow roles to target pools such as `database`, `logging`, `monitoring`, `ai`, `app`, or `general`

### Exit Criteria

- same playbook supports 3-node, 6-node, and larger topologies with config changes only
- taints/labels are derived from inventory data, not fixed hostnames
- README includes a scaling example

## v1.3 Deterministic Packaging

### Goals

- Make deployments predictable and friendly to restricted environments.
- Reduce runtime drift caused by live downloads.

### Scope

- pin Helm chart versions everywhere
- reduce `curl | bash` bootstrap paths where practical
- vendor or mirror required artifacts for offline installs
- replace runtime `pip install` in `ai-engine` with a real container image
- document supported artifact sources and upgrade rules

### Exit Criteria

- production deployment can run without general outbound internet
- framework installs the same versions every time
- AI engine no longer depends on package installation during pod startup

## v1.4 Readiness and Rerun Hardening

### Goals

- Make failure states explicit.
- Improve rerun safety and reduce half-successful deployments.

### Scope

- replace fragile `Running` checks with readiness/availability checks
- remove soft-fail verification where the framework should stop
- standardize rollout, health, and scrape verification across roles
- add explicit uninstall/cleanup paths for major components
- review idempotency of Helm and Kubernetes tasks

### Exit Criteria

- reruns are predictable and boring
- broken dependencies fail early with actionable errors
- component cleanup paths are documented and automated

## v1.5 Alerting and SLOs

### Goals

- Move from dashboard-first observability to incident-ready observability.
- Provide reusable alerting for the platform, not just the demo application.

### Scope

- add `PrometheusRule` packs for Kubernetes, CNPG, MongoDB, OpenSearch, Traefik, Fluent Bit, and AI engine
- define baseline Alertmanager routing
- add a small recording-rule layer for high-value queries
- define initial SLO-style golden signals for generic services

### Exit Criteria

- common platform failures trigger alerts automatically
- Prometheus rules are versioned inside the framework
- AI anomaly severity can participate in alerting

## v1.6 CI and Smoke Validation

### Goals

- Validate framework changes before deployment.
- Catch regressions in syntax, templates, dashboards, and role flow.

### Scope

- add `ansible-lint`, YAML/JSON validation, and template checks
- add a smoke-test workflow for core phases
- add post-deploy verification scripts for cluster health
- define upgrade validation for framework releases

### Exit Criteria

- every change gets automated validation
- obvious playbook/template regressions are caught before runtime
- release notes can point to tested upgrade paths

## v1.7 Profiles, Docs, and Runbooks

### Goals

- Make the framework easier to adopt in both lab and production settings.
- Separate example/demo defaults from real deployment guidance.

### Scope

- split configuration into `lab` and `production`-oriented profiles
- document secret injection, scaling, storage, and sizing guidance
- add backup/restore and disaster-recovery notes
- add operator runbooks for common failures

### Exit Criteria

- a new team can deploy the framework without tribal knowledge
- production operators have a documented recovery path
- README stays concise while deep operational docs live in dedicated files

## Recommended Execution Order

1. `v1.1` Security hardening
2. `v1.2` Topology abstraction
3. `v1.3` Deterministic packaging
4. `v1.4` Readiness and rerun hardening
5. `v1.5` Alerting and SLOs
6. `v1.6` CI and smoke validation
7. `v1.7` Profiles, docs, and runbooks

## Next Sprint

With the first reusable alert pack in place, the next sprint should continue production hardening without making the example apps part of the core framework:

1. Add baseline Alertmanager routing and receiver configuration.
2. Add recording rules for high-value platform and database queries.
3. Define generic service SLOs that can apply to future real workloads.
4. Continue replacing live bootstrap dependencies with mirrored or vendored artifacts.
