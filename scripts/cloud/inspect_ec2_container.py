"""scripts/cloud/inspect_ec2_container.py
Inspect stopped container and docker compose status on EC2.
"""

import sys
import time
import boto3

sys.stdout.reconfigure(encoding="utf-8")

def inspect_ec2():
    ssm = boto3.client("ssm", region_name="ap-south-1")
    instance_id = "i-064834c54249ba9b7"

    commands = [
        "echo '=== Docker ps -a ==='",
        "docker ps -a",
        "echo '=== Docker logs tail 100 ==='",
        "docker logs --tail 100 novaedge-ai-agent",
        "echo '=== Docker compose config ==='",
        "cd /opt/novaedge-ai && cat docker-compose.yml",
        "echo '=== System memory & disk ==='",
        "free -m",
        "df -h /"
    ]

    response = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Comment="Inspect stopped container",
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
    inspect_ec2()

