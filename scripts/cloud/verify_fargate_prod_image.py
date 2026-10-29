"""scripts/cloud/verify_fargate_prod_image.py
Verify that the newly pushed production image runs cleanly on AWS ECS Fargate.
"""
from __future__ import annotations

import time
import boto3

def main():
    session = boto3.Session(region_name="ap-south-1")
    ecs = session.client("ecs")

    cluster = "novaedge-ai-cluster"
    task_def = "novaedge-ai-agent-task"
    subnets = ["subnet-0800a34e9e2b3cf20"]
    sgs = ["sg-028ce8a8a8ef1d5e8"]

    print("Triggering ECS Fargate verification task with latest production image...")
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
                    "command": [
                        "python",
                        "-c",
                        "import lightgbm, sklearn, joblib, requests; print('PROD_CONTAINER_VERIFIED_ON_FARGATE')",
                    ],
                }
            ]
        },
    )

    task_arn = resp["tasks"][0]["taskArn"]
    task_id = task_arn.split("/")[-1]
    print(f"Task launched: {task_id}")

    for _ in range(25):
        time.sleep(4)
        desc = ecs.describe_tasks(cluster=cluster, tasks=[task_arn])
        task = desc["tasks"][0]
        status = task["lastStatus"]
        print(f"Status: {status}")
        if status == "STOPPED":
            containers = task.get("containers", [])
            exit_code = containers[0].get("exitCode", "Unknown") if containers else "Unknown"
            print(f"Task stopped with container exit code: {exit_code}")
            if exit_code == 0:
                print("ECS FARGATE PRODUCTION CONTAINER RUN CONFIRMED SUCCESSFUL (EXIT 0).")
            else:
                raise RuntimeError(f"Fargate task failed with exit code: {exit_code}")
            return

    raise TimeoutError("Fargate verification task timed out.")

if __name__ == "__main__":
    main()
