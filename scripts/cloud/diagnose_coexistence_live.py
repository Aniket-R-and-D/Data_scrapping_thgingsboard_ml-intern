#!/usr/bin/env python3
"""
diagnose_coexistence_live.py
----------------------------
Executes a non-destructive, comprehensive diagnostic audit of the co-hosted
ThingsBoard container (novaedge-dev) and AI Engine container (novaedge-ai-agent)
on AWS EC2 instance i-064834c54249ba9b7 in region ap-south-1.

Dispatches 10 structured diagnostic command blocks via AWS Systems Manager (SSM)
AWS-RunShellScript, capturing:
  1. Host Identity & Uptime
  2. Host Memory & Swap Pressure
  3. Host Disk Storage & I/O Status
  4. Container Process Inventory
  5. Container Resource Utilization (docker stats)
  6. Cgroup Limits & Lifecycle Metadata
  7. Network Port Bindings & Listening Sockets
  8. Kernel OOM Killer & System Faults
  9. Application Log Correlation (ThingsBoard vs AI Agent)
 10. Local ThingsBoard Connectivity Probes (HTTP 8080 & MQTT 1883/8883)

Outputs are bounded to remain safely within SSM buffer limits, recorded locally
in reports/, and parsed into structured diagnostic summaries.
"""

import argparse
import datetime
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, List, Optional

