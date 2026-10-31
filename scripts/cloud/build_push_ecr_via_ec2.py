"""scripts/cloud/build_push_ecr_via_ec2.py
Build and push latest NovaEdge AI production Docker image to ECR via EC2 without disrupting running containers.
"""
import sys
import time
import boto3

sys.stdout.reconfigure(encoding="utf-8")

def main():
    ssm = boto3.client("ssm", region_name="ap-south-1")
    instance_id = "i-064834c54249ba9b7"

    commands = [
        "set -e",
        "echo '=== Step 1: Update repository code ==='",
        "cd /opt/novaedge-ai",
        "git fetch origin main",
        "git reset --hard origin/main",
        "git log -1 --oneline",
        "echo '=== Step 2: Authenticate Docker with Amazon ECR ==='",
        "ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)",
        "aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin ${ACCOUNT_ID}.dkr.ecr.ap-south-1.amazonaws.com",
        "echo '=== Step 3: Build Linux/amd64 Docker Image ==='",
        "docker build -t novaedge-ai-agent:latest /opt/novaedge-ai",
        "echo '=== Step 4: Tag & Push to ECR ==='",
        "docker tag novaedge-ai-agent:latest ${ACCOUNT_ID}.dkr.ecr.ap-south-1.amazonaws.com/novaedge-ai-agent:latest",
        "docker push ${ACCOUNT_ID}.dkr.ecr.ap-south-1.amazonaws.com/novaedge-ai-agent:latest",
        "echo '=== BUILD AND ECR PUSH COMPLETED SUCCESSFULLY ==='",
        "docker ps --format 'table {{.Names}}\\t{{.Status}}'"
    ]

    print("Submitting SSM build and push command to EC2...")
    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Comment="Build and push updated NovaEdge AI image to ECR",
        Parameters={"commands": commands}
    )
    cmd_id = resp["Command"]["CommandId"]
    print(f"SSM Command ID: {cmd_id}. Polling execution progress...")

    for i in range(60):
        time.sleep(5)
        inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
        status = inv.get("Status")
        print(f"[{i*5:3d}s] Status: {status}")
        if status in ("Success", "Failed", "TimedOut", "Cancelled"):
            print("=" * 70)
            print(f"Final Status: {status} | Response Code: {inv.get('ResponseCode')}")
            print("=" * 70)
            print(inv.get("StandardOutputContent", ""))
            if inv.get("StandardErrorContent"):
                print("STDERR:")
                print(inv.get("StandardErrorContent", ""))
            if status != "Success":
                sys.exit(1)
            return

    print("Command polling timed out after 300s.")
    sys.exit(1)

if __name__ == "__main__":
    main()
