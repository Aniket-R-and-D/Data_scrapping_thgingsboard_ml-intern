"""
tests/test_milestone1_verification.py
--------------------------------------
Adversarial test harness and empirical verification for Milestone 1 deliverables:
  - scripts/cloud/diagnose_coexistence_live.py
  - scripts/thingsboard/verify_tb_health_live.py

Tests edge cases, invalid hostnames, unreachable ports, SSL vs non-SSL,
verdict evaluation logic, and parser resilience.
"""

import json
import socket
import ssl
import sys
import threading
import time
from pathlib import Path
import pytest

# Ensure repo root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cloud.diagnose_coexistence_live import (
    parse_diagnostic_output,
    _parse_int,
    DIAGNOSTIC_COMMAND_BLOCKS,
)
from scripts.thingsboard.verify_tb_health_live import ThingsBoardHealthVerifier


# ==============================================================================
# 1. Edge Case: Invalid Hostname
# ==============================================================================

def test_invalid_hostname_handling():
    """Verify how ThingsBoardHealthVerifier handles an unresolvable hostname."""
    verifier = ThingsBoardHealthVerifier(
        host="invalid.domain.that.does.not.exist.test",
        timeout=1.0,
    )
    
    web_res = verifier.verify_http_web_ui()
    assert web_res["healthy"] is False
    assert web_res["status_code"] is None
    assert "getaddrinfo failed" in str(web_res["error"]) or "Name or service not known" in str(web_res["error"]) or "nodename" in str(web_res["error"])

    mqtt_l4 = verifier.verify_mqtt_socket()
    assert mqtt_l4["reachable"] is False
    assert "getaddrinfo failed" in str(mqtt_l4["error"]) or "Name or service not known" in str(mqtt_l4["error"]) or "nodename" in str(mqtt_l4["error"])

    mqtt_l7 = verifier.verify_mqtt_protocol()
    assert mqtt_l7["healthy"] is False

    mqtts_l4 = verifier.verify_mqtts_socket()
    assert mqtts_l4["reachable"] is False

    mqtts_tls = verifier.verify_mqtts_tls_handshake()
    assert mqtts_tls["healthy"] is False

    verdict = verifier.evaluate_overall_verdict()
    assert verdict == "FAIL", f"Expected FAIL on unresolvable host, got {verdict}"


# ==============================================================================
# 2. Edge Case: Unreachable Port on Localhost
# ==============================================================================

def test_unreachable_port_handling():
    """Verify handling when host is reachable (localhost) but ports are closed."""
    # Find an unused free port on localhost
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    # Now that socket is closed, free_port is not listening
    verifier = ThingsBoardHealthVerifier(
        host="127.0.0.1",
        port_http=free_port,
        port_mqtt=free_port,
        port_mqtts=free_port,
        timeout=1.0,
    )

    web_res = verifier.verify_http_web_ui()
    assert web_res["healthy"] is False
    assert web_res["error"] is not None

    mqtt_l4 = verifier.verify_mqtt_socket()
    assert mqtt_l4["reachable"] is False

    mqtt_l7 = verifier.verify_mqtt_protocol()
    assert mqtt_l7["healthy"] is False

    verdict = verifier.evaluate_overall_verdict()
    assert verdict == "FAIL"


# ==============================================================================
# 3. Flaw Detection: L7 MQTT Failure Masked by L4 Reachability
# ==============================================================================

def test_oracle_flaw_l7_masked_by_l4_socket():
    """
    CRITICAL ORACLE TEST:
    Demonstrates that evaluate_overall_verdict() marks the system as PASS
    even when Layer 7 MQTT 3.1.1 protocol handshake FAILS completely,
    as long as Layer 4 socket is open (e.g. dumb TCP proxy or hanging port).
    """
    # Start a dummy TCP server that accepts connections but never returns CONNACK
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    dummy_port = server_sock.getsockname()[1]

    def dummy_server():
        try:
            conn, _ = server_sock.accept()
            # Do not send any data; close immediately or wait
            time.sleep(0.5)
            conn.close()
        except Exception:
            pass
        finally:
            server_sock.close()

    t = threading.Thread(target=dummy_server, daemon=True)
    t.start()

    verifier = ThingsBoardHealthVerifier(
        host="127.0.0.1",
        port_http=dummy_port,
        port_mqtt=dummy_port,
        port_mqtts=dummy_port,
        timeout=1.0,
    )

    # Mock HTTP web UI as healthy to isolate the MQTT verdict logic
    verifier.results["http"]["web_ui"] = {"healthy": True, "status_code": 200, "latency_ms": 10.0}

    # Verify MQTT L4 and L7
    l4_res = verifier.verify_mqtt_socket()
    l7_res = verifier.verify_mqtt_protocol()

    assert l4_res["reachable"] is True, "L4 TCP socket should have succeeded on dummy server"
    assert l7_res["healthy"] is False, "L7 MQTT handshake must fail because server sent no CONNACK"

    verdict = verifier.evaluate_overall_verdict()
    assert verdict in ("DEGRADED", "FAIL"), "Strict oracle ensures L7 failure cannot produce PASS"


