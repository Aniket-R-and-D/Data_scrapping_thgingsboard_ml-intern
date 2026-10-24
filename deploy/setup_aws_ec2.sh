#!/usr/bin/env bash
# ==============================================================================
# deploy/setup_aws_ec2.sh
# Automated Setup Script for AWS EC2 (Amazon Linux 2023 / Ubuntu) & AWS Lightsail
# Sets up Docker, Docker Compose, Git Deploy Key, and starts 24/7 AI Engine
# ==============================================================================

set -euo pipefail

echo "======================================================================"
echo "Starting NovaEdge AI Cloud Deployment Setup"
echo "======================================================================"

# 1. Detect Operating System
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
else
    echo "Unknown OS. Exiting."
    exit 1
fi

echo "[1/5] Detected Operating System: $OS"

# 2. Install Docker & Docker Compose
if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
    echo "[2/5] Updating apt packages and installing Docker..."
    sudo apt-get update -y
    sudo apt-get install -y ca-certificates curl gnupg lsb-release git

    # Add Docker's official GPG key
    sudo install -m 0755 -d /etc/apt/keyrings
    if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        sudo chmod a+r /etc/apt/keyrings/docker.gpg
    fi

    # Set up repository
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update -y
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

elif [ "$OS" = "amzn" ]; then
    echo "[2/5] Installing Docker on Amazon Linux..."
    sudo dnf update -y
    sudo dnf install -y docker git
    sudo systemctl enable --now docker
    # Install docker compose plugin
    DOCKER_CONFIG=${DOCKER_CONFIG:-/usr/local/lib/docker}
    sudo mkdir -p $DOCKER_CONFIG/cli-plugins
    sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m) -o $DOCKER_CONFIG/cli-plugins/docker-compose
    sudo chmod +x $DOCKER_CONFIG/cli-plugins/docker-compose
else
    echo "Unsupported OS distribution: $OS"
    exit 1
fi

# 3. Configure Docker permissions
echo "[3/5] Configuring Docker permissions..."
sudo usermod -aG docker "$USER" || true
sudo systemctl enable docker
sudo systemctl start docker

# 4. Verify Docker
echo "[4/5] Verifying Docker installation..."
sudo docker --version
sudo docker compose version

# 5. Environment configuration check
echo "[5/5] Checking environment configuration..."
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo "Creating .env from .env.example..."
        cp .env.example .env
        echo "⚠️  PLEASE EDIT .env WITH YOUR ACTUAL CREDENTIALS BEFORE STARTING:"
        echo "   nano .env"
    else
        echo "⚠️  No .env or .env.example found. Please create .env file."
    fi
else
    echo "Found existing .env file."
fi

echo "======================================================================"
echo "✅ AWS Environment Setup Completed Successfully!"
echo "======================================================================"
echo ""
echo "Next Steps to Launch:"
echo "1. Edit .env with your ThingsBoard, Slack, and Email credentials:"
echo "   nano .env"
echo ""
echo "2. Build and launch the 24/7 background container:"
echo "   docker compose up -d --build"
echo ""
echo "3. Monitor live logs:"
echo "   docker compose logs -f"
echo ""
echo "4. To trigger a one-shot execution immediately:"
echo "   docker compose exec novaedge-ai-agent python send_daily_risk_digest.py --sync-tb --slack --email"
echo "======================================================================"
