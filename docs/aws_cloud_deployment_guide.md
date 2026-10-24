# AWS Cloud Deployment Guide: 24/7/365 AI Operations & Recovery Engine

This guide provides end-to-end instructions for deploying the **SEPLE NovaEdge AI Engine** to dedicated AWS cloud infrastructure (AWS Lightsail, AWS EC2, or AWS ECS/Fargate).

Deploying to cloud infrastructure ensures that daily fleet risk evaluation, multi-channel alerting (ThingsBoard, Slack, Email), and real-time hardware recovery detection operate continuously without relying on a personal laptop being powered on or connected to Wi-Fi.

---

## 1. System Requirements & Cloud Sizing

The production engine is optimized to be lightweight and fast:
* **Active Memory (RAM):** ~250 MB peak during scoring.
* **CPU Usage:** < 5% idle; brief 10–20 second burst during daily scoring.
* **Disk Footprint:** < 1.5 GB total (including base Linux OS, Python runtime, and model weights).
* **Network Egress:** Outbound HTTPS (port 443) to ThingsBoard and Slack; Outbound SMTP (port 587) for Gmail alerts. No inbound ports or public IP exposure required.

### Recommended AWS Instances
| Provider / Service | Sizing | Monthly Cost | Best For |
|---|---|---|---|
| **AWS Lightsail (Recommended)** | 1 vCPU, 1 GB RAM, 40 GB SSD | **$5.00 / month** | Predictable fixed pricing, included bandwidth, 1-click setup |
| **AWS EC2** | `t3.micro` or `t3.small` | **$3.50 – $7.50 / mo** (Free Tier eligible) | Standard AWS VPC integration, enterprise IAM roles |
| **AWS ECS (Fargate)** | 0.25 vCPU, 0.5 GB RAM | **< $0.10 / month** | True serverless execution (runs only 30s per scheduled task) |

---

## 2. Git Versioning & Repository Hygiene

### A. What to Commit vs. What to Ignore
* **Tracked in Git:**
  - Application source code (`send_daily_risk_digest.py`, `src/`, `scripts/`).
  - Production model bundles: `models/gbm_outage_oct2026_365d/` (~2.5 MB) and `models/isolation_forest_365d/` (~25 MB).
  - Deployment assets (`Dockerfile`, `docker-compose.yml`, `requirements-prod.txt`, `deploy/setup_aws_ec2.sh`, `.env.example`).
* **Ignored in Git (`.gitignore`):**
  - Secrets and credentials (`.env`).
  - Heavy raw telemetry captures (`*.parquet`, `training_data/`, `data_harvest/`).
  - Local Python environments (`.venv/`) and experimental LLM weights (`models/qwen_reasoning_outage_predictor/`).

### B. Setting up Private GitHub / GitLab Deployment
1. Initialize or push to your team's private GitHub/GitLab repository:
   ```bash
   git add .
   git commit -m "feat: production AWS deployment bundle with 24/7 cloud runner"
   git push origin main
   ```
2. On your AWS server, generate a dedicated read-only SSH Deploy Key:
   ```bash
   ssh-keygen -t ed25519 -C "aws-novaedge-deploy" -f ~/.ssh/id_ed25519 -N ""
   cat ~/.ssh/id_ed25519.pub
   ```
3. In GitHub/GitLab: Navigate to **Repository Settings -> Deploy Keys -> Add Deploy Key** (leave "Allow write access" unchecked for maximum security).
4. Clone the repository onto your server:
   ```bash
   git clone git@github.com:your-organization/Data_scrapping_thgingsboard_ml-intern.git /opt/novaedge-ai
   cd /opt/novaedge-ai
   ```

---

## 3. Option 1: AWS Lightsail ($5/month — Recommended)

AWS Lightsail is the simplest option with fixed monthly billing and zero surprise bandwidth fees.

### Step 1: Launch Instance in AWS Lightsail Console
1. Log into the AWS Console and open **Amazon Lightsail**.
2. Click **Create instance**.
3. Select your region (e.g., `ap-south-1` Mumbai for minimal latency to ThingsBoard).
4. Select platform: **Linux/Unix**.
5. Select blueprint: **OS Only -> Ubuntu 22.04 LTS** (or Amazon Linux 2023).
6. Select the **$5.00 USD/month** plan (1 GB RAM, 1 vCPU, 40 GB SSD, 2 TB transfer).
7. Name the instance (e.g., `novaedge-ai-prod`) and click **Create instance**.

### Step 2: Run Automated Setup Script
Connect via the Lightsail browser SSH terminal or your local terminal, then run:
```bash
# Clone repository
git clone git@github.com:your-organization/Data_scrapping_thgingsboard_ml-intern.git /opt/novaedge-ai
cd /opt/novaedge-ai

# Make setup script executable and run
chmod +x deploy/setup_aws_ec2.sh
./deploy/setup_aws_ec2.sh
```