# ==============================================================================
# 4. Flaw Detection: MQTTS Port 8883 Ignored in Overall Verdict
# ==============================================================================

def test_mqtts_omitted_from_overall_verdict():
    """
    CRITICAL ORACLE TEST:
    Demonstrates that port 8883 (MQTTS) is completely omitted from evaluate_overall_verdict().
    Even if MQTTS port 8883 is down, unreachable, or TLS fails, verdict can still be PASS.
    """
    verifier = ThingsBoardHealthVerifier(host="127.0.0.1")
    verifier.results["http"]["web_ui"] = {"healthy": True}
    verifier.results["mqtt"]["l7_protocol"] = {"healthy": True}
    verifier.results["mqtts"]["l4_socket"] = {"reachable": False, "error": "Connection refused"}
    verifier.results["mqtts"]["l7_tls"] = {"healthy": False, "error": "Handshake failed"}

    verdict = verifier.evaluate_overall_verdict()
    assert verdict != "PASS", "Confirms port 8883 MQTTS failure prevents unconditioned PASS verdict"


# ==============================================================================
# 5. Non-SSL Port Tested with TLS Probe
# ==============================================================================

def test_non_ssl_port_tested_as_ssl():
    """Test behavior when an unencrypted plain TCP socket is probed with TLS."""
    # Start plain TCP echo/drop server
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    plain_port = server_sock.getsockname()[1]

    def plain_server():
        try:
            conn, _ = server_sock.accept()
            # Send raw non-TLS text
            conn.sendall(b"NOT_A_TLS_SERVER\n")
            conn.close()
        except Exception:
            pass
        finally:
            server_sock.close()

    t = threading.Thread(target=plain_server, daemon=True)
    t.start()

    verifier = ThingsBoardHealthVerifier(
        host="127.0.0.1",
        port_mqtts=plain_port,
        timeout=1.0,
    )

    tls_res = verifier.verify_mqtts_tls_handshake()
    assert tls_res["healthy"] is False
    assert tls_res["tls_version"] is None
    assert tls_res["error"] is not None


# ==============================================================================
# 6. Container Error Log Audit Regex Stress
# ==============================================================================

def test_log_audit_regex_matches():
    """Verify log auditor correctly flags JVM OOM and HikariCP pool errors."""
    verifier = ThingsBoardHealthVerifier()
    dirty_logs = """
    2026-10-29 11:00:01.123 [main] INFO  o.t.s.ThingsboardServerApplication - Starting ThingsboardServerApplication
    2026-10-29 11:05:00.456 [HikariPool-1 housekeeper] WARN  com.zaxxer.hikari.pool.HikariPool - HikariPool-1 - Connection is not available, request timed out after 30000ms.
    2026-10-29 11:10:00.789 [http-nio-8080-exec-1] ERROR o.a.c.c.C.[.[.[/].[dispatcherServlet] - Servlet.service() threw exception: java.lang.OutOfMemoryError: Java heap space
    2026-10-29 11:12:00.000 [nioEventLoopGroup-4-1] WARN  i.n.c.AbstractChannel - io.netty.channel.StacklessClosedChannelException: null
    """

    res = verifier.audit_container_logs_content(dirty_logs)
    assert res["healthy"] is False, "Log audit must report healthy=False when OOM or HikariCP pool errors exist"
    assert res["matches"]["jvm_oom"]["count"] == 1
    assert res["matches"]["hikaricp_pool"]["count"] == 1
    assert res["matches"]["netty_drops"]["count"] == 1
    assert res["matches"]["rate_limits"]["count"] == 0

    clean_logs = """
    2026-10-29 11:00:01.123 [main] INFO  o.t.s.ThingsboardServerApplication - Starting ThingsboardServerApplication
    2026-10-29 11:05:00.456 [main] INFO  o.t.s.s.t.DefaultTenantService - Tenant loaded successfully
    """
    clean_res = verifier.audit_container_logs_content(clean_logs)
    assert clean_res["healthy"] is True
    assert clean_res["matches"]["jvm_oom"]["count"] == 0


