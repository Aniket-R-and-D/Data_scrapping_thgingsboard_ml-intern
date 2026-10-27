"""Inspect live container status and logs on AWS EC2 novaedge-dev."""

import json
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

def check_ec2():
    import boto3
    import time

    ssm = boto3.client("ssm", region_name="ap-south-1")
    instance_id = "i-064834c54249ba9b7"

    response = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Comment="Check container status and logs",
        Parameters={
            "commands": [
                "docker ps --filter name=novaedge-ai-agent",
                "docker logs --tail 25 novaedge-ai-agent"
            ]
        }
    )
    cmd_id = response["Command"]["CommandId"]
    print(f"Sent SSM command {cmd_id}, waiting for execution...")
    time.sleep(6)

    inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
    print("=" * 70)
    print("AWS EC2 LIVE CONTAINER STATUS & LOGS")
    print(f"Status: {inv.get('Status')} | ResponseCode: {inv.get('ResponseCode')}")
    print("=" * 70)
    stdout_content = inv.get("StandardOutputContent", "")
    sys.stdout.buffer.write((stdout_content + "\n").encode("utf-8", errors="replace"))
    if inv.get("StandardErrorContent"):
        print("STDERR:")
        sys.stdout.buffer.write((inv.get("StandardErrorContent", "") + "\n").encode("utf-8", errors="replace"))
    print("=" * 70)

if __name__ == "__main__":
    check_ec2()
