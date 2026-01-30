# AI-Powered K3s Observability Framework

## Project Vision
A 6-VM lightweight Kubernetes (K3s) cluster designed for automated observability and AI-driven anomaly detection. This framework enables rapid prototyping and experimentation with cloud-native monitoring, logging, and AI-based anomaly detection in a resource-efficient environment.

## Architecture Overview

**Cluster Topology:**

- **VM 1: K8s Controller (The Brain)**  
  Orchestrates the cluster, manages workloads, and exposes the Kubernetes API.
- **VM 2: App Worker (Microservices)**  
  Hosts microservices and application workloads.
- **VM 3: DB Worker (PostgreSQL via CloudNativePG)**  
  Dedicated to running PostgreSQL using the CloudNativePG operator.
- **VM 4: Logging Node (OpenSearch Stack)**  
  Centralized log aggregation and search with OpenSearch and OpenSearch Dashboards.
- **VM 5: Monitoring Node (Prometheus & Grafana)**  
  Collects metrics and provides dashboards for observability.
- **VM 6: AI Engine (Anomaly Detection scripts)**  
  Runs Python-based AI scripts for real-time anomaly detection.

> **Architecture Diagram:**
>
> ![Cluster Architecture](docs/architecture-diagram.png)
> *(Add your diagram in the docs/ folder)*

## Tech Stack
- **K3s** (Lightweight Kubernetes)
- **Ansible** (Automated provisioning and configuration)
- **OpenSearch** (Logging & Search)
- **Prometheus & Grafana** (Monitoring & Visualization)
- **Python** (AI/ML for anomaly detection)

## Key Features
- Automated node joining and specialized labeling via Ansible
- Lightweight design optimized for VirtualBox/Vagrant
- Declarative workload placement using K8s Node Affinity
- Modular, extensible roles for easy customization

## Getting Started

1. **Edit Inventory:**
   - Update `inventory/inventory.ini` with your VM IP addresses and hostnames.
2. **Configure Variables:**
   - Set passwords, resource limits, and other settings in `inventory/group_vars/all.yml`.
3. **Deploy the Cluster:**
   - Run:
     ```sh
     ansible-playbook -i inventory/inventory.ini playbooks/site.yml
     ```

## Verification
After deployment, verify your cluster and node labels:

```sh
kubectl get nodes --show-labels
```

You should see each node with its specialized `workload` label (e.g., `workload=ai`, `workload=logging`).

---

For more details, see the playbooks and roles in this repository. Contributions and feedback are welcome!