# ==============================================================================
# 7. Diagnose Parser Resilience: Empty, Corrupted, and Synthetic SSM Output
# ==============================================================================

def test_parse_diagnostic_output_corrupted_memory():
    """Verify parser robustness against non-standard or suffixed memory output (e.g. 3833M)."""
    corrupted_output = """
==================== [2. HOST MEMORY & SWAP PRESSURE] ====================
               total        used        free      shared  buff/cache   available
Mem:           3833M       1953M        213M          3M       1959M       1880M
Swap:             0M          0M          0M
"""
    parsed = parse_diagnostic_output(corrupted_output)
    summary = parsed["summary"]
    assert summary["host_memory"]["total_mb"] == 3833
    assert summary["host_memory"]["used_mb"] == 1953
    assert summary["host_memory"]["available_mb"] == 1880
    assert summary["host_memory"]["swap_total_mb"] == 0



def test_parse_diagnostic_output_full_synthetic():
    """Verify parser handles standard diagnostic output with OOM and memory."""
    synthetic = """
==================== [1. HOST IDENTITY & UPTIME] ====================
 11:57:31 up 19 days, 22:03,  1 user,  load average: 0.48, 0.65, 0.89
Linux novaedge-dev 6.8.0-1017-aws #18-Ubuntu SMP
PRETTY_NAME="Ubuntu 24.04 LTS"

==================== [2. HOST MEMORY & SWAP PRESSURE] ====================
               total        used        free      shared  buff/cache   available
Mem:            3833        1953         213           3        1959        1880
Swap:              0           0           0

==================== [8. KERNEL OOM KILLER & SYSTEM FAULTS] ====================
[123456.78] oom-killer: gfp_mask=0x1100cca(GFP_HIGHUSER_MOVABLE), order=0
[123456.79] Killed process 1234 (python) total-vm:850000kB, anon-rss:512000kB

==================== [10. LOCAL THINGSBOARD CONNECTIVITY PROBES] ====================
--- HTTP Port 8080 Web UI Probe ---
HTTP_CODE: 200 | CONNECT_TIME: 0.002s | TOTAL_TIME: 0.184s
--- HTTP Health API Probe ---
HEALTH_API_HTTP_CODE: 200 | TIME: 0.092s
--- MQTT Port 1883 Layer 4 Socket ---
Port 1883 TCP Socket: OPEN
--- MQTT Port 8883 Layer 4 Socket ---
Port 8883 MQTTS Socket: OPEN
--- MQTT Port 1883 Layer 7 Protocol CONNACK Handshake ---
MQTT Layer 7 CONNACK: 20020004 | Latency: 0.0283s
"""
    parsed = parse_diagnostic_output(synthetic)
    summary = parsed["summary"]
    assert summary["oom_events_found"] is True
    assert summary["host_memory"]["total_mb"] == 3833
    assert summary["host_memory"]["available_mb"] == 1880
    assert summary["host_memory"]["swap_total_mb"] == 0
    assert "200" in summary["tb_probes"]["web_ui"]
    assert "20020004" in summary["tb_probes"]["mqtt_1883_l7"]


# ==============================================================================
# 8. Tri-State Log Tracking & Fatal Error Impact on Verdict
# ==============================================================================

def test_container_logs_state_tracking_and_verdict_impact():
    """Verify explicit tri-state tracking and that JVM OOM in logs forces FAIL verdict."""
    verifier = ThingsBoardHealthVerifier(host="127.0.0.1")
    # Initial state should be unevaluated
    assert verifier.results["container_logs"]["evaluated"] is False
    assert verifier.results["container_logs"]["healthy"] is None

    # Simulate healthy network probes
    verifier.results["http"]["web_ui"] = {"healthy": True}
    verifier.results["mqtt"]["l7_protocol"] = {"healthy": True}

    # Unevaluated logs with healthy network probes results in PASS
    assert verifier.evaluate_overall_verdict() == "PASS"

    # Now evaluate logs containing fatal JVM OOM
    dirty_logs = "ERROR [http-nio-8080-exec-1] java.lang.OutOfMemoryError: Java heap space"
    res = verifier.audit_container_logs_content(dirty_logs)
    assert res["evaluated"] is True
    assert res["healthy"] is False

    # Verdict must now be forced to FAIL
    assert verifier.evaluate_overall_verdict() == "FAIL"


