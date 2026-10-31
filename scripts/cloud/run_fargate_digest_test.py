"""scripts/cloud/run_fargate_digest_test.py
Trigger a dry-run ECS Fargate task of send_daily_risk_digest.py to verify full cloud execution and CloudWatch logs.
"""
import sys
import time
import boto3

sys.stdout.reconfigure(encoding="utf-8")

def main():
    ecs = boto3.client("ecs", region_name="ap-south-1")
    logs = boto3.client("logs", region_name="ap-south-1")

    cluster = "novaedge-ai-cluster"
    task_def = "novaedge-ai-agent-task"
    subnets = ["subnet-0800a34e9e2b3cf20"]
    sgs = ["sg-028ce8a8a8ef1d5e8"]

    print("Launching test ECS Fargate task: send_daily_risk_digest.py --dry-run...")
    resp = ecs.run_task(
        cluster=cluster,
        taskDefinition=task_def,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": subnets,
                "securityGroups": sgs,
                "assignPublicIp": "ENABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": "novaedge-ai-agent",
                    "command": ["python", "send_daily_risk_digest.py", "--dry-run"],
                }
            ]
        },
    )

    task_arn = resp["tasks"][0]["taskArn"]
    task_id = task_arn.split("/")[-1]
    print(f"Task launched: {task_id}. Polling execution...")

    for i in range(35):
        time.sleep(4)
        desc = ecs.describe_tasks(cluster=cluster, tasks=[task_arn])
        task = desc["tasks"][0]
        status = task["lastStatus"]
        print(f"[{i*4:2d}s] Task Status: {status}")
        if status == "STOPPED":
            containers = task.get("containers", [])
            exit_code = containers[0].get("exitCode", "Unknown") if containers else "Unknown"
            print("=" * 70)
            print(f"Fargate Task Finished. Exit Code: {exit_code}")
            print("=" * 70)

            # Fetch CloudWatch logs
            try:
                log_stream = f"ecs/novaedge-ai-agent/{task_id}"
                print(f"Fetching logs from CloudWatch stream: {log_stream}...")
                log_resp = logs.get_log_events(
                    logGroupName="/ecs/novaedge-ai-agent-task",
                    logStreamName=log_stream,
                    startFromHead=True
                )
                events = log_resp.get("events", [])
                for ev in events[-30:]:
                    print(ev.get("message", "").strip())
            except Exception as e:
                print(f"Could not retrieve CloudWatch logs: {e}")

            if exit_code == 0:
                print("\n✅ LIVE ECS FARGATE END-TO-END EXECUTION VERIFIED!")
                return
            else:
                sys.exit(1)

    print("Task timed out.")
    sys.exit(1)

if __name__ == "__main__":
    main()
