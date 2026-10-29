# NovaEdge AI Engine Isolation & Migration Strategy

- **Document ID:** DOC-STRAT-20261029-ISOL-01
- **Target Host:** AWS EC2 Instance `i-064834c54249ba9b7` (`novaedge-dev`)
- **AWS Region / AZ:** `ap-south-1` / `ap-south-1a` (Mumbai)
- **Author:** Teamwork Architecture & Cloud Systems Taskforce (Milestone 2, R3)
- **Status:** Complete Technical Strategy & Runbook
- **Date:** 2026-10-29

---

## 1. Executive Summary & Architectural Motivation

The forensic coexistence diagnostic audit ([docs/coexistence_diagnostic_audit_report.md](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/docs/coexistence_diagnostic_audit_report.md)) and ThingsBoard health verification ([docs/thingsboard_health_verification_report.md](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/docs/thingsboard_health_verification_report.md)) established three foundational facts:

1. **ThingsBoard Operational Health:** The monolithic ThingsBoard platform container (`novaedge-dev`) is online and functional, handling HTTP port 8080 web traffic, MQTT port 1883 telemetry, and secure MQTTS port 8883 connections without permanent service degradation.
2. **Zero Port Contention:** The AI container (`novaedge-ai-agent`) binds zero host ports, completely eliminating network socket collisions.
3. **Severe Memory & Contention Vulnerability:** Unconstrained coexistence on a burstable `t3.medium` instance (3,833 MB usable RAM, 0 MB swap space) created physical memory exhaustion. During batch inference bursts, the Linux kernel Out-Of-Memory killer terminated `novaedge-ai-agent` with Exit Code 137. Furthermore, a synchronized restart of `novaedge-dev` occurred at the exact second of the memory crisis (11:43:10Z), proving that running unconstrained batch machine learning alongside a production Java IoT platform on a 4 GB RAM host poses an unacceptable availability risk.

To ensure long-term operational resilience, this document outlines an isolation and migration roadmap with step-by-step instructions and runnable configuration files.

---

## 2. Comparative Evaluation of Architectural Strategies

| Strategy | Architecture | Operational Risk to ThingsBoard | Monthly Infrastructure Cost | Deployment Complexity | Recommendation |
|---|---|---|---|---|---|
| **Strategy 1: Hardened Coexistence** | Retain both containers on `i-064834c54249ba9b7`; enforce 512 MB cgroup memory cap, 0.50 CPU quota, `OMP_NUM_THREADS=1`, and provision 2 GB host swapfile. | Low (governed by Linux cgroups and swap protection) | **$0.00** (no additional AWS resources) | Low (single `docker-compose` override) | **Immediate Tactical Baseline** |
| **Strategy 2: Serverless AWS ECS Fargate** | Decouple AI engine to AWS ECS Fargate; trigger every 15 min via EventBridge schedule; write daily digests and models to S3. | **Zero** (100% decoupled compute and network) | **< $0.50 / month** (billed only for runtime seconds) | Medium (ECR push + task definition) | **Primary Strategic Recommendation** |
| **Strategy 3: Dedicated AWS Lightsail / EC2** | Deploy AI engine as a standing daemon on a dedicated 1 vCPU / 1 GB RAM virtual machine in `ap-south-1`. | **Zero** (100% physical compute isolation) | **$3.50 – $5.00 / month** | Low (standard Docker daemon on independent host) | **Alternative Standalone Option** |

---

## 3. Strategy 1: Hardened Coexistence Implementation Runbook

If organizational requirements mandate keeping the AI operations engine on the existing EC2 host, the following tactical hardening steps must be executed to eliminate memory exhaustion and CPU starvation.

### Step 1.1: Provision Host Swap Space (2 GB)
Execute via AWS Systems Manager (SSM) on `i-064834c54249ba9b7`:
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```
*Rationale:* Provides a safety buffer for anonymous dirty memory pages, preventing immediate invocation of the Linux kernel `oom-killer` during brief inference spikes.

### Step 1.2: Apply Docker Cgroup Resource Limits
A drop-in Docker Compose override file is provided at [deploy/docker-compose.resource-limits.yml](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/deploy/docker-compose.resource-limits.yml):
```yaml
services:
  novaedge-ai-agent:
    deploy:
      resources:
        limits:
          cpus: '0.50'
          memory: 512M
        reservations:
          memory: 256M
    environment:
      - OMP_NUM_THREADS=1
      - OPENBLAS_NUM_THREADS=1
      - MKL_NUM_THREADS=1
    memswap_limit: 512m