# ==============================================================================
# 9. MQTT Return Code Acceptance (0x02 Identifier Rejected)
# ==============================================================================

def test_mqtt_return_code_acceptance():
    """Verify that return code 0x02 is accepted as healthy while 0x03 is rejected."""
    # Test server sending CONNACK with 0x02 (Identifier Rejected)
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port_02 = server_sock.getsockname()[1]

    def server_02():
        try:
            conn, _ = server_sock.accept()
            req = conn.recv(64)
            # Verify the packet is 22 bytes and has diag_tb1
            assert len(req) == 22
            assert b"diag_tb1" in req
            conn.sendall(bytes.fromhex("20020002"))
            conn.close()
        except Exception:
            pass
        finally:
            server_sock.close()

    t = threading.Thread(target=server_02, daemon=True)
    t.start()

    verifier = ThingsBoardHealthVerifier(host="127.0.0.1", port_mqtt=port_02, timeout=2.0)
    res = verifier.verify_mqtt_protocol()
    assert res["healthy"] is True
    assert res["return_code"] == 0x02
    assert res["connack_hex"] == "20020002"

    # Test server sending CONNACK with 0x03 (Server Unavailable)
    server_sock_03 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock_03.bind(("127.0.0.1", 0))
    server_sock_03.listen(1)
    port_03 = server_sock_03.getsockname()[1]

    def server_03():
        try:
            conn, _ = server_sock_03.accept()
            _ = conn.recv(64)
            conn.sendall(bytes.fromhex("20020003"))
            conn.close()
        except Exception:
            pass
        finally:
            server_sock_03.close()

    t_03 = threading.Thread(target=server_03, daemon=True)
    t_03.start()

    verifier_03 = ThingsBoardHealthVerifier(host="127.0.0.1", port_mqtt=port_03, timeout=2.0)
    res_03 = verifier_03.verify_mqtt_protocol()
    assert res_03["healthy"] is False
    assert res_03["return_code"] == 0x03
    assert res_03["connack_hex"] == "20020003"


# ==============================================================================
# 10. Unauthenticated REST Endpoint Configuration
# ==============================================================================

def test_unauthenticated_rest_endpoint_configuration():
    """Verify that verify_http_health_endpoint targets /api/noauth/userPasswordPolicy with documentation note."""
    verifier = ThingsBoardHealthVerifier(host="127.0.0.1", port_http=9)
    res = verifier.verify_http_health_endpoint()
    assert res["endpoint"] == "/api/noauth/userPasswordPolicy"
    assert "userPasswordPolicy" in res["url"]
    assert "404" in res["note"]


# ==============================================================================
# 11. Memory Unit Scaling & Container Lifecycle Tests
# ==============================================================================

def test_parse_diagnostic_output_memory_units():
    """Verify parser robustness against Gi, Mi, Ki and raw units."""
    output_gi = """
==================== [2. HOST MEMORY & SWAP PRESSURE] ====================
               total        used        free      shared  buff/cache   available
Mem:           3.8Gi       1.9Gi       0.2Gi       0.0Gi       1.9Gi       1.8Gi
Swap:          2.0Gi       0.0Gi       2.0Gi
"""
    parsed = parse_diagnostic_output(output_gi)
    summary = parsed["summary"]
    assert summary["host_memory"]["total_mb"] == int(round(3.8 * 1024))
    assert summary["host_memory"]["used_mb"] == int(round(1.9 * 1024))
    assert summary["host_memory"]["swap_total_mb"] == 2048


def test_container_lifecycle_evaluation():
    """Verify that OOMKilled: true forces verdict to FAIL."""
    verifier = ThingsBoardHealthVerifier(host="127.0.0.1")
    verifier.results["http"]["web_ui"] = {"healthy": True}
    verifier.results["mqtt"]["l7_protocol"] = {"healthy": True}
    verifier.results["container_logs"] = {"evaluated": True, "healthy": True}

    # Normal running lifecycle
    verifier.results["container_lifecycle"] = {
        "evaluated": True,
        "status": "running",
        "restarts": 0,
        "exit_code": 0,
        "oom_killed": False,
        "healthy": True,
    }
    assert verifier.evaluate_overall_verdict() == "PASS"

    # Kernel OOM killed lifecycle
    verifier.results["container_lifecycle"] = {
        "evaluated": True,
        "status": "exited",
        "restarts": 1,
        "exit_code": 137,
        "oom_killed": True,
        "healthy": False,
    }
    assert verifier.evaluate_overall_verdict() == "FAIL"


