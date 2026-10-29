"""scripts/cloud/diagnose_ec2_slack.py
Diagnose Slack webhook configuration and logs on AWS EC2 novaedge-dev.
"""

import sys
import time
import boto3

sys.stdout.reconfigure(encoding="utf-8")

def diagnose_ec2():
    ssm = boto3.client("ssm", region_name="ap-south-1")
    instance_id = "i-064834c54249ba9b7"

    commands = [
        "echo '=== Docker Container Env ==='",
        "docker exec novaedge-ai-agent python -c 'import os; print(\"SLACK_WEBHOOK present:\", bool(os.getenv(\"SLACK_WEBHOOK\")), \"Length:\", len(os.getenv(\"SLACK_WEBHOOK\", \"\")), \"Preview:\", os.getenv(\"SLACK_WEBHOOK\", \"\")[:30])'",
        "echo '=== Host /opt/novaedge-ai/.env ==='",
        "grep -E 'SLACK_WEBHOOK' /opt/novaedge-ai/.env || echo 'No SLACK_WEBHOOK in host .env'",
        "echo '=== Docker Compose Env check ==='",
        "grep -E 'SLACK_WEBHOOK' /opt/novaedge-ai/docker-compose.yml || echo 'No SLACK_WEBHOOK in docker-compose.yml'",
        "echo '=== Recent Slack Logs in Container ==='",
        "docker logs --tail 100 novaedge-ai-agent | grep -i slack || echo 'No slack lines in recent 100 logs'",
        "echo '=== Direct Slack Test inside Docker Container ==='",
        "docker exec novaedge-ai-agent python -c 'import os, requests; url = os.getenv(\"SLACK_WEBHOOK\"); print(\"Testing container URL:\", url[:30] if url else \"None\"); resp = requests.post(url, json={\"text\": \"Automated diagnostic probe from EC2 Docker container\"}, timeout=10) if url else None; print(\"Response code:\", resp.status_code if resp else \"No URL\", \"Text:\", resp.text if resp else \"\")'"
    ]

    response = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Comment="Diagnose Slack configuration",
        Parameters={"commands": commands}
    )
    cmd_id = response["Command"]["CommandId"]
    print(f"Sent SSM command {cmd_id}, waiting for execution...")
    time.sleep(8)

    inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
    print("=" * 70)
    print(f"Status: {inv.get('Status')} | ResponseCode: {inv.get('ResponseCode')}")
    print("=" * 70)
    print(inv.get("StandardOutputContent", ""))
    if inv.get("StandardErrorContent"):
        print("STDERR:\n", inv.get("StandardErrorContent"))
    print("=" * 70)

if __name__ == "__main__":
    diagnose_ec2()