```

### Step 1.3: Deploy Hardened Container Configuration
On the host, launch the container using the multi-file compose command:
```bash
cd /opt/novaedge-ai
docker compose -f docker-compose.yml -f deploy/docker-compose.resource-limits.yml up -d
```
Verify cgroup enforcement:
```bash
docker inspect novaedge-ai-agent --format 'Memory Limit: {{.HostConfig.Memory}} bytes | NanoCPUs: {{.HostConfig.NanoCPUs}}'
```
*Expected Output:* `Memory Limit: 536870912 bytes | NanoCPUs: 500000000` (512 MB and 0.50 vCPU).

---

## 4. Strategy 2: Strategic Cloud Migration to AWS ECS Fargate (Recommended)

Migrating the AI operations engine to AWS ECS Fargate completely frees the ThingsBoard EC2 host from batch inference loads, allowing all 4 GB RAM and both vCPUs to serve IoT device connections exclusively.

### Architecture Overview
1. **ECR Image Repository:** `901178127457.dkr.ecr.ap-south-1.amazonaws.com/novaedge-ai-agent:latest`
2. **Compute Profile:** AWS Fargate Task (0.5 vCPU / 512 CPU units, 1024 MB RAM).
3. **Execution Mode:** Serverless scheduled batch task invoked every 15 minutes by Amazon EventBridge.
4. **Data Persistence:** Reports and machine learning models continue to be archived to Amazon S3 (`s3://bank-report-data/novaedge-ai-reports/`).
5. **Telemetry & Notifications:** CloudWatch Metrics (`NovaEdge/FleetAgent`), Slack webhook notifications, and SMTP email alerts.

### Automated Turnkey Execution Script
An automated migration script is provided at [deploy/migrate_ai_to_ecs.sh](file:///c:/workspace/Data_scrapping_thgingsboard_ml-intern/deploy/migrate_ai_to_ecs.sh).

To execute the migration:
```bash
chmod +x deploy/migrate_ai_to_ecs.sh
./deploy/migrate_ai_to_ecs.sh
```

The script performs the following operations non-destructively:
1. Validates AWS CLI credentials in region `ap-south-1`.
2. Creates the Amazon ECR repository `novaedge-ai-agent` with scan-on-push enabled.
3. Builds and tags the Docker image, then authenticates and pushes to Amazon ECR.
4. Provisions the `ecsTaskExecutionRole` IAM policy if missing.
5. Registers the ECS task definition `novaedge-ai-agent-task` with 512 CPU units and 1024 MB RAM.
6. Provisions the ECS cluster `novaedge-ai-cluster`.
7. Creates the Amazon EventBridge rule `novaedge-ai-periodic-schedule` to trigger execution every 15 minutes.

### Decommissioning on EC2 `i-064834c54249ba9b7`
Once the ECS Fargate task is confirmed active in AWS CloudWatch, safely halt the container on the ThingsBoard host:
```bash
cd /opt/novaedge-ai
docker compose down
```
ThingsBoard will then operate with 100% of instance resources dedicated solely to IoT ingest.

---

## 5. Strategy 3: Dedicated Standalone EC2 / Lightsail Instance

If an always-on Linux daemon is preferred over serverless execution, provision a dedicated small instance in `ap-south-1`:

### Instance Profile
- **AWS Service:** Amazon Lightsail (Linux / Ubuntu 24.04 LTS) or Amazon EC2 `t4g.micro` (ARM Graviton) / `t3.micro` (x86_64).
- **Compute Specs:** 1 vCPU, 1 GB RAM, 25 GB SSD.
- **Estimated Cost:** $3.50 – $5.00 / month.
- **Security Group:** Inbound: None (no open ingress ports). Outbound: All traffic (HTTPS 443, SMTP 587).

### Deployment Procedure
1. Launch the new virtual machine and install Docker Engine.
2. Clone repository:
   ```bash
   git clone https://github.com/Aniket-R-and-D/Data_scrapping_thgingsboard_ml-intern.git /opt/novaedge-ai
   cd /opt/novaedge-ai
   ```
3. Copy environment configuration `.env` containing Slack webhook URL, ThingsBoard credentials, and SMTP settings.
4. Launch container:
   ```bash
   docker compose up -d --build
   ```

---

## 6. Verification and Validation Checklist

Before declaring the migration complete, verify the following operational metrics:

1. **ThingsBoard Instance Stability:**
   - On `i-064834c54249ba9b7`, run `scripts/thingsboard/verify_tb_health_live.py --host 43.205.119.168`.
   - Confirm Web UI latency is `< 250 ms` and Layer 7 MQTT protocol handshake succeeds.
   - Confirm host available RAM remains `> 1,500 MB`.
2. **AI Engine Delivery:**
   - Verify daily 06:00 UTC digest arrives in Slack channel (`#daily-risk-digest`) with HTTP 200.
   - Verify email digest arrives at `animesh.choudhury@seple.in`.
   - Verify artifacts (HTML/CSV digests) are uploaded to `s3://bank-report-data/novaedge-ai-reports/`.
3. **CloudWatch Metrics:**
   - Verify metric publication in namespace `NovaEdge/FleetAgent` for metric `TelemetryDriftAnomalyScore`.