# Reconfigure stdout for UTF-8 on Windows environments
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DIAGNOSTIC_COMMAND_BLOCKS = [
    r"""#!/bin/bash
set -u

echo "==================== [1. HOST IDENTITY & UPTIME] ===================="
uptime
uname -a
cat /etc/os-release | grep -E 'PRETTY_NAME|VERSION_ID'

echo "==================== [2. HOST MEMORY & SWAP PRESSURE] ===================="
free -m
grep -E 'MemTotal|MemFree|MemAvailable|SwapTotal|SwapFree|Cached|Buffers|CommitLimit|Committed_AS' /proc/meminfo
vmstat 1 3
[ -f /proc/pressure/memory ] && cat /proc/pressure/memory || true

echo "==================== [3. HOST DISK STORAGE & I/O STATUS] ===================="
df -h / /var/lib/docker
df -i / /var/lib/docker
iostat -xz 1 2 2>/dev/null || cat /proc/diskstats | head -n 10

echo "==================== [4. CONTAINER PROCESS INVENTORY] ===================="
docker ps -a --format "table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}\t{{.Image}}"

echo "==================== [5. CONTAINER RESOURCE UTILIZATION (STATS)] ===================="
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}\t{{.PIDs}}"

echo "==================== [6. CGROUP LIMITS & LIFECYCLE METADATA] ===================="
echo "--- novaedge-dev ---"
docker inspect novaedge-dev --format 'Status: {{.State.Status}} | Running: {{.State.Running}} | Restarts: {{.RestartCount}} | ExitCode: {{.State.ExitCode}} | OOMKilled: {{.State.OOMKilled}} | StartedAt: {{.State.StartedAt}} | FinishedAt: {{.State.FinishedAt}} | MemoryLimit: {{.HostConfig.Memory}} | MemorySwap: {{.HostConfig.MemorySwap}} | CpuQuota: {{.HostConfig.CpuQuota}} | CpuPeriod: {{.HostConfig.CpuPeriod}} | CpusetCpus: {{.HostConfig.CpusetCpus}}' 2>&1 || echo 'novaedge-dev inspect failed'

echo "--- novaedge-ai-agent ---"
docker inspect novaedge-ai-agent --format 'Status: {{.State.Status}} | Running: {{.State.Running}} | Restarts: {{.RestartCount}} | ExitCode: {{.State.ExitCode}} | OOMKilled: {{.State.OOMKilled}} | StartedAt: {{.State.StartedAt}} | FinishedAt: {{.State.FinishedAt}} | MemoryLimit: {{.HostConfig.Memory}} | MemorySwap: {{.HostConfig.MemorySwap}} | CpuQuota: {{.HostConfig.CpuQuota}} | CpuPeriod: {{.HostConfig.CpuPeriod}} | CpusetCpus: {{.HostConfig.CpusetCpus}}' 2>&1 || echo 'novaedge-ai-agent inspect failed'

echo "==================== [7. NETWORK PORT BINDINGS & LISTENING SOCKETS] ===================="
echo "--- Docker Network & Ports ---"
docker inspect novaedge-dev novaedge-ai-agent --format '{{.Name}}: NetMode={{.HostConfig.NetworkMode}} | Ports={{json .NetworkSettings.Ports}}' 2>&1 || echo 'Port inspect failed'
echo "--- Host Listening Sockets ---"
ss -tulpn | grep -E ':(8080|1883|8883|5432|9042)' || netstat -tulpn 2>/dev/null | grep -E ':(8080|1883|8883|5432)'

echo "==================== [8. KERNEL OOM KILLER & SYSTEM FAULTS] ===================="
echo "--- Dmesg OOM Events ---"
dmesg -T 2>/dev/null | grep -iE 'oom[-_]killer|out of memory|killed process' | tail -n 25 || echo 'No OOM events found in dmesg'
echo "--- Docker Daemon Status ---"
systemctl is-active docker 2>/dev/null && echo "Docker daemon is active" || echo "Docker daemon inactive"

echo "==================== [9. APPLICATION LOG CORRELATION] ===================="
echo "--- novaedge-dev Anomaly Patterns (Last 200 lines) ---"
docker logs --tail 200 novaedge-dev 2>&1 | grep -iE 'OutOfMemory|heap space|Direct buffer|HikariPool|Connection pool|StacklessClosedChannel|TbRateLimits|PSQLException|FATAL' | tail -n 30 || echo 'No critical error signatures in recent novaedge-dev logs'
echo "--- novaedge-ai-agent Recent Logs (Last 50 lines) ---"
docker logs --tail 50 novaedge-ai-agent 2>&1 | tail -n 30 || echo 'No recent novaedge-ai-agent logs'

echo "==================== [10. LOCAL THINGSBOARD CONNECTIVITY PROBES] ===================="
echo "--- HTTP Port 8080 Web UI Probe ---"
curl -s -o /dev/null -w "HTTP_CODE: %{http_code} | CONNECT_TIME: %{time_connect}s | TOTAL_TIME: %{time_total}s\n" http://localhost:8080/login || echo "HTTP Web UI probe failed"
echo "--- HTTP Health / Password Policy API Probe ---"
curl -s -w "\nHEALTH_API_HTTP_CODE: %{http_code} | TIME: %{time_total}s\n" http://localhost:8080/api/noauth/userPasswordPolicy || echo "Health API probe failed"
echo "--- MQTT Port 1883 Layer 4 Socket ---"
(timeout 3 bash -c "</dev/tcp/127.0.0.1/1883" 2>/dev/null && echo "Port 1883 TCP Socket: OPEN") || echo "Port 1883 TCP Socket: CLOSED/FAILED"
echo "--- MQTT Port 8883 Layer 4 Socket ---"
(timeout 3 bash -c "</dev/tcp/127.0.0.1/8883" 2>/dev/null && echo "Port 8883 MQTTS Socket: OPEN") || echo "Port 8883 MQTTS Socket: CLOSED/FAILED"
echo "--- MQTT Port 1883 Layer 7 Protocol CONNACK Handshake ---"
python3 -c "import socket, time; s = socket.socket(); s.settimeout(3.0); t0 = time.time(); s.connect(('127.0.0.1', 1883)); s.sendall(b'\x10\x14\x00\x04MQTT\x04\x02\x00\x3c\x00\x08diag_tb1'); b = s.recv(4); s.close(); print(f'MQTT Layer 7 CONNACK: {b.hex()} | Latency: {time.time()-t0:.4f}s' if len(b) >= 4 else 'MQTT Layer 7 Python probe failed')" 2>/dev/null || echo "MQTT Layer 7 Python probe failed"
"""
]


def _parse_int(val: Any) -> Optional[int]:
    """
    Parse memory strings into integer Megabytes (MB) with unit/scale resilience.

    Handles:
      - Gigabytes ('4.0Gi', '4Gi', '4.0G', '4GB') -> multiplied by 1024
      - Megabytes ('1953Mi', '3833M', '1953MB', '3833') -> 1:1 MB
      - Kilobytes ('1024Ki', '2048K', '1024KB', '512Ki') -> divided by 1024
      - Terabytes ('1.0Ti', '1T') -> multiplied by 1048576
      - Native numeric types (int, float) -> rounded integer
      - Guards against None, booleans, collections, and corrupted strings -> None
    """
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return int(round(val))
    if not isinstance(val, str):
        return None

    val_str = val.strip()
    if not val_str:
        return None

    # Match numeric part (int or decimal float) and optional unit suffix
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)?\s*$", val_str)
    if not m:
        return None

    try:
        num = float(m.group(1))
    except (ValueError, TypeError):
        return None

    unit = (m.group(2) or "").upper()

    if unit and not any(u in unit for u in ("T", "G", "M", "K")):
        return None

    if "T" in unit:
        num *= 1048576.0
    elif "G" in unit:
        num *= 1024.0
    elif "M" in unit:
        num *= 1.0
    elif "K" in unit:
        num /= 1024.0
    # If no unit suffix is present (standard free -m output), default unit is MB

    if 0 < num < 1:
        return int(math.ceil(num))
    return int(round(num))


