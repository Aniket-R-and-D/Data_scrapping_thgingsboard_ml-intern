"""Inspect live container status and logs on AWS EC2 novaedge-dev."""

import json
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

def check_ec2():
    # 1. Send command
    cmd_input = {
        "InstanceIds": ["i-064834c54249ba9b7"],
        "DocumentName": "AWS-RunShellScript",
        "Comment": "Check container status and logs",
        "Parameters": {
            "commands": [
                "docker ps --filter name=novaedge-ai-agent",
                "docker logs --tail 30 novaedge-ai-agent"
            ]
        }
    }
    
    import os
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["AWS_CLI_FILE_ENCODING"] = "UTF-8"

    send_res = subprocess.run(
        ["aws", "ssm", "send-command", "--cli-input-json", json.dumps(cmd_input)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env
    )
    if send_res.returncode != 0:
        print("Failed to send command:", send_res.stderr)
        return

    cmd_id = json.loads(send_res.stdout)["Command"]["CommandId"]
    print(f"Sent SSM command {cmd_id}, waiting for execution...")

    import time
    time.sleep(6)

    # 2. Get invocation
    get_res = subprocess.run(
        ["aws", "ssm", "get-command-invocation", "--command-id", cmd_id, "--instance-id", "i-064834c54249ba9b7"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env
    )
    if get_res.returncode != 0:
        print("Failed to get invocation:", get_res.stderr)
        return

    data = json.loads(get_res.stdout)
    print("=" * 70)
    print("AWS EC2 LIVE CONTAINER STATUS & LOGS")
    print(f"Status: {data.get('Status')} | ResponseCode: {data.get('ResponseCode')}")
    print("=" * 70)
    print(data.get("StandardOutputContent", ""))
    if data.get("StandardErrorContent"):
        print("STDERR:")
        print(data.get("StandardErrorContent", ""))
    print("=" * 70)

if __name__ == "__main__":
    check_ec2()
