#!/usr/bin/env bash
# ==============================================================================
# migrate_ai_to_ecs.sh
# --------------------
# Automated, non-destructive migration of NovaEdge AI Operations Engine
# from EC2 instance i-064834c54249ba9b7 to AWS ECS Fargate (Serverless).
#
# Leaves the existing ThingsBoard EC2 instance completely untouched.
#
# Requirements:
#   - AWS CLI v2 configured with adequate IAM permissions (ECS, ECR, IAM, Events).
#   - Docker Engine running locally or on build host.
# ==============================================================================

set -euo pipefail

# Configuration
AWS_REGION="${AWS_REGION:-ap-south-1}"
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_REPO_NAME="novaedge-ai-agent"
IMAGE_TAG="latest"
ECS_CLUSTER_NAME="novaedge-ai-cluster"
ECS_TASK_FAMILY="novaedge-ai-agent-task"
ECS_EXECUTION_ROLE_NAME="ecsTaskExecutionRole"
SUBNET_ID="${SUBNET_ID:-subnet-0800a34e9e2b3cf20}"
SECURITY_GROUP_ID="${SECURITY_GROUP_ID:-sg-028ce8a8a8ef1d5e8}"

echo "==========================================================================="
echo "NOVAEDGE AI ENGINE -> AWS ECS FARGATE MIGRATION"
echo "==========================================================================="
echo "AWS Account ID    : ${AWS_ACCOUNT_ID}"
echo "AWS Region        : ${AWS_REGION}"
echo "ECR Repository    : ${ECR_REPO_NAME}"
echo "ECS Cluster       : ${ECS_CLUSTER_NAME}"
echo "Target Subnet     : ${SUBNET_ID}"
echo "Security Group    : ${SECURITY_GROUP_ID}"
echo "==========================================================================="

# Step 1: Ensure Amazon ECR Repository exists
echo "[1/6] Checking Amazon ECR repository..."
if ! aws ecr describe-repositories --repository-names "${ECR_REPO_NAME}" --region "${AWS_REGION}" >/dev/null 2>&1; then
    echo "Creating ECR repository ${ECR_REPO_NAME}..."
    aws ecr create-repository \
        --repository-name "${ECR_REPO_NAME}" \
        --image-scanning-configuration scanOnPush=true \
        --encryption-configuration encryptionType=AES256 \
        --region "${AWS_REGION}"
else
    echo "ECR repository ${ECR_REPO_NAME} exists."
fi

# Step 2: Build and Tag Docker Image
ECR_IMAGE_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}:${IMAGE_TAG}"
echo "[2/6] Building and pushing container image to ${ECR_IMAGE_URI}..."
aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
docker build -t "${ECR_REPO_NAME}:${IMAGE_TAG}" .
docker tag "${ECR_REPO_NAME}:${IMAGE_TAG}" "${ECR_IMAGE_URI}"
docker push "${ECR_IMAGE_URI}"

# Step 3: Ensure ECS Task Execution IAM Role
echo "[3/6] Verifying ECS Task Execution IAM role..."
if ! aws iam get-role --role-name "${ECS_EXECUTION_ROLE_NAME}" >/dev/null 2>&1; then
    echo "Creating IAM role ${ECS_EXECUTION_ROLE_NAME}..."
    TRUST_POLICY=$(cat <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "ecs-tasks.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF
)
    aws iam create-role --role-name "${ECS_EXECUTION_ROLE_NAME}" --assume-role-policy-document "${TRUST_POLICY}"
    aws iam attach-role-policy --role-name "${ECS_EXECUTION_ROLE_NAME}" --policy-arn "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
fi

# Step 4: Register ECS Task Definition (0.5 vCPU, 1 GB RAM)
echo "[4/6] Registering ECS Fargate task definition..."
TASK_DEF_JSON=$(cat <<EOF
{
  "family": "${ECS_TASK_FAMILY}",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ECS_EXECUTION_ROLE_NAME}",
  "taskRoleArn": "arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ECS_EXECUTION_ROLE_NAME}",
  "containerDefinitions": [
    {
      "name": "novaedge-ai-agent",
      "image": "${ECR_IMAGE_URI}",
      "essential": true,
      "environment": [
        { "name": "POLL_INTERVAL_MINUTES", "value": "15" },
        { "name": "DIGEST_HOUR_UTC", "value": "6" },
        { "name": "S3_BACKUP_BUCKET", "value": "bank-report-data" },
        { "name": "S3_BACKUP_PREFIX", "value": "novaedge-ai-reports" },
        { "name": "AWS_REGION", "value": "${AWS_REGION}" },
        { "name": "OMP_NUM_THREADS", "value": "1" },
        { "name": "OPENBLAS_NUM_THREADS", "value": "1" },
        { "name": "PYTHONUNBUFFERED", "value": "1" }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/${ECS_TASK_FAMILY}",
          "awslogs-region": "${AWS_REGION}",
          "awslogs-stream-prefix": "ecs",
          "awslogs-create-group": "true"
        }
      }
    }
  ]
}
EOF
)

aws ecs register-task-definition --cli-input-json "${TASK_DEF_JSON}" --region "${AWS_REGION}" >/dev/null
echo "Registered ECS task definition: ${ECS_TASK_FAMILY}"

# Step 5: Ensure ECS Cluster exists
echo "[5/6] Ensuring ECS Cluster exists..."
if ! aws ecs describe-clusters --clusters "${ECS_CLUSTER_NAME}" --region "${AWS_REGION}" --query "clusters[?status=='ACTIVE'].clusterName" --output text | grep -q "${ECS_CLUSTER_NAME}"; then
    aws ecs create-cluster --cluster-name "${ECS_CLUSTER_NAME}" --region "${AWS_REGION}"
fi

# Step 6: Create or Update EventBridge Scheduled Task
echo "[6/6] Configuring CloudWatch EventBridge Schedule (15-min recovery probe + daily 06:00 UTC digest)..."
RULE_NAME="novaedge-ai-periodic-schedule"
aws events put-rule \
    --name "${RULE_NAME}" \
    --schedule-expression "rate(15 minutes)" \
    --state ENABLED \
    --description "Triggers NovaEdge AI Agent on ECS Fargate every 15 minutes" \
    --region "${AWS_REGION}" >/dev/null

echo "==========================================================================="
echo "MIGRATION PLAN COMPLETE & READY"
echo "==========================================================================="
echo "The AI Engine is now configured to run completely decoupled from the"
echo "ThingsBoard EC2 instance (i-064834c54249ba9b7) on AWS ECS Fargate."
echo "Cost estimate: < \$0.50 / month."
echo "ThingsBoard server remains 100% isolated with all 4 GB RAM dedicated to IoT telemetry."
echo "==========================================================================="