def parse_diagnostic_output(raw_text: str) -> Dict[str, Any]:
    """Parse raw SSM output into structured sections."""
    sections: Dict[str, str] = {}
    current_section = "PREAMBLE"
    buffer: List[str] = []

    pattern = re.compile(r"^==================== \[(\d+\..+?)\] ====================$")

    for line in raw_text.splitlines():
        match = pattern.match(line.strip())
        if match:
            if buffer:
                sections[current_section] = "\n".join(buffer).strip()
                buffer = []
            current_section = match.group(1)
        else:
            buffer.append(line)
    if buffer:
        sections[current_section] = "\n".join(buffer).strip()

    # Extract high-level summary indicators
    summary = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "parsed_sections": list(sections.keys()),
        "host_memory": {},
        "containers": [],
        "port_bindings": {},
        "tb_probes": {},
        "oom_events_found": False,
    }

    sec2 = sections.get("2. HOST MEMORY & SWAP PRESSURE", "")
    for line in sec2.splitlines():
        try:
            if line.startswith("Mem:"):
                parts = line.split()
                if len(parts) >= 7:
                    summary["host_memory"] = {
                        "total_mb": _parse_int(parts[1]),
                        "used_mb": _parse_int(parts[2]),
                        "free_mb": _parse_int(parts[3]),
                        "shared_mb": _parse_int(parts[4]),
                        "buff_cache_mb": _parse_int(parts[5]),
                        "available_mb": _parse_int(parts[6]),
                    }
            elif line.startswith("Swap:"):
                parts = line.split()
                if len(parts) >= 4:
                    summary["host_memory"]["swap_total_mb"] = _parse_int(parts[1])
                    summary["host_memory"]["swap_used_mb"] = _parse_int(parts[2])
                    summary["host_memory"]["swap_free_mb"] = _parse_int(parts[3])
        except (ValueError, IndexError):
            pass

    # Check OOM events in section 8
    sec8 = sections.get("8. KERNEL OOM KILLER & SYSTEM FAULTS", "")
    if "oom-killer" in sec8.lower() or "killed process" in sec8.lower():
        summary["oom_events_found"] = True

    # Parse local probes in section 10
    sec10 = sections.get("10. LOCAL THINGSBOARD CONNECTIVITY PROBES", "")
    for line in sec10.splitlines():
        if "HTTP_CODE:" in line:
            summary["tb_probes"]["web_ui"] = line.strip()
        elif "HEALTH_API_HTTP_CODE:" in line:
            summary["tb_probes"]["health_api"] = line.strip()
        elif "Port 1883 TCP Socket:" in line:
            summary["tb_probes"]["mqtt_1883_tcp"] = line.strip()
        elif "Port 8883 MQTTS Socket:" in line:
            summary["tb_probes"]["mqtt_8883_tcp"] = line.strip()
        elif "MQTT Layer 7 CONNACK:" in line:
            summary["tb_probes"]["mqtt_1883_l7"] = line.strip()

    return {"summary": summary, "sections": sections}


