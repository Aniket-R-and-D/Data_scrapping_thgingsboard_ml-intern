# Project: NovaEdge ThingsBoard & AI Engine Coexistence Investigation and Migration

## Architecture
- **Host Target**: AWS EC2 instance `i-064834c54249ba9b7` (`novaedge-dev`, Ubuntu 24.04 LTS, `t3.medium`: 2 vCPUs, 4 GB RAM, Region `ap-south-1`, Public IP `43.205.119.168`).
- **Co-Hosted Services**:
  1. `novaedge-dev`: Monolithic ThingsBoard Professional/Community Edition container (Spring Boot Java JVM, Netty MQTT transport, PostgreSQL database backend). Exposes host ports 8080 (HTTP Web UI/REST), 1883 (MQTT), and 8883 (MQTTS).
  2. `novaedge-ai-agent`: Python 3.12-slim daemon running batch inference (LightGBM/HistGradientBoosting with OpenMP), 15-minute recovery probe cycles, daily 06:00 UTC digests, and monthly model retraining. Zero host port bindings.
- **Access & Management Plane**: AWS Systems Manager (SSM) Agent online; remote execution via SSM Run Command / SSM Session Manager.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Resource Utilization Audit | Assess CPU, memory, swap, disk I/O, and cgroups between `novaedge-dev` and `novaedge-ai-agent` | M1 | survey |
| 2 | Port Contention & Network Audit | Verify host port bindings (8080, 1883, 8883) and establish zero port collision proof | M1 | survey |
| 3 | Container Lifecycle & Crash Analysis | Audit container uptimes, restart counts, exit codes (e.g. 137 OOM), and dmesg OOM killer logs | M1 | survey |
| 4 | Impact Correlation Conclusion | Conclude definitively whether `novaedge-ai-agent` caused performance degradation or crashes in `novaedge-dev` | M1 | survey |
| 5 | ThingsBoard Web UI Verification | Test HTTP port 8080 responsiveness, login endpoint latency, and health check API | M1 | survey |
| 6 | ThingsBoard MQTT Broker Verification | Verify Layer 4 TCP reachability and Layer 7 protocol CONNACK handshake on ports 1883 and 8883 | M1 | survey |
| 7 | ThingsBoard Container Error Log Audit | Review `novaedge-dev` logs for JVM OOM, HikariCP pool exhaustion, and Netty connection drops | M1 | survey |
| 8 | Safe Coexistence Configuration | Create runnable resource-limited Docker Compose configurations (cgroup limits, CPU quotas, OMP thread pinning) | M2 | survey |
| 9 | Cloud Migration Plan | Develop step-by-step migration to dedicated AWS ECS Fargate or dedicated EC2 instance with deploy scripts | M2 | survey |
| 10 | Executive Synthesis & Reporting | Synthesize diagnostic audit, health verification, and migration strategy into final human report | M3 | survey |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Diagnostic Audit & ThingsBoard Health Verification | R1 & R2: Full container resource, port, uptime, error log analysis, and Web UI/MQTT health verification on `i-064834c54249ba9b7` | none | COMPLETED |
| M2 | Isolation & Migration Plan with Runnable Configs | R3: Docker resource limits for coexistence and dedicated ECS/EC2 migration plan with runnable assets | M1 | COMPLETED |
| M3 | End-to-End Verification & Human Reporting | Full review, adversarial verification, forensic audit, and stakeholder reporting | M1, M2 | COMPLETED |

## Interface Contracts
### Diagnostic Tools ↔ Remote Host `i-064834c54249ba9b7`
- Protocol: AWS Systems Manager (SSM) `AWS-RunShellScript` / Session Manager.
- Execution format: Non-destructive, read-only shell commands (`docker inspect`, `docker stats --no-stream`, `docker logs`, `uptime`, `free -m`, `ss -tulpn`, `curl`).
- Error Handling: Failures trapped, stderr captured, timeout bounded.

### AI Engine Container ↔ Host Resources
- Memory: Maximum 512MB RAM cap under coexistence; zero swap.
- CPU: Maximum 0.50 vCPU (50% of 1 core) with `OMP_NUM_THREADS=1`.
- Networking: Outbound HTTPS/SMTP only; zero host port bindings.

## Code Layout
- `.agents/teamwork/`: Agent coordination metadata, plans, progress, and handoff reports.
- `docs/`: Architectural documentation, migration guides, and incident audit reports (`docs/coexistence_diagnostic_audit_report.md`, `docs/thingsboard_health_verification_report.md`, `docs/ai_engine_isolation_and_migration_strategy.md`).
- `deploy/`: Host deployment, Docker Compose overrides, and resource-constraining configs (`deploy/docker-compose.resource-limits.yml`, `deploy/migrate_ai_to_ecs.sh`).
- `scripts/cloud/`: SSM inspection, diagnostics, and container management utilities (`scripts/cloud/diagnose_coexistence_live.py`).
- `scripts/thingsboard/`: Live ThingsBoard probes and health check scripts (`scripts/thingsboard/verify_tb_health_live.py`).