### Step 3: Configure Environment Credentials
```bash
# Edit .env with your live keys
nano .env
```
Ensure the following keys are populated:
```ini
TB_HOST=https://seple.iot-private.cloud
TB_EMAIL=info@seple.in
TB_PASSWORD=your_actual_password
SLACK_WEBHOOK=https://hooks.slack.com/services/REPLACE_WITH_YOUR_SLACK_WEBHOOK
SMTP_EMAIL=your_email@gmail.com
SMTP_PASSWORD=your_16_char_app_password
EMAIL_TO=animesh.choudhury@seple.in
DIGEST_HOUR_UTC=6
POLL_INTERVAL_MINUTES=15
```

### Step 4: Build and Launch 24/7 Container
```bash
docker compose up -d --build
```

Verify that the container is running and healthy:
```bash
docker compose ps
docker compose logs -f
```

---

## 4. Option 2: AWS EC2 (`t3.small` / `t3.micro`)

If your company uses an established AWS VPC, deploy to a standard EC2 instance.

### Step 1: Security Group Configuration
In the EC2 console:
* **Inbound Rules:**
  - SSH (port 22): Restrict to your office IP or AWS Systems Manager (SSM) Session Manager.
  - *No other inbound ports are needed.*
* **Outbound Rules:**
  - HTTPS (port 443): Allowed to `0.0.0.0/0` (ThingsBoard, Slack API).
  - SMTPS (port 587): Allowed to `0.0.0.0/0` (Gmail / Amazon SES).

### Step 2: Automated Deployment via Docker
SSH into your EC2 instance and run:
```bash
cd /home/ubuntu
git clone git@github.com:your-organization/Data_scrapping_thgingsboard_ml-intern.git
cd Data_scrapping_thgingsboard_ml-intern
chmod +x deploy/setup_aws_ec2.sh
./deploy/setup_aws_ec2.sh

# Fill in .env
cp .env.example .env
nano .env

# Start daemon
docker compose up -d --build
```

### Step 3: Alternative Native Systemd Service (Without Docker)
If you prefer running natively on Linux without Docker:
1. Create a Python virtual environment:
   ```bash
   sudo apt-get install -y python3-venv python3-pip libgomp1
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements-prod.txt
   ```
2. Create systemd unit file `/etc/systemd/system/novaedge-ai.service`:
   ```ini
   [Unit]
   Description=SEPLE NovaEdge AI Operations Engine
   After=network.target

   [Service]
   Type=simple
   User=ubuntu
   WorkingDirectory=/opt/novaedge-ai
   ExecStart=/opt/novaedge-ai/.venv/bin/python /opt/novaedge-ai/scripts/cloud_runner.py --daemon
   Restart=always
   RestartSec=30
   EnvironmentFile=/opt/novaedge-ai/.env

   [Install]
   WantedBy=multi-user.target
   ```
3. Enable and start:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now novaedge-ai
   sudo journalctl -u novaedge-ai -f
   ```

---

## 5. Option 3: AWS ECS / Fargate (Serverless Scheduled Task)

If your organization prefers zero server management:
1. **Build and push image to Amazon ECR:**
   ```bash
   aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin <aws_account_id>.dkr.ecr.ap-south-1.amazonaws.com
   docker build -t novaedge-ai-agent .
   docker tag novaedge-ai-agent:latest <aws_account_id>.dkr.ecr.ap-south-1.amazonaws.com/novaedge-ai:latest
   docker push <aws_account_id>.dkr.ecr.ap-south-1.amazonaws.com/novaedge-ai:latest
   ```
2. **Create ECS Task Definition:**
   - Launch type: **Fargate**.
   - CPU: `0.25 vCPU (256)`, Memory: `0.5 GB (512 MB)`.
   - Command override: `["python", "send_daily_risk_digest.py", "--sync-tb", "--slack", "--email"]`.
   - Secrets: Store `.env` keys in **AWS Systems Manager Parameter Store** or **AWS Secrets Manager**.
3. **Configure AWS EventBridge Schedule:**
   - Schedule expression: `cron(0 6 * * ? *)` (Every day at 06:00 UTC).
   - Target: ECS Task.

---

## 6. Operational Runbook & Maintenance

### Common Administrative Commands

* **View Live Container Output:**
  ```bash
  docker compose logs -f --tail=100
  ```
* **Trigger an Immediate Fleet Evaluation & Dispatch:**
  ```bash
  docker compose exec novaedge-ai-agent python send_daily_risk_digest.py --sync-tb --slack --email
  ```
* **Trigger a Real-Time Recovery Probe:**
  ```bash
  docker compose exec novaedge-ai-agent python scripts/cloud_runner.py --probe-only
  ```
* **Update Code from Git:**
  ```bash
  git pull origin main
  docker compose up -d --build
  ```
* **Inspect Daily Reports:**
  All generated HTML briefs, CSV digests, and recovery ledgers are persisted in `./reports` on the host:
  ```bash
  ls -la reports/
  cat reports/latest_risk_digest.json | grep -A 10 "summary"
  ```
