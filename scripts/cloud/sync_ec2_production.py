"""Synchronize AWS EC2 production instance with latest git commit and restart container."""

import json
import sys
import time
import boto3

sys.stdout.reconfigure(encoding="utf-8")

def sync_ec2():
    ssm = boto3.client("ssm", region_name="ap-south-1")
    instance_id = "i-064834c54249ba9b7"

    commands = [
        "set -e",
        "echo '=== Updating /opt/novaedge-ai from GitHub ==='",
        "cd /opt/novaedge-ai",
        "git fetch origin main",
        "git reset --hard origin/main",
        "chmod -R 777 /opt/novaedge-ai/reports /opt/novaedge-ai/models",
        "echo '=== Latest Git Commit ==='",
        "git log -1 --oneline",
        "echo '=== Restarting Docker Containers ==='",
        "docker compose down",
        "docker compose up -d --build",
        "sleep 8",
        "echo '=== Docker Container Status ==='",
        "docker ps --filter name=novaedge-ai-agent",
        "echo '=== Recent Logs ==='",
        "docker logs --tail 20 novaedge-ai-agent"
    ]

    print(f"Sending SSM command to {instance_id}...")
    response = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Comment="Sync modernized dashboard and restart novaedge-ai-agent",
        Parameters={"commands": commands}
    )
    cmd_id = response["Command"]["CommandId"]
    print(f"Sent SSM command {cmd_id}, waiting for completion...")

    # Wait up to 120 seconds for docker build and startup
    for _ in range(24):
        time.sleep(5)
        inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
        status = inv.get("Status")
        if status in ("Success", "Failed", "Cancelled", "TimedOut"):
            break

    print("=" * 70)
    print(f"SSM Execution Status: {status} | ResponseCode: {inv.get('ResponseCode')}")
    print("=" * 70)
    print(inv.get("StandardOutputContent", ""))
    if inv.get("StandardErrorContent"):
        print("STDERR:")
        print(inv.get("StandardErrorContent", ""))
    print("=" * 70)

if __name__ == "__main__":
    sync_ec2()