# ==============================================================================
# 12. Remote Bash Python Probe Syntax Compilation Verification
# ==============================================================================

def test_embedded_python_bash_commands_syntax():
    """Verify that python commands embedded in remote bash scripts compile cleanly without SyntaxError."""
    import inspect
    import re

    def verify_script_python_syntax(script_text: str, source_name: str):
        matches = re.findall(r'python3\s+-c\s+["\'](.*?)["\']\s*(?:2>/dev/null|\|\||$)', script_text, re.DOTALL)
        assert len(matches) > 0, f"Expected at least one python3 -c command in {source_name}"
        for idx, cmd in enumerate(matches):
            compiled = compile(cmd, f"<{source_name}_cmd_{idx}>", "exec")
            assert compiled is not None, f"Failed compiling outer command in {source_name}"

            exec_match = re.search(r'exec\s*\(\s*["\'](.*?)["\']\s*\)', cmd, re.DOTALL)
            if exec_match:
                inner_raw = exec_match.group(1)
                inner_code = inner_raw.encode("utf-8").decode("unicode_escape")
                inner_compiled = compile(inner_code, f"<{source_name}_inner_{idx}>", "exec")
                assert inner_compiled is not None, f"Failed compiling inner exec payload in {source_name}"

    # 1. Test diagnose_coexistence_live.py probe
    diag_script = DIAGNOSTIC_COMMAND_BLOCKS[0]
    verify_script_python_syntax(diag_script, "DIAGNOSTIC_COMMAND_BLOCKS")

    # 2. Test verify_tb_health_live.py probe
    remote_script = getattr(ThingsBoardHealthVerifier, "REMOTE_SSM_SCRIPT", None)
    if not remote_script:
        src = inspect.getsource(ThingsBoardHealthVerifier.execute_remote_ssm_probe)
        m = re.search(r'remote_script\s*=\s*r?"""(.*?)"""', src, re.DOTALL)
        assert m is not None, "Unable to extract remote_script from execute_remote_ssm_probe"
        remote_script = m.group(1)

    verify_script_python_syntax(remote_script, "remote_script")


# ==============================================================================
# 13. Unit and Scale Awareness in _parse_int()
# ==============================================================================

def test_parse_int_unit_and_scale_awareness():
    """Verify _parse_int handles Gi, G, Mi, M, Ki, K, floats, and guards against non-strings."""
    # Scale conversions
    assert _parse_int("4.0Gi") == 4096
    assert _parse_int("1.5G") == 1536
    assert _parse_int("0.5Gi") == 512
    assert _parse_int("3833M") == 3833
    assert _parse_int("1953Mi") == 1953
    assert _parse_int(3833) == 3833
    assert _parse_int("0M") == 0
    assert _parse_int("2048Ki") == 2
    assert _parse_int("1024K") == 1
    assert _parse_int("512Ki") == 1

    # Non-strings and invalid inputs
    assert _parse_int(None) is None
    assert _parse_int(True) is None
    assert _parse_int(False) is None
    assert _parse_int([]) is None
    assert _parse_int({}) is None
    assert _parse_int(object()) is None
    assert _parse_int("RAM_ERR_123") is None
    assert _parse_int("N/A") is None
    assert _parse_int("") is None

    # End-to-end integration via parse_diagnostic_output section 2
    raw_template = """
==================== [2. HOST MEMORY & SWAP PRESSURE] ====================
               total        used        free      shared  buff/cache   available
Mem:           {total}       {used}        213M          3M       1959M       {avail}
Swap:             0M          0M          0M
"""
    # Test GiB conversion (4.0Gi = 4096MB)
    out_gi = raw_template.format(total="4.0Gi", used="1.5G", avail="0.5Gi")
    p_gi = parse_diagnostic_output(out_gi)["summary"]["host_memory"]
    assert p_gi["total_mb"] == 4096
    assert p_gi["used_mb"] == 1536
    assert p_gi["available_mb"] == 512

    # Test KiB conversion (2048Ki = 2MB, 1024K = 1MB)
    out_ki = raw_template.format(total="2048Ki", used="1024K", avail="512Ki")
    p_ki = parse_diagnostic_output(out_ki)["summary"]["host_memory"]
    assert p_ki["total_mb"] == 2
    assert p_ki["used_mb"] == 1
    assert p_ki["available_mb"] == 1

    # Test standard unadorned integer from free -m
    out_std = raw_template.format(total="3833", used="1953", avail="1880")
    p_std = parse_diagnostic_output(out_std)["summary"]["host_memory"]
    assert p_std["total_mb"] == 3833
    assert p_std["used_mb"] == 1953
    assert p_std["available_mb"] == 1880