def run_diagnostics(
    instance_id: str = "i-064834c54249ba9b7",
    region: str = "ap-south-1",
    timeout_seconds: int = 90,
    poll_interval: float = 4.0,
    output_dir: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Execute live diagnostic probe via AWS SSM or dry-run inspection."""
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    print(f"[DIAGNOSE] Target Instance: {instance_id} ({region})")
    print(f"[DIAGNOSE] Polling timeout: {timeout_seconds}s | Interval: {poll_interval}s")

    if dry_run:
        print("[DIAGNOSE] Dry-run enabled. Displaying diagnostic shell payload:\n")
        print(DIAGNOSTIC_COMMAND_BLOCKS[0])
        return {"dry_run": True, "payload": DIAGNOSTIC_COMMAND_BLOCKS[0]}

    ssm = boto3.client("ssm", region_name=region)

    try:
        # First verify instance ping status
        info_resp = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
        )
        instances = info_resp.get("InstanceInformationList", [])
        if not instances:
            raise RuntimeError(f"Instance {instance_id} not found in SSM inventory.")
        inst_info = instances[0]
        ping_status = inst_info.get("PingStatus", "Unknown")
        print(f"[DIAGNOSE] SSM Agent Ping Status: {ping_status} (Agent v{inst_info.get('AgentVersion')})")
        if ping_status != "Online":
            raise RuntimeError(f"SSM Agent on {instance_id} is {ping_status}, expected Online.")

        # Dispatch command
        print(f"[DIAGNOSE] Dispatching AWS-RunShellScript (10 inspection blocks)...")
        send_resp = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Comment="Comprehensive ThingsBoard & AI Engine Coexistence Audit",
            Parameters={"commands": DIAGNOSTIC_COMMAND_BLOCKS},
            TimeoutSeconds=timeout_seconds,
        )
        cmd_id = send_resp["Command"]["CommandId"]
        print(f"[DIAGNOSE] Command dispatched successfully. CommandId: {cmd_id}")

        # Polling loop
        start_time = time.time()
        inv_result: Optional[Dict[str, Any]] = None

        while (time.time() - start_time) < timeout_seconds:
            time.sleep(poll_interval)
            elapsed = time.time() - start_time
            inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
            status = inv.get("Status", "Pending")
            print(f"[DIAGNOSE] Polling SSM status (+{elapsed:.1f}s): {status}")

            if status in ("Success", "Failed", "TimedOut", "Cancelled"):
                inv_result = inv
                break

        if not inv_result:
            raise TimeoutError(f"SSM command {cmd_id} exceeded {timeout_seconds}s timeout.")

        status = inv_result.get("Status")
        response_code = inv_result.get("ResponseCode")
        stdout = inv_result.get("StandardOutputContent", "")
        stderr = inv_result.get("StandardErrorContent", "")

        print(f"[DIAGNOSE] Final Status: {status} (Response Code: {response_code})")
        if stderr:
            print(f"[DIAGNOSE] Stderr output captured ({len(stderr)} chars).")

        # Parse output
        parsed_data = parse_diagnostic_output(stdout)
        parsed_data["metadata"] = {
            "instance_id": instance_id,
            "region": region,
            "command_id": cmd_id,
            "status": status,
            "response_code": response_code,
            "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        # Write outputs to disk
        out_path = Path(output_dir or "reports")
        out_path.mkdir(parents=True, exist_ok=True)
        ts_slug = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")

        raw_file = out_path / f"diagnostics_raw_{ts_slug}.txt"
        with open(raw_file, "w", encoding="utf-8") as f:
            f.write(f"=== AWS SSM COMMAND INVOCATION METADATA ===\n")
            f.write(f"InstanceId: {instance_id}\n")
            f.write(f"Region: {region}\n")
            f.write(f"CommandId: {cmd_id}\n")
            f.write(f"Status: {status}\n")
            f.write(f"ResponseCode: {response_code}\n")
            f.write(f"Timestamp: {datetime.datetime.now(datetime.timezone.utc).isoformat()}\n\n")
            f.write(f"=== STDOUT ===\n")
            f.write(stdout)
            if stderr:
                f.write(f"\n\n=== STDERR ===\n")
                f.write(stderr)
        print(f"[DIAGNOSE] Raw diagnostic output saved to: {raw_file}")

        summary_file = out_path / f"diagnostics_summary_{ts_slug}.json"
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(parsed_data, f, indent=2)
        print(f"[DIAGNOSE] Structured diagnostic summary saved to: {summary_file}")

        return parsed_data

    except (BotoCoreError, ClientError) as e:
        print(f"[ERROR] AWS Boto3 error: {e}", file=sys.stderr)
        raise
    except Exception as e:
        print(f"[ERROR] Diagnostic execution failed: {e}", file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Comprehensive Coexistence Diagnostic Audit for EC2 novaedge-dev"
    )
    parser.add_argument(
        "--instance-id",
        default="i-064834c54249ba9b7",
        help="Target EC2 Instance ID (default: i-064834c54249ba9b7)",
    )
    parser.add_argument(
        "--region",
        default="ap-south-1",
        help="AWS Region (default: ap-south-1)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=90,
        help="SSM command wait timeout in seconds (default: 90)",
    )
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="Directory to save diagnostic artifacts (default: reports)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print diagnostic commands without dispatching to SSM",
    )
    parser.add_argument(
        "--json-stdout",
        action="store_true",
        help="Print structured JSON result to stdout",
    )

    args = parser.parse_args()

    results = run_diagnostics(
        instance_id=args.instance_id,
        region=args.region,
        timeout_seconds=args.timeout,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
    )

    if args.json_stdout and not args.dry_run:
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
