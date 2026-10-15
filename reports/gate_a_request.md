# Formal Gate A Authorization Request: Live Shadow Mode Polling

**Target:** ThingsBoard Production Tenant  
**Requester:** ML / Telemetry Pipeline Engineering  
**Date:** 2026-10-15  
**Status:** PENDING TENANT OWNER APPROVAL  

---

## 1. What Is Being Requested

Authorization is requested from the tenant owner to resume the **Live Shadow Scorer** in read-only polling mode:
- **Target Fleet:** 158 registered commercial bank branch telemetry devices (135 active production bank branch gateways across BOI, SBI, BOB, and Canara).
- **Operation:** Periodic read-only polling of device telemetry timeseries (15-minute resolution) and historical alarm events (`/api/plugins/telemetry/...`).
- **Purpose:** Shadow evaluation of the advisory 24-hour power-outage warning feed and deterministic diagnostic ticket generation without effectful actions.

---

## 2. Strictly Prohibited Actions (Guaranteed & Enforced)

The pipeline is technically constrained and fail-closed against any operational interference:
1. **NO ThingsBoard Alarm Creation:** The system cannot create, mutate, acknowledge, or clear alarms in ThingsBoard.
2. **NO Technician Dispatch:** The system cannot generate work orders, dispatch field technicians, or trigger ticketing integrations.
3. **NO External Notifications:** The system cannot send emails, SMS, webhooks, or push alerts to branch staff or operators.
4. **NO Telemetry Writes:** Telemetry handling is strictly `read-only`. The HTTP client issues only `GET` and read-only token authentication `POST` requests; all write operations are disabled and blocked.
5. **NO Unsafe Model Inference:** The experimental Qwen reasoning LLM is disabled by default (`experimental_llm_enabled: false`). Only deterministic ticket rendering and Platt-calibrated LightGBM scoring run.

---

## 3. Data Handling & Privacy Guarantees

- **Local Storage Only:** Data is stored locally on the designated host machine under `data/live/` and is strictly gitignored.
- **Append-Only Accounting:** Operational audit logs (`cycles.jsonl`, `shadow_run.log`, `task_wrapper.log`) are append-only; past entries are never deleted or modified during operation.
- **Retention Policy:** Local raw cycle caches and prediction logs are governed by the tenant owner's retention rule (e.g., 90-day rolling local purge).
- **Identifier Policy:** External and operational reports use aggregated metrics or internal hashed references. Customer identifiers, IP addresses, credentials, and raw gateway secrets are never logged or exported in public reports.

---

## 4. Required Tenant Owner Actions

To grant approval, the tenant owner must:
1. Copy the template file:
   ```powershell
   Copy-Item configs/gate_a_approval.template.yaml configs/gate_a_approval.yaml
   ```
2. Open `configs/gate_a_approval.yaml` and complete **every** field with authentic, non-placeholder values:

```yaml
gate_a_approval:
  approver_name: "<tenant owner full name>"
  approver_role: "<official title / organizational role>"
  approval_date: "2026-10-15"
  approved_scope: "Read-only ThingsBoard telemetry and alarm-history polling for 158 registered devices"
  prohibited_actions:
    - "ThingsBoard alarm creation"
    - "technician dispatch"
    - "external notifications"
    - "telemetry writes"
  retention_policy: "90 days local append-only storage, then automated prune"
  identifier_policy: "Aggregate metrics and internal IDs only; no raw credentials or IP addresses in reports"
  evidence_link: "<ticket ID, email thread reference, or internal sign-off doc>"
```

*Note: Any field containing `<...>`, `pending`, `TBD`, or left empty will be rejected by the validation parser.*

---

## 5. Technical Enforcement Notice

The live shadow scorer (`src/live/shadow_run.py`) and scheduler health monitor (`src/live/scheduler_health.py`) strictly enforce Gate A validation prior to executing any ThingsBoard requests:
> **The scorer refuses to poll ThingsBoard and immediately exits with status `REFUSED_GATE_A_APPROVAL` if `configs/gate_a_approval.yaml` is absent, unparseable, or contains placeholder values.**

No live cycles or network polling will be initiated until this approval file is completed by the authorized tenant owner.
