#!/usr/bin/env python3
"""
verify_tb_health_live.py
------------------------
Modular ThingsBoard Operational Health Verification CLI.
Probes HTTP 8080 Web UI / REST latency, Layer 4 TCP socket availability,
credential-independent Layer 7 MQTT 3.1.1 protocol handshake (CONNACK),
Layer 7 TLS negotiation on port 8883, and container error logs for
anomalies (JVM OOM, HikariCP pool, Netty channel drops, rate limiting).

Supports:
  1. Direct external network probing against public IP / domain.
  2. Remote on-host probe execution via AWS Systems Manager (SSM).
  3. Structured JSON output and formatted ANSI terminal summary.
"""

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import time
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request

# Reconfigure stdout for UTF-8 on Windows environments
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ANSI color codes
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


class ThingsBoardHealthVerifier:
    REMOTE_SSM_SCRIPT = r"""#!/bin/bash
set -u

echo "=== [TB_LOCAL_PROBES_START] ==="

echo "--- 1. HTTP 8080 Web UI ---"
curl -s -o /dev/null -w "HTTP_CODE:%{http_code}|CONNECT_TIME:%{time_connect}|TOTAL_TIME:%{time_total}\n" http://localhost:8080/login || echo "FAIL"

echo "--- 2. HTTP Health / Policy API ---"
curl -s -w "\nHEALTH_API_HTTP_CODE:%{http_code}|TOTAL_TIME:%{time_total}\n" http://localhost:8080/api/noauth/userPasswordPolicy || echo "FAIL"

echo "--- 3. Port Sockets ---"
ss -tulpn | grep -E ':(8080|1883|8883)' || echo "FAIL"

echo "--- 4. MQTT 1883 Protocol CONNACK ---"
python3 -c "import socket, time; s = socket.socket(); s.settimeout(3.0); t0 = time.time(); s.connect(('127.0.0.1', 1883)); s.sendall(b'\x10\x14\x00\x04MQTT\x04\x02\x00\x3c\x00\x08diag_tb1'); b = s.recv(4); s.close(); print(f'MQTT_CONNACK:{b.hex()}|TIME:{time.time()-t0:.4f}' if len(b) >= 4 else 'FAIL')" 2>/dev/null || echo "FAIL"

echo "--- 5. Container Lifecycle & Inspect ---"
docker inspect novaedge-dev --format 'Status: {{.State.Status}} | Restarts: {{.RestartCount}} | ExitCode: {{.State.ExitCode}} | OOMKilled: {{.State.OOMKilled}} | StartedAt: {{.State.StartedAt}}' 2>&1 || echo "FAIL"

echo "--- 6. Recent Container Logs Tail 500 ---"
docker logs --tail 500 novaedge-dev 2>&1

echo "=== [TB_LOCAL_PROBES_END] ==="
"""

    def __init__(
        self,
        host: str = "43.205.119.168",
        port_http: int = 8080,
        port_mqtt: int = 1883,
        port_mqtts: int = 8883,
        email: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = 5.0,
        insecure_tls: bool = False,
        container_name: str = "novaedge-dev",
    ):
        self.host = host
        self.port_http = port_http
        self.port_mqtt = port_mqtt
        self.port_mqtts = port_mqtts
        self.email = email
        self.password = password
        self.timeout = timeout
        self.insecure_tls = insecure_tls
        self.container_name = container_name
        self.results: Dict[str, Any] = {
            "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "target": {
                "host": self.host,
                "port_http": self.port_http,
                "port_mqtt": self.port_mqtt,
                "port_mqtts": self.port_mqtts,
            },
            "http": {},
            "mqtt": {},
            "mqtts": {},
            "container_logs": {
                "evaluated": False,
                "healthy": None,
                "total_lines_analyzed": 0,
                "matches": {},
                "reason": "Not evaluated (external network probe mode)",
            },
            "container_lifecycle": {
                "evaluated": False,
                "status": None,
                "restarts": None,
                "exit_code": None,
                "oom_killed": None,
                "started_at": None,
                "healthy": None,
                "reason": "Not evaluated (external network probe mode)",
            },
            "overall_verdict": "UNKNOWN",
        }

    # --------------------------------------------------------------------------
    # 1. HTTP 8080 Web UI & REST API Probes
    # --------------------------------------------------------------------------

    def verify_http_web_ui(self) -> Dict[str, Any]:
        """Probe GET http://<host>:8080/login."""
        url = f"http://{self.host}:{self.port_http}/login"
        t0 = time.perf_counter()
        result: Dict[str, Any] = {
            "endpoint": "/login",
            "url": url,
            "healthy": False,
            "status_code": None,
            "latency_ms": None,
            "error": None,
        }
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "ThingsBoard-HealthVerifier/1.0"}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                body = resp.read(2048).decode("utf-8", errors="replace")
                result["status_code"] = resp.status
                result["latency_ms"] = round(elapsed_ms, 2)
                # Check for standard ThingsBoard HTML indicators
                is_tb = "thingsboard" in body.lower() or "login" in body.lower() or "<html" in body.lower()
                result["healthy"] = (resp.status == 200) and is_tb
        except urllib.error.HTTPError as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            result["status_code"] = e.code
            result["latency_ms"] = round(elapsed_ms, 2)
            result["error"] = f"HTTP {e.code}: {e.reason}"
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            result["latency_ms"] = round(elapsed_ms, 2)
            result["error"] = str(e)

        self.results["http"]["web_ui"] = result
        return result

    def verify_http_health_endpoint(self) -> Dict[str, Any]:
        """
        Probe unauthenticated REST API endpoint.
        Probes GET http://<host>:8080/api/noauth/userPasswordPolicy (returns HTTP 200 OK with JSON).
        Note: /api/noauth/health is unmapped (HTTP 404) in standard ThingsBoard CE/PE.
        """
        url = f"http://{self.host}:{self.port_http}/api/noauth/userPasswordPolicy"
        t0 = time.perf_counter()
        result: Dict[str, Any] = {
            "endpoint": "/api/noauth/userPasswordPolicy",
            "url": url,
            "healthy": False,
            "status_code": None,
            "latency_ms": None,
            "payload": None,
            "error": None,
            "note": "Endpoint /api/noauth/health returns HTTP 404 (unmapped in ThingsBoard); verified via /api/noauth/userPasswordPolicy",
        }
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "ThingsBoard-HealthVerifier/1.0"}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                body = resp.read().decode("utf-8", errors="replace")
                result["status_code"] = resp.status
                result["latency_ms"] = round(elapsed_ms, 2)
                try:
                    result["payload"] = json.loads(body)
                except Exception:
                    result["payload"] = body[:200]
                result["healthy"] = resp.status == 200
        except urllib.error.HTTPError as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            result["status_code"] = e.code
            result["latency_ms"] = round(elapsed_ms, 2)
            result["error"] = f"HTTP {e.code}: {e.reason}"
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            result["latency_ms"] = round(elapsed_ms, 2)
            result["error"] = str(e)

        self.results["http"]["health_api"] = result
        return result

    def verify_rest_login(self) -> Dict[str, Any]:
        """Probe POST http://<host>:8080/api/auth/login."""
        url = f"http://{self.host}:{self.port_http}/api/auth/login"
        result: Dict[str, Any] = {
            "endpoint": "/api/auth/login",
            "url": url,
            "healthy": False,
            "status_code": None,
            "latency_ms": None,
            "token_acquired": False,
            "error": None,
        }

        if not self.email or not self.password:
            result["error"] = "Skipped: TB_EMAIL or TB_PASSWORD not provided"
            result["status_code"] = 0
            self.results["http"]["auth_login"] = result
            return result

        t0 = time.perf_counter()
        try:
            payload = json.dumps({"username": self.email, "password": self.password}).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "ThingsBoard-HealthVerifier/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                body = json.loads(resp.read().decode("utf-8"))
                result["status_code"] = resp.status
                result["latency_ms"] = round(elapsed_ms, 2)
                token = body.get("token")
                result["token_acquired"] = bool(token)
                result["healthy"] = resp.status == 200 and bool(token)
                result["token_preview"] = token[:16] + "..." if token else None
        except urllib.error.HTTPError as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            result["status_code"] = e.code
            result["latency_ms"] = round(elapsed_ms, 2)
            result["error"] = f"HTTP {e.code}: {e.reason}"
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            result["latency_ms"] = round(elapsed_ms, 2)
            result["error"] = str(e)

        self.results["http"]["auth_login"] = result
        return result

    # --------------------------------------------------------------------------
    # 2. MQTT 1883 Layer 4 Socket & Layer 7 Protocol Probes
    # --------------------------------------------------------------------------

    def verify_mqtt_socket(self) -> Dict[str, Any]:
        """Probe Layer 4 TCP socket on port 1883."""
        result: Dict[str, Any] = {
            "port": self.port_mqtt,
            "reachable": False,
            "latency_ms": None,
            "error": None,
        }
        t0 = time.perf_counter()
        try:
            with socket.create_connection((self.host, self.port_mqtt), timeout=self.timeout) as s:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                result["reachable"] = True
                result["latency_ms"] = round(elapsed_ms, 2)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            result["latency_ms"] = round(elapsed_ms, 2)
            result["error"] = str(e)

        self.results["mqtt"]["l4_socket"] = result
        return result

    def verify_mqtt_protocol(self) -> Dict[str, Any]:
        """
        Probe Layer 7 MQTT 3.1.1 protocol handshake on port 1883.
        Transmits a synthesized 22-byte CONNECT packet:
          Fixed Header: 0x10, Length: 0x14 (20)
          Variable Header: 'MQTT' (4 bytes), Level: 4, CleanSession (0x02), KeepAlive: 60s (0x003c)
          Payload: Client ID 'diag_tb1' (8 bytes)
        Validates receipt of 4-byte CONNACK: 0x20 0x02 <flags> <return_code>
        """
        packet = b"\x10\x14\x00\x04MQTT\x04\x02\x00\x3c\x00\x08diag_tb1"
        result: Dict[str, Any] = {
            "port": self.port_mqtt,
            "healthy": False,
            "connack_hex": None,
            "return_code": None,
            "return_description": None,
            "latency_ms": None,
            "error": None,
        }

        rc_map = {
            0x00: "Connection Accepted",
            0x01: "Unacceptable Protocol Version",
            0x02: "Identifier Rejected",
            0x03: "Server Unavailable",
            0x04: "Bad User Name or Password",
            0x05: "Not Authorized",
        }

        t0 = time.perf_counter()
        try:
            with socket.create_connection((self.host, self.port_mqtt), timeout=self.timeout) as s:
                s.settimeout(self.timeout)
                s.sendall(packet)
                resp_buf = bytearray()
                while len(resp_buf) < 4:
                    chunk = s.recv(4 - len(resp_buf))
                    if not chunk:
                        break
                    resp_buf.extend(chunk)
                resp = bytes(resp_buf)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                result["latency_ms"] = round(elapsed_ms, 2)

                if len(resp) >= 4 and resp[0] == 0x20 and resp[1] == 0x02:
                    return_code = resp[3]
                    result["connack_hex"] = resp.hex()
                    result["return_code"] = return_code
                    result["return_description"] = rc_map.get(return_code, f"Unknown ({return_code})")
                    # In ThingsBoard, an unauthenticated client receives return code 0x02 (Identifier Rejected) or 0x04/0x05.
                    # Receipt of ANY valid CONNACK packet confirms the Netty MQTT broker is operational.
                    result["healthy"] = return_code in (0x00, 0x02, 0x04, 0x05)
                else:
                    result["error"] = f"Invalid packet format: {resp.hex() if resp else 'empty'}"
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            result["latency_ms"] = round(elapsed_ms, 2)
            result["error"] = str(e)

        self.results["mqtt"]["l7_protocol"] = result
        return result

    # --------------------------------------------------------------------------
    # 3. MQTTS 8883 Layer 4 & Layer 7 TLS Probes
    # --------------------------------------------------------------------------

    def verify_mqtts_socket(self) -> Dict[str, Any]:
        """Probe Layer 4 TCP socket on port 8883."""
        result: Dict[str, Any] = {
            "port": self.port_mqtts,
            "reachable": False,
            "latency_ms": None,
            "error": None,
        }
        t0 = time.perf_counter()
        try:
            with socket.create_connection((self.host, self.port_mqtts), timeout=self.timeout) as s:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                result["reachable"] = True
                result["latency_ms"] = round(elapsed_ms, 2)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            result["latency_ms"] = round(elapsed_ms, 2)
            result["error"] = str(e)

        self.results["mqtts"]["l4_socket"] = result
        return result

    def verify_mqtts_tls_handshake(self) -> Dict[str, Any]:
        """Probe Layer 7 TLS handshake and optional encrypted MQTT CONNECT on port 8883."""
        result: Dict[str, Any] = {
            "port": self.port_mqtts,
            "healthy": False,
            "tls_version": None,
            "cipher": None,
            "peer_cert_subject": None,
            "latency_ms": None,
            "connack_hex": None,
            "return_code": None,
            "return_description": None,
            "error": None,
        }

        context = ssl.create_default_context()
        if self.insecure_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        t0 = time.perf_counter()
        try:
            with socket.create_connection((self.host, self.port_mqtts), timeout=self.timeout) as raw_s:
                with context.wrap_socket(raw_s, server_hostname=self.host) as tls_s:
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    result["latency_ms"] = round(elapsed_ms, 2)
                    result["tls_version"] = tls_s.version()
                    result["cipher"] = tls_s.cipher()[0] if tls_s.cipher() else None

                    try:
                        cert = tls_s.getpeercert()
                        if cert and "subject" in cert:
                            result["peer_cert_subject"] = dict(x[0] for x in cert["subject"])
                    except Exception:
                        pass

                    # Attempt encrypted MQTT CONNECT over TLS
                    packet = b"\x10\x14\x00\x04MQTT\x04\x02\x00\x3c\x00\x08diag_tb1"
                    tls_s.sendall(packet)
                    resp_buf = bytearray()
                    while len(resp_buf) < 4:
                        chunk = tls_s.recv(4 - len(resp_buf))
                        if not chunk:
                            break
                        resp_buf.extend(chunk)
                    resp = bytes(resp_buf)

                    rc_map = {
                        0x00: "Connection Accepted",
                        0x01: "Unacceptable Protocol Version",
                        0x02: "Identifier Rejected",
                        0x03: "Server Unavailable",
                        0x04: "Bad User Name or Password",
                        0x05: "Not Authorized",
                    }
                    if len(resp) >= 4 and resp[0] == 0x20 and resp[1] == 0x02:
                        return_code = resp[3]
                        result["connack_hex"] = resp.hex()
                        result["return_code"] = return_code
                        result["return_description"] = rc_map.get(return_code, f"Unknown ({return_code})")
                        rc_ok = return_code in (0x00, 0x02, 0x04, 0x05)
                        result["healthy"] = rc_ok
                        if not rc_ok:
                            result["error"] = f"Broker rejected connection with code {return_code}: {result['return_description']}"
                    else:
                        result["healthy"] = True  # TLS succeeded; broker may require client certificates
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            result["latency_ms"] = round(elapsed_ms, 2)
            result["error"] = str(e)

        self.results["mqtts"]["l7_tls"] = result
        return result

    # --------------------------------------------------------------------------
    # 4. Container Error Log Audit
    # --------------------------------------------------------------------------

    def audit_container_logs_content(self, logs_text: str) -> Dict[str, Any]:
        """Audit container logs text against the 5 failure domains."""
        signatures = {
            "jvm_oom": re.compile(
                r"(?i)(OutOfMemoryError|Java heap space|GC overhead limit exceeded|Direct buffer memory|Metaspace)"
            ),
            "hikaricp_pool": re.compile(
                r"(?i)(HikariPool.*Connection is not available|ConnectionTimeoutException|FATAL: remaining connection slots are reserved|too many clients)"
            ),
            "db_transport": re.compile(
                r"(?i)(org\.postgresql\.util\.PSQLException|Connection reset by peer|could not send data to server|NoHostAvailableException)"
            ),
            "netty_drops": re.compile(
                r"(?i)(io\.netty\.channel\.StacklessClosedChannelException|Client session closed due to inactivity timeout|Too many open files)"
            ),
            "rate_limits": re.compile(
                r"(?i)(TbRateLimits: Blocked|Queue is full|Dropping message|Failed to submit message to rule engine)"
            ),
        }

        audit_result: Dict[str, Any] = {
            "evaluated": True,
            "total_lines_analyzed": len(logs_text.splitlines()),
            "matches": {},
            "healthy": True,
            "reason": "Log audit completed",
        }

        for domain, pattern in signatures.items():
            matches = [line.strip() for line in logs_text.splitlines() if pattern.search(line)]
            audit_result["matches"][domain] = {
                "count": len(matches),
                "samples": matches[:3],
            }
            # OOM or HikariCP pool exhaustion are critical failures
            if domain in ("jvm_oom", "hikaricp_pool") and len(matches) > 0:
                audit_result["healthy"] = False
                audit_result["reason"] = f"Critical anomaly detected: {domain}"

        self.results["container_logs"] = audit_result
        return audit_result

    # --------------------------------------------------------------------------
    # 5. Remote SSM Execution Adapter
    # --------------------------------------------------------------------------

    def execute_remote_ssm_probe(
        self,
        instance_id: str = "i-064834c54249ba9b7",
        region: str = "ap-south-1",
        timeout_seconds: int = 45,
    ) -> Dict[str, Any]:
        """Execute on-host verification script via AWS Systems Manager."""
        import boto3

        ssm = boto3.client("ssm", region_name=region)
        remote_script = self.REMOTE_SSM_SCRIPT

        print(f"[SSM_PROBE] Dispatching remote probe to {instance_id} ({region})...")
        send_resp = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Comment="ThingsBoard Remote Health Verification",
            Parameters={"commands": [remote_script]},
            TimeoutSeconds=timeout_seconds,
        )
        cmd_id = send_resp["Command"]["CommandId"]

        start_time = time.time()
        inv_result: Optional[Dict[str, Any]] = None
        while (time.time() - start_time) < timeout_seconds:
            time.sleep(3.0)
            inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
            status = inv.get("Status")
            if status in ("Success", "Failed", "TimedOut", "Cancelled"):
                inv_result = inv
                break

        if not inv_result:
            raise TimeoutError(f"SSM command {cmd_id} timed out.")

        stdout = inv_result.get("StandardOutputContent", "")
        self.results["ssm_raw_stdout"] = stdout
        self.results["ssm_command_id"] = cmd_id
        self.results["ssm_status"] = inv_result.get("Status")

        # Parse remote stdout into results structure
        lines = stdout.splitlines()
        for idx, line in enumerate(lines):
            if "HTTP_CODE:" in line and "CONNECT_TIME:" in line:
                parts = dict(kv.split(":", 1) for kv in line.strip().split("|") if ":" in kv)
                self.results["http"]["web_ui"] = {
                    "endpoint": "/login",
                    "status_code": int(parts.get("HTTP_CODE", 0)),
                    "latency_ms": round(float(parts.get("TOTAL_TIME", 0.0)) * 1000.0, 2),
                    "healthy": parts.get("HTTP_CODE") == "200",
                }
            elif "HEALTH_API_HTTP_CODE:" in line:
                parts = dict(kv.split(":", 1) for kv in line.strip().split("|") if ":" in kv)
                code = int(parts.get("HEALTH_API_HTTP_CODE", 0))
                self.results["http"]["health_api"] = {
                    "endpoint": "/api/noauth/userPasswordPolicy",
                    "status_code": code,
                    "latency_ms": round(float(parts.get("TOTAL_TIME", 0.0)) * 1000.0, 2),
                    "healthy": code == 200,
                }
            elif "MQTT_CONNACK:" in line:
                parts = dict(kv.split(":", 1) for kv in line.strip().split("|") if ":" in kv)
                hex_str = parts.get("MQTT_CONNACK", "")
                rc = int(hex_str[6:8], 16) if len(hex_str) >= 8 else -1
                self.results["mqtt"]["l7_protocol"] = {
                    "connack_hex": hex_str,
                    "return_code": rc,
                    "latency_ms": round(float(parts.get("TIME", 0.0)) * 1000.0, 2),
                    "healthy": hex_str.startswith("2002") and rc in (0x00, 0x02, 0x04, 0x05),
                }
            elif "Status:" in line and "OOMKilled:" in line:
                parts = {
                    k.strip(): v.strip()
                    for kv in line.strip().split("|")
                    if ":" in kv
                    for k, v in [kv.split(":", 1)]
                }
                raw_status = parts.get("Status", "").strip()
                status_lower = raw_status.lower()
                restarts_str = parts.get("Restarts", "0").strip()
                exit_code_str = parts.get("ExitCode", "0").strip()
                oom_killed_str = parts.get("OOMKilled", "false").strip().lower()
                started_at = parts.get("StartedAt", "").strip()

                try:
                    restarts = int(restarts_str)
                except ValueError:
                    restarts = None

                try:
                    exit_code = int(exit_code_str)
                except ValueError:
                    exit_code = None

                oom_killed = oom_killed_str == "true"
                is_running = status_lower == "running"
                is_healthy = is_running and not oom_killed and (exit_code == 0 if exit_code is not None else True)

                reason = "Container running normally"
                if oom_killed:
                    reason = "Container was terminated by kernel OOM-killer (OOMKilled: true)"
                elif not is_running:
                    reason = f"Container is not running (Status: {raw_status})"
                elif exit_code not in (0, None):
                    reason = f"Container exited with non-zero code ({exit_code})"

                self.results["container_lifecycle"] = {
                    "evaluated": True,
                    "status": raw_status,
                    "restarts": restarts,
                    "exit_code": exit_code,
                    "oom_killed": oom_killed,
                    "started_at": started_at,
                    "healthy": is_healthy,
                    "reason": reason,
                }

        # Extract container logs tail
        log_start = False
        captured_logs = []
        for line in lines:
            if "--- 6. Recent Container Logs Tail 500 ---" in line:
                log_start = True
                continue
            if "=== [TB_LOCAL_PROBES_END] ===" in line:
                log_start = False
            if log_start:
                captured_logs.append(line)

        if captured_logs:
            self.audit_container_logs_content("\n".join(captured_logs))

        return self.results

    # --------------------------------------------------------------------------
    # 6. Aggregation and Presentation
    # --------------------------------------------------------------------------

    def evaluate_overall_verdict(self) -> str:
        """
        Evaluate overall health verdict based on HTTP, MQTT, MQTTS, and Log health:
          - PASS: All evaluated services healthy.
          - DEGRADED: Essential services operational, but one non-vital transport or cert check failed.
          - FAIL: Critical failure (Web UI down, all MQTT ingestion down, or JVM OOM detected).
        """
        http_ok = bool(self.results.get("http", {}).get("web_ui", {}).get("healthy", False))

        # Layer 7 protocol health strictly required; no Layer 4 fallback
        mqtt_ok = bool(self.results.get("mqtt", {}).get("l7_protocol", {}).get("healthy", False))

        # Evaluate MQTTS 8883 if probed
        mqtts_probed = bool(self.results.get("mqtts", {}).get("l7_tls") or self.results.get("mqtts", {}).get("l4_socket"))
        mqtts_ok = bool(self.results.get("mqtts", {}).get("l7_tls", {}).get("healthy", False)) if mqtts_probed else True

        # Evaluate Container Logs state
        logs_data = self.results.get("container_logs", {})
        logs_evaluated = bool(logs_data.get("evaluated", False))
        logs_clean = logs_data.get("healthy") if logs_evaluated else True

        # Evaluate Container Lifecycle (Docker inspect)
        lifecycle_data = self.results.get("container_lifecycle", {})
        lifecycle_evaluated = bool(lifecycle_data.get("evaluated", False))
        lifecycle_ok = lifecycle_data.get("healthy", True) if lifecycle_evaluated else True

        # Critical Failure Conditions
        if lifecycle_evaluated and lifecycle_data.get("oom_killed", False):
            verdict = "FAIL"
        elif lifecycle_evaluated and lifecycle_ok is False:
            verdict = "FAIL"
        elif logs_evaluated and logs_clean is False:
            verdict = "FAIL"
        elif not http_ok and not mqtt_ok:
            verdict = "FAIL"
        elif not mqtt_ok and (mqtts_probed and not mqtts_ok):
            # Both plain and secure telemetry ingestion are failing
            verdict = "FAIL"
        # Full Pass Condition
        elif http_ok and mqtt_ok and (mqtts_ok or not mqtts_probed) and logs_clean and lifecycle_ok:
            verdict = "PASS"
        # Degraded Conditions
        elif http_ok or mqtt_ok or (mqtts_probed and mqtts_ok):
            verdict = "DEGRADED"
        else:
            verdict = "FAIL"

        self.results["overall_verdict"] = verdict
        return verdict

    def print_summary(self):
        """Render ANSI colored summary table."""
        verdict = self.evaluate_overall_verdict()
        verdict_color = GREEN if verdict == "PASS" else (YELLOW if verdict == "DEGRADED" else RED)

        print("\n" + "=" * 75)
        print(f"{BOLD}THINGSBOARD OPERATIONAL HEALTH VERIFICATION REPORT{RESET}")
        print("=" * 75)
        print(f"Target Host       : {self.host}")
        print(f"Timestamp (UTC)   : {self.results['timestamp_utc']}")
        print(f"Overall Verdict   : {verdict_color}{BOLD}{verdict}{RESET}")
        print("-" * 75)

        # HTTP Table
        print(f"{BOLD}[1] HTTP 8080 Web UI & REST API{RESET}")
        web_ui = self.results["http"].get("web_ui", {})
        health_api = self.results["http"].get("health_api", {})
        auth_login = self.results["http"].get("auth_login", {})

        def fmt_status(h_dict):
            if not h_dict:
                return f"{YELLOW}N/A{RESET}"
            if h_dict.get("healthy"):
                lat = h_dict.get("latency_ms", "0")
                code = h_dict.get("status_code", "200")
                return f"{GREEN}PASS{RESET} (HTTP {code}, {lat}ms)"
            err = h_dict.get("error") or f"HTTP {h_dict.get('status_code')}"
            return f"{RED}FAIL{RESET} ({err})"

        print(f"  - Web UI (/login)             : {fmt_status(web_ui)}")
        if health_api:
            print(f"  - Health API (/api/noauth/..) : {fmt_status(health_api)}")
        if auth_login:
            print(f"  - Auth Login (/api/auth/..)   : {fmt_status(auth_login)}")

        # MQTT Table
        print(f"\n{BOLD}[2] MQTT Broker Services (Ports 1883 & 8883){RESET}")
        l4_mqtt = self.results["mqtt"].get("l4_socket", {})
        l7_mqtt = self.results["mqtt"].get("l7_protocol", {})
        l4_mqtts = self.results["mqtts"].get("l4_socket", {})
        l7_mqtts = self.results["mqtts"].get("l7_tls", {})

        if l4_mqtt:
            status_l4 = f"{GREEN}OPEN{RESET} ({l4_mqtt.get('latency_ms')}ms)" if l4_mqtt.get("reachable") else f"{RED}CLOSED{RESET} ({l4_mqtt.get('error')})"
            print(f"  - Port 1883 Layer 4 Socket    : {status_l4}")
        if l7_mqtt:
            if l7_mqtt.get("healthy"):
                hex_str = l7_mqtt.get("connack_hex", "")
                desc = l7_mqtt.get("return_description", "")
                lat = l7_mqtt.get("latency_ms", "")
                print(f"  - Port 1883 Layer 7 CONNACK   : {GREEN}VERIFIED{RESET} ({hex_str} -> {desc}, {lat}ms)")
            else:
                print(f"  - Port 1883 Layer 7 CONNACK   : {RED}FAILED{RESET} ({l7_mqtt.get('error')})")

        if l4_mqtts:
            status_l4s = f"{GREEN}OPEN{RESET} ({l4_mqtts.get('latency_ms')}ms)" if l4_mqtts.get("reachable") else f"{RED}CLOSED{RESET} ({l4_mqtts.get('error')})"
            print(f"  - Port 8883 Layer 4 Socket    : {status_l4s}")
        if l7_mqtts:
            if l7_mqtts.get("healthy"):
                ver = l7_mqtts.get("tls_version", "TLS")
                ciph = l7_mqtts.get("cipher", "Cipher")
                lat = l7_mqtts.get("latency_ms", "")
                print(f"  - Port 8883 Layer 7 TLS       : {GREEN}NEGOTIATED{RESET} ({ver}, {ciph}, {lat}ms)")
            else:
                print(f"  - Port 8883 Layer 7 TLS       : {YELLOW}SKIPPED/UNVERIFIED{RESET} ({l7_mqtts.get('error')})")

        # Container Lifecycle (if evaluated)
        lifecycle = self.results.get("container_lifecycle", {})
        if lifecycle.get("evaluated"):
            print(f"\n{BOLD}[3] Container Lifecycle & Kernel State{RESET}")
            status = lifecycle.get("status", "unknown")
            restarts = lifecycle.get("restarts", "unknown")
            exit_code = lifecycle.get("exit_code", "unknown")
            oom = lifecycle.get("oom_killed", False)
            started_at = lifecycle.get("started_at", "unknown")
            oom_str = f"{RED}YES (OOM DETECTED){RESET}" if oom else f"{GREEN}NO{RESET}"
            status_str = f"{GREEN}{status}{RESET}" if str(status).lower() == "running" else f"{RED}{status}{RESET}"
            print(f"  - Container Status   : {status_str}")
            print(f"  - Restart Count      : {restarts}")
            print(f"  - Last Exit Code     : {exit_code}")
            print(f"  - Kernel OOM Killed  : {oom_str}")
            print(f"  - Started At         : {started_at}")

        # Container Logs
        log_section_idx = "[4]" if lifecycle.get("evaluated") else "[3]"
        print(f"\n{BOLD}{log_section_idx} Container Anomaly Log Audit{RESET}")
        logs_res = self.results.get("container_logs", {})
        if logs_res.get("evaluated") and "matches" in logs_res:
            matches = logs_res["matches"]
            for dom, m in matches.items():
                cnt = m["count"]
                color = GREEN if cnt == 0 else (RED if dom in ("jvm_oom", "hikaricp_pool") else YELLOW)
                print(f"  - {dom.ljust(15)} : {color}{cnt} matches{RESET}")
        else:
            print("  - Log audit not evaluated (external network probe mode)")

        print("=" * 75 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="ThingsBoard Live Health Verification CLI"
    )
    parser.add_argument(
        "--host",
        default="43.205.119.168",
        help="Target host IP or domain (default: 43.205.119.168)",
    )
    parser.add_argument(
        "--port-http",
        type=int,
        default=8080,
        help="ThingsBoard HTTP Port (default: 8080)",
    )
    parser.add_argument(
        "--port-mqtt",
        type=int,
        default=1883,
        help="ThingsBoard MQTT Port (default: 1883)",
    )
    parser.add_argument(
        "--port-mqtts",
        type=int,
        default=8883,
        help="ThingsBoard Secure MQTT Port (default: 8883)",
    )
    parser.add_argument(
        "--tb-email",
        default=os.getenv("TB_EMAIL"),
        help="ThingsBoard user email for REST login test",
    )
    parser.add_argument(
        "--tb-password",
        default=os.getenv("TB_PASSWORD"),
        help="ThingsBoard user password for REST login test",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Socket / HTTP timeout in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--insecure-tls",
        action="store_true",
        help="Disable strict certificate hostname verification for TLS",
    )
    parser.add_argument(
        "--remote-ssm",
        action="store_true",
        help="Dispatch probe remotely to EC2 host via AWS SSM",
    )
    parser.add_argument(
        "--instance-id",
        default="i-064834c54249ba9b7",
        help="EC2 Instance ID for SSM dispatch (default: i-064834c54249ba9b7)",
    )
    parser.add_argument(
        "--region",
        default="ap-south-1",
        help="AWS Region for SSM dispatch (default: ap-south-1)",
    )
    parser.add_argument(
        "--json-output",
        help="File path to save JSON verification report",
    )

    args = parser.parse_args()

    verifier = ThingsBoardHealthVerifier(
        host=args.host,
        port_http=args.port_http,
        port_mqtt=args.port_mqtt,
        port_mqtts=args.port_mqtts,
        email=args.tb_email,
        password=args.tb_password,
        timeout=args.timeout,
        insecure_tls=args.insecure_tls,
    )

    if args.remote_ssm:
        verifier.execute_remote_ssm_probe(
            instance_id=args.instance_id,
            region=args.region,
            timeout_seconds=int(args.timeout * 8),
        )
    else:
        # Run local / network probes
        verifier.verify_http_web_ui()
        verifier.verify_http_health_endpoint()
        verifier.verify_rest_login()
        verifier.verify_mqtt_socket()
        verifier.verify_mqtt_protocol()
        verifier.verify_mqtts_socket()
        verifier.verify_mqtts_tls_handshake()

    verifier.print_summary()

    if args.json_output:
        out_p = Path(args.json_output)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            json.dump(verifier.results, f, indent=2)
        print(f"[REPORT] JSON report written to: {args.json_output}")

    verdict = verifier.results.get("overall_verdict", "UNKNOWN")
    sys.exit(0 if verdict == "PASS" else (1 if verdict == "DEGRADED" else 2))


if __name__ == "__main__":
    main()