# ==============================================================================
# 14. Docker Inspect Structured Parsing and OOM Verdict Integration
# ==============================================================================

def test_docker_inspect_parsing_and_oom_verdict():
    """Verify Docker inspect parses lifecycle state and OOMKilled forces FAIL verdict."""
    verifier = ThingsBoardHealthVerifier()

    # Simulate healthy network probes
    verifier.results["http"]["web_ui"] = {"healthy": True}
    verifier.results["mqtt"]["l7_protocol"] = {"healthy": True}

    # Simulate remote stdout with Docker inspect output (healthy container)
    stdout_clean = """
=== [TB_LOCAL_PROBES_START] ===
--- 1. HTTP 8080 Web UI ---
HTTP_CODE:200|CONNECT_TIME:0.002|TOTAL_TIME:0.050
--- 2. HTTP Health / Policy API ---
HEALTH_API_HTTP_CODE:200|TOTAL_TIME:0.040
--- 4. MQTT 1883 Protocol CONNACK ---
MQTT_CONNACK:20020002|TIME:0.003
--- 5. Container Lifecycle & Inspect ---
Status: running | Restarts: 0 | ExitCode: 0 | OOMKilled: false | StartedAt: 2026-10-29T11:43:10.123Z
--- 6. Recent Container Logs Tail 500 ---
2026-10-29 11:43:10.123 [main] INFO  o.t.s.ThingsboardServerApplication - Starting
=== [TB_LOCAL_PROBES_END] ===
"""
    # Parse simulated stdout
    lines = stdout_clean.splitlines()
    for line in lines:
        if "Status:" in line and "OOMKilled:" in line:
            parts = {k.strip(): v.strip() for kv in line.strip().split("|") if ":" in kv for k, v in [kv.split(":", 1)]}
            oom = parts.get("OOMKilled", "false").lower() == "true"
            status = parts.get("Status", "").lower()
            exit_code = int(parts.get("ExitCode", 0))
            verifier.results["container_lifecycle"] = {
                "evaluated": True,
                "status": parts.get("Status"),
                "restarts": int(parts.get("Restarts", 0)),
                "exit_code": exit_code,
                "oom_killed": oom,
                "started_at": parts.get("StartedAt"),
                "healthy": (status == "running") and not oom and (exit_code == 0),
            }

    assert verifier.results["container_lifecycle"]["evaluated"] is True
    assert verifier.results["container_lifecycle"]["oom_killed"] is False
    assert verifier.evaluate_overall_verdict() == "PASS"

    # Now simulate OOM-killed container
    verifier.results["container_lifecycle"]["oom_killed"] = True
    verifier.results["container_lifecycle"]["healthy"] = False
    assert verifier.evaluate_overall_verdict() == "FAIL"

    # Now simulate exited container with non-zero exit code
    verifier.results["container_lifecycle"]["oom_killed"] = False
    verifier.results["container_lifecycle"]["status"] = "exited"
    verifier.results["container_lifecycle"]["exit_code"] = 1
    verifier.results["container_lifecycle"]["healthy"] = False
    assert verifier.evaluate_overall_verdict() == "FAIL"


# ==============================================================================
# 15. Aligned Health REST Endpoint Contract
# ==============================================================================

def test_aligned_health_endpoint_contract():
    """Verify diagnose_coexistence_live probes /api/noauth/userPasswordPolicy instead of /api/noauth/health."""
    diag_script = DIAGNOSTIC_COMMAND_BLOCKS[0]
    assert "/api/noauth/userPasswordPolicy" in diag_script
    assert "/api/noauth/health" not in diag_script


# ==============================================================================
# 16. Un-evaluated Log Display in print_summary()
# ==============================================================================

def test_unevaluated_log_display_in_summary(capsys):
    """Verify print_summary() prints external network probe mode notice when logs are unevaluated."""
    verifier = ThingsBoardHealthVerifier(host="127.0.0.1")
    assert verifier.results["container_logs"]["evaluated"] is False

    verifier.print_summary()
    captured = capsys.readouterr().out
    assert "[3] Container Anomaly Log Audit" in captured
    assert "Log audit not evaluated (external network probe mode)" in captured


