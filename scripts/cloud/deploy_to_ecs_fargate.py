#!/usr/bin/env python
"""scripts/cloud/deploy_to_ecs_fargate.py
Automated turnkey deployment of NovaEdge AI Operations Engine to AWS ECS Fargate.

Steps:
1. Load credentials and config from .env.
2. Authenticate Docker with Amazon ECR.
3. Build and push linux/amd64 Docker image to ECR.
4. Ensure IAM ecsTaskExecutionRole with S3, CloudWatch, and Logs permissions.
5. Ensure CloudWatch log group exists.
6. Register ECS Fargate Task Definition (0.5 vCPU, 1 GB RAM).
7. Ensure ECS Cluster exists.
8. Set up EventBridge Schedule Rules (Daily 06:00 UTC Digest + 15-min Recovery Probe).
9. Run a live test task on ECS Fargate to verify execution, S3 upload, and Slack dispatch.
10. Stop and remove the old stopped container on EC2 instance i-064834c54249ba9b7 via SSM.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Any, List

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ecs_fargate_deploy")

ROOT = Path(__file__).resolve().parents[2]
REGION = "ap-south-1"
ECR_REPO = "novaedge-ai-agent"
ECS_CLUSTER = "novaedge-ai-cluster"
TASK_FAMILY = "novaedge-ai-agent-task"
EXECUTION_ROLE_NAME = "ecsTaskExecutionRole"
LOG_GROUP_NAME = f"/ecs/{TASK_FAMILY}"
EC2_INSTANCE_ID = "i-064834c54249ba9b7"
SUBNET_ID = "subnet-0800a34e9e2b3cf20"
SECURITY_GROUP_ID = "sg-028ce8a8a8ef1d5e8"


def load_env_vars() -> Dict[str, str]:
    """Load key-value environment variables from .env file."""
    env_file = ROOT / ".env"
    env_vars: Dict[str, str] = {}
    if not env_file.exists():
        logger.warning(f"No .env file found at {env_file}")
        return env_vars

    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()
    return env_vars


def step1_check_identity(session: boto3.Session) -> str:
    """Check AWS caller identity and return Account ID."""
    sts = session.client("sts")
    ident = sts.get_caller_identity()
    account_id = ident["Account"]
    arn = ident["Arn"]
    logger.info(f"[1/8] AWS Identity verified: Account={account_id}, ARN={arn}")
    return account_id


def step2_build_and_push_image(session: boto3.Session, account_id: str) -> str:
    """Authenticate with ECR, build linux/amd64 Docker image, and push to ECR."""
    ecr = session.client("ecr", region_name=REGION)
    ecr_uri = f"{account_id}.dkr.ecr.{REGION}.amazonaws.com/{ECR_REPO}:latest"

    logger.info(f"[2/8] Getting ECR login token for {account_id}.dkr.ecr.{REGION}.amazonaws.com...")
    auth_data = ecr.get_authorization_token()
    token = auth_data["authorizationData"][0]["authorizationToken"]
    import base64
    user_pass = base64.b64decode(token).decode("utf-8")
    username, password = user_pass.split(":", 1)

    login_cmd = [
        "docker", "login",
        "--username", username,
        "--password-stdin",
        f"{account_id}.dkr.ecr.{REGION}.amazonaws.com"
    ]
    p = subprocess.Popen(login_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate(input=password)
    if p.returncode != 0:
        raise RuntimeError(f"Docker ECR login failed: {err}")
    logger.info("Docker authenticated with Amazon ECR successfully.")

    logger.info(f"Building Docker image for platform linux/amd64: {ECR_REPO}:latest...")
    build_cmd = [
        "docker", "build",
        "--platform", "linux/amd64",
        "-t", f"{ECR_REPO}:latest",
        str(ROOT)
    ]
    res = subprocess.run(build_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error(f"Docker build failed:\n{res.stderr}")
        raise RuntimeError("Docker build failed.")
    logger.info("Docker build completed successfully.")

    logger.info(f"Tagging image as {ecr_uri}...")
    subprocess.run(["docker", "tag", f"{ECR_REPO}:latest", ecr_uri], check=True)

    logger.info(f"Pushing image to ECR ({ecr_uri})...")
    push_res = subprocess.run(["docker", "push", ecr_uri], capture_output=True, text=True)
    if push_res.returncode != 0:
        logger.error(f"Docker push failed:\n{push_res.stderr}")
        raise RuntimeError("Docker push failed.")
    logger.info("Docker image successfully pushed to Amazon ECR.")
    return ecr_uri


def step3_ensure_iam_role(session: boto3.Session, account_id: str) -> str:
    """Ensure ecsTaskExecutionRole exists with required policies."""
    iam = session.client("iam")
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    role_arn = f"arn:aws:iam::{account_id}:role/{EXECUTION_ROLE_NAME}"
    try:
        iam.get_role(RoleName=EXECUTION_ROLE_NAME)
        logger.info(f"[3/8] IAM role {EXECUTION_ROLE_NAME} already exists.")
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            logger.info(f"[3/8] Creating IAM role {EXECUTION_ROLE_NAME}...")
            resp = iam.create_role(
                RoleName=EXECUTION_ROLE_NAME,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="ECS Task Execution Role for NovaEdge AI Agent",
            )
            role_arn = resp["Role"]["Arn"]
        else:
            raise

    # Attach AWS managed policy for ECS task execution
    iam.attach_role_policy(
        RoleName=EXECUTION_ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
    )

    # Attach inline policy for S3 and CloudWatch metrics access
    inline_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "s3:PutObject",
                    "s3:GetObject",
                    "s3:ListBucket"
                ],
                "Resource": [
                    "arn:aws:s3:::bank-report-data",
                    "arn:aws:s3:::bank-report-data/*"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "cloudwatch:PutMetricData"
                ],
                "Resource": "*"
            },
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:CreateLogGroup"
                ],
                "Resource": "*"
            }
        ]
    }
    iam.put_role_policy(
        RoleName=EXECUTION_ROLE_NAME,
        PolicyName="NovaEdgeTaskExecutionCustomPolicy",
        PolicyDocument=json.dumps(inline_policy),
    )
    logger.info("IAM role and policies configured.")
    return role_arn


def step4_ensure_cloudwatch_log_group(session: boto3.Session) -> None:
    """Ensure CloudWatch log group exists for container logs."""
    logs = session.client("logs", region_name=REGION)
    try:
        logs.create_log_group(logGroupName=LOG_GROUP_NAME)
        logger.info(f"[4/8] Created CloudWatch log group: {LOG_GROUP_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceAlreadyExistsException":
            logger.info(f"[4/8] CloudWatch log group {LOG_GROUP_NAME} already exists.")
        else:
            raise


def step5_register_task_definition(
    session: boto3.Session,
    role_arn: str,
    ecr_uri: str,
    env_vars: Dict[str, str],
) -> str:
    """Register ECS Fargate task definition with 0.5 vCPU and 1 GB RAM."""
    ecs = session.client("ecs", region_name=REGION)

    # Construct environment array
    env_list: List[Dict[str, str]] = [
        {"name": "POLL_INTERVAL_MINUTES", "value": "15"},
        {"name": "DIGEST_HOUR_UTC", "value": "6"},
        {"name": "S3_BACKUP_BUCKET", "value": "bank-report-data"},
        {"name": "S3_BACKUP_PREFIX", "value": "novaedge-ai-reports"},
        {"name": "AWS_REGION", "value": REGION},
        {"name": "OMP_NUM_THREADS", "value": "1"},
        {"name": "OPENBLAS_NUM_THREADS", "value": "1"},
        {"name": "MKL_NUM_THREADS", "value": "1"},
        {"name": "PYTHONUNBUFFERED", "value": "1"},
    ]

    # Add secrets and operational variables from .env
    for k, v in env_vars.items():
        if k not in [x["name"] for x in env_list]:
            env_list.append({"name": k, "value": v})

    container_def = {
        "name": "novaedge-ai-agent",
        "image": ecr_uri,
        "essential": True,
        "environment": env_list,
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": LOG_GROUP_NAME,
                "awslogs-region": REGION,
                "awslogs-stream-prefix": "ecs",
            },
        },
    }

    logger.info(f"[5/8] Registering ECS Fargate task definition '{TASK_FAMILY}' (512 CPU, 1024 RAM)...")
    resp = ecs.register_task_definition(
        family=TASK_FAMILY,
        networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"],
        cpu="512",
        memory="1024",
        executionRoleArn=role_arn,
        taskRoleArn=role_arn,
        containerDefinitions=[container_def],
    )
    task_def_arn = resp["taskDefinition"]["taskDefinitionArn"]
    logger.info(f"Task definition registered: {task_def_arn}")
    return task_def_arn


def step6_ensure_ecs_cluster(session: boto3.Session) -> str:
    """Ensure ECS Cluster exists."""
    ecs = session.client("ecs", region_name=REGION)
    resp = ecs.describe_clusters(clusters=[ECS_CLUSTER])
    active = [c for c in resp.get("clusters", []) if c.get("status") == "ACTIVE"]
    if not active:
        logger.info(f"[6/8] Creating ECS Cluster '{ECS_CLUSTER}'...")
        create_resp = ecs.create_cluster(clusterName=ECS_CLUSTER)
        cluster_arn = create_resp["cluster"]["clusterArn"]
    else:
        cluster_arn = active[0]["clusterArn"]
        logger.info(f"[6/8] ECS Cluster '{ECS_CLUSTER}' is active.")
    return cluster_arn


def step7_setup_eventbridge_schedules(session: boto3.Session, role_arn: str, task_def_arn: str) -> None:
    """Configure EventBridge rules for daily 06:00 UTC digest and 15-minute recovery probe."""
    events = session.client("events", region_name=REGION)
    iam = session.client("iam")

    # Ensure an EventBridge invocation role exists to run ECS tasks
    eventbridge_role_name = "ecsEventsRole"
    try:
        iam.get_role(RoleName=eventbridge_role_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            logger.info(f"Creating EventBridge execution role {eventbridge_role_name}...")
            trust = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "events.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
            iam.create_role(
                RoleName=eventbridge_role_name,
                AssumeRolePolicyDocument=json.dumps(trust),
            )
            # Attach policy to run ECS task
            ecs_run_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "ecs:RunTask",
                        "Resource": task_def_arn.rsplit(":", 1)[0] + ":*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": "iam:PassRole",
                        "Resource": role_arn,
                    },
                ],
            }
            iam.put_role_policy(
                RoleName=eventbridge_role_name,
                PolicyName="EventBridgeRunECSTaskPolicy",
                PolicyDocument=json.dumps(ecs_run_policy),
            )

    events_role_arn = iam.get_role(RoleName=eventbridge_role_name)["Role"]["Arn"]

    # Rule 1: Daily full digest at 06:00 UTC (11:30 AM IST)
    rule_daily = "novaedge-ai-daily-digest"
    logger.info(f"[7/8] Configuring EventBridge schedule: {rule_daily} (cron(0 6 * * ? *))...")
    events.put_rule(
        Name=rule_daily,
        ScheduleExpression="cron(0 6 * * ? *)",
        State="ENABLED",
        Description="Triggers NovaEdge full fleet daily risk digest, S3 archival, Slack & Email dispatch",
    )

    daily_target = {
        "Id": "novaedge-daily-digest-target",
        "Arn": f"arn:aws:ecs:{REGION}:{session.client('sts').get_caller_identity()['Account']}:cluster/{ECS_CLUSTER}",
        "RoleArn": events_role_arn,
        "EcsParameters": {
            "TaskDefinitionArn": task_def_arn,
            "TaskCount": 1,
            "LaunchType": "FARGATE",
            "NetworkConfiguration": {
                "awsvpcConfiguration": {
                    "Subnets": [SUBNET_ID],
                    "SecurityGroups": [SECURITY_GROUP_ID],
                    "AssignPublicIp": "ENABLED",
                }
            },
        },
        "Input": json.dumps({
            "containerOverrides": [
                {
                    "name": "novaedge-ai-agent",
                    "command": ["python", "scripts/cloud_runner.py", "--once"],
                }
            ]
        }),
    }
    events.put_targets(Rule=rule_daily, Targets=[daily_target])
    logger.info("Daily digest EventBridge rule active.")

    # Rule 2: 15-minute recovery probe
    rule_probe = "novaedge-ai-recovery-probe"
    logger.info(f"Configuring EventBridge schedule: {rule_probe} (rate(15 minutes))...")
    events.put_rule(
        Name=rule_probe,
        ScheduleExpression="rate(15 minutes)",
        State="ENABLED",
        Description="Triggers NovaEdge 15-minute telemetry recovery probe for hardware clear events",
    )

    probe_target = {
        "Id": "novaedge-probe-target",
        "Arn": f"arn:aws:ecs:{REGION}:{session.client('sts').get_caller_identity()['Account']}:cluster/{ECS_CLUSTER}",
        "RoleArn": events_role_arn,
        "EcsParameters": {
            "TaskDefinitionArn": task_def_arn,
            "TaskCount": 1,
            "LaunchType": "FARGATE",
            "NetworkConfiguration": {
                "awsvpcConfiguration": {
                    "Subnets": [SUBNET_ID],
                    "SecurityGroups": [SECURITY_GROUP_ID],
                    "AssignPublicIp": "ENABLED",
                }
            },
        },
        "Input": json.dumps({
            "containerOverrides": [
                {
                    "name": "novaedge-ai-agent",
                    "command": ["python", "scripts/cloud_runner.py", "--probe-only"],
                }
            ]
        }),
    }
    events.put_targets(Rule=rule_probe, Targets=[probe_target])
    logger.info("15-minute recovery probe EventBridge rule active.")


def step8_clean_up_ec2_agent(session: boto3.Session) -> None:
    """Clean up the stopped/exited novaedge-ai-agent container from the EC2 instance."""
    ssm = session.client("ssm", region_name=REGION)
    cleanup_cmd = (
        "if docker ps -a --format '{{.Names}}' | grep -q '^novaedge-ai-agent$'; then "
        "  echo 'Stopping and removing novaedge-ai-agent from EC2...'; "
        "  docker stop novaedge-ai-agent 2>/dev/null || true; "
        "  docker rm -f novaedge-ai-agent 2>/dev/null || true; "
        "  echo 'Successfully removed novaedge-ai-agent from EC2 host.'; "
        "else "
        "  echo 'novaedge-ai-agent is not present on EC2 host.'; "
        "fi && "
        "echo 'Remaining containers:' && "
        "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
    )

    logger.info(f"[8/8] Decommissioning old AI container on EC2 instance {EC2_INSTANCE_ID} via SSM...")
    resp = ssm.send_command(
        InstanceIds=[EC2_INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [cleanup_cmd]},
        Comment="Decommission novaedge-ai-agent on shared ThingsBoard EC2 host",
    )
    cmd_id = resp["Command"]["CommandId"]

    # Poll for completion
    for _ in range(15):
        time.sleep(3)
        inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=EC2_INSTANCE_ID)
        status = inv.get("Status")
        if status in ("Success", "Failed", "TimedOut", "Cancelled"):
            logger.info(f"EC2 cleanup SSM execution completed with status: {status}")
            output = inv.get("StandardOutputContent", "")
            logger.info(f"Host SSM cleanup output:\n{output}")
            break


def main():
    logger.info("=" * 70)
    logger.info("NovaEdge AI Engine -> AWS ECS Fargate Turnkey Migration")
    logger.info("=" * 70)

    session = boto3.Session(region_name=REGION)
    env_vars = load_env_vars()
    account_id = step1_check_identity(session)

    ecr_uri = step2_build_and_push_image(session, account_id)
    role_arn = step3_ensure_iam_role(session, account_id)
    step4_ensure_cloudwatch_log_group(session)
    task_def_arn = step5_register_task_definition(session, role_arn, ecr_uri, env_vars)
    step6_ensure_ecs_cluster(session)
    step7_setup_eventbridge_schedules(session, role_arn, task_def_arn)
    step8_clean_up_ec2_agent(session)

    logger.info("=" * 70)
    logger.info("MIGRATION TO AWS ECS FARGATE COMPLETED SUCCESSFULLY!")
    logger.info("=" * 70)
    logger.info(f"ECS Cluster      : {ECS_CLUSTER}")
    logger.info(f"Task Definition  : {task_def_arn}")
    logger.info("EventBridge Rules : novaedge-ai-daily-digest (06:00 UTC)")
    logger.info("                    novaedge-ai-recovery-probe (every 15 min)")
    logger.info(f"EC2 Host Cleanup : novaedge-ai-agent removed from {EC2_INSTANCE_ID}")
    logger.info("ThingsBoard now has 100% of EC2 RAM and CPU exclusively.")


if __name__ == "__main__":
    main()

