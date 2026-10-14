# Release v0.1.0-deterministic-default

- Release date (UTC): 2026-10-14
- Branch: `release/v0.1.0` (local only; **not pushed**)
- Base: `1332c66` (local, unpushed) on top of `origin/main` = `ee450c2`
- Release config: `configs/release.yaml` (`release_name: r5-reliability-recovery-stage-f`, `policy_version: ticket-policy-v1`, `validator_version: stage-c-field-validator-v1`)

## 1. Tag

Tag name: `v0.1.0-deterministic-default` (annotated)

Tag target: the latest commit that edits **this file** (after `9cbeec1`).
A report cannot contain its own commit hash; resolve with
`git rev-list -n 1 v0.1.0-deterministic-default`.

Tag message:

```
v0.1.0-deterministic-default

- Deterministic renderer is the sole operational ticket path.
- LLM explanation layer is experimental and disabled by default
  (EXPERIMENTAL_LLM_ENABLED=false).
- Dispatch, ThingsBoard alarm creation, and notifications are disabled
  per configs/release.yaml and the src/ release path; see
  reports/release_v0.1.0.md section 5 for known gaps L1-L7.
- Human review is pending (0 completed review forms).
- Independent semantic accuracy is not measured.
```

## 2. Commits in this release (oldest first)

| Hash | Message |
|---|---|
| `db4ac51` | chore(repo): deduplicate .gitignore and stop tracking model weights |
| `17f199e` | feat(tickets): deterministic ticket renderer, field validator and release startup check |
| `0fade28` | feat(eval): clean evaluation splits, Stage E comparison and R5 evidence verification |
| `9fd1542` | fix(explain): tighten explanation validator; update production training script |
| `283bc56` | test: add ticket, field-validator, split-integrity, Stage E and release startup tests |
| `ccac628` | feat(experimental): reasoning-model dataset, fine-tune and inference scripts |
| `1f99776` | chore(models): update GBM model metadata and feature lists (weights untracked) |
| `df1f0c5` | docs: update README and model card; add Phase 0 plan |
| `9cbeec1` | docs(reports): add R5 Stage A-F evidence, review pack and R6 release readiness |
| (tag target) | docs(release): add v0.1.0 release report |

Pre-existing reports modified before this stage (`phase_6_explanation_layer_report.md`,
`phase_r5_explanation_layer_remediation.md`, `remediated_explanation_benchmark.json`)
were committed as found (existing R5 corrections); not edited in G1.

## 3. Test and release-check results

Interpreter: `.venv/Scripts/python.exe` (Python 3.13.13, pytest 9.0.2).
Run from repo root with `-p no:cacheprovider --basetemp=<scratch>`.

| Check | Command | Exit | Result |
|---|---|---|---|
| Full pytest suite | `python -m pytest tests -q` | 0 | 245 passed |
| R5 evidence verification | `verify_r5_evidence.main()` with `OUT_FILE` redirected to scratch (see note) | 0 | 5/5 checks OK; output byte-identical to stored `reports/phase_r5_stage_a_reproduction.json` |
| Split verification | `python src/eval/verify_splits.py` | 0 | PASS (UNSEEN_DEVICES: 150) |
| Stage C field-validator suite | `python -m pytest tests/test_field_level_validator.py -q` | 0 | 27 passed |
| Stage E comparison verification | `python -m pytest tests/test_stage_e_comparison.py -q` | 0 | 5 passed |
| Release startup check | `python src/tickets/startup_check.py` | 0 | `startup check: PASS` |

Note: `python verify_r5_evidence.py` unconditionally overwrites the frozen Stage A
artifact (`verify_r5_evidence.py:491`). To respect the no-modification rule it was
run as:

```
python -c "import sys,pathlib,verify_r5_evidence as v; v.OUT_FILE=pathlib.Path('<scratch>/stage_a_reproduction_rerun.json'); sys.exit(v.main())"
```

`src/eval/run_stage_e_comparison.py` was **not** executed (its `__main__` regenerates
the Stage E raw results, review forms and blinding key). Stage E verification is the
stored-artifact test suite above, not a re-run.

**Fresh-clone caveat:** these results were produced in the release workspace, which
holds files git does not track (model weights untracked in `db4ac51`; `data/eval/`
and `data/processed/` ignored). A clean clone of the tag will FAIL the startup check
and the data/model-dependent tests until those artifacts are restored and match the
hashes in section 4. "245 passed" is a property of the workspace, not the tagged tree.

Artifact immutability: SHA-256 of all 97 files under `reports/`, `data/eval/`,
`models/`, `configs/` taken before and after all checks — **no differences**.

## 4. Artifact hashes (SHA-256)

Model weights are no longer tracked in git (see §6); local files are identified by hash.

### Pinned in `configs/release.yaml` (verified by startup check)

| Artifact | Path | SHA-256 |
|---|---|---|
| LightGBM outage predictor | `models/outage_predictor/remediated_lightgbm_clean.joblib` | `799fae217485a0b56b340802171329d816e557661f43591be62511b155d2e5bf` |
| Platt calibrator | `models/outage_predictor/remediated_platt_calibrator.joblib` | `0958ce80bb3407cca838d87c2c3e28efb83aad601e21cb4c18de463bd6eddbed` |
| Qwen merged (experimental, OFF) | `models/qwen_reasoning_outage_predictor/remediated_final_model/model.safetensors` | `be45761a2a66ed5dc5911009a1c3784af1d2d4dc5b392b9343b76cdbfe58e051` |
| Qwen LoRA adapter (experimental, OFF) | `models/qwen_reasoning_outage_predictor/remediated_lora_adapter/adapter_model.safetensors` | `db16593c3500c4d09c5dd4bfb4aae448a6ca91856eaf5db25d6d314681843438` |

### Evaluation manifests

`manifest_sha256` is computed over the manifest body **excluding** its own
`manifest_sha256` field (`src/tickets/startup_check.py:147`); whole-file hashes are
listed for reference and intentionally differ.

| Split | Manifest body hash (pinned) | Whole-file hash | Data file hash (pinned) |
|---|---|---|---|
| train | `a8a3c92e2f73324f47f04ddc929438e10ae25ee97071e169a7e9585e97b17e4e` | `20e673369681e0126178c8a2f19e8855674e13248f970bd6fd5b874648d15f80` | `b5afcf84e873031387ea21dd3be726f1b829b79570cd8f7193d5b602fc5eadbe` |
| future_known_devices | `7e8927dd4fe51d598407ff367853758a3fbc680c8bb4ed748a5e888e8097b72c` | `655431ec6f5e3d15960b4c3945aaf576ae2e7b723ae2e8443de08c54809a05e2` | `54e224628cccd738e845d1bddc3477eb70fce2a68205200a8da5ed8027429ace` |
| unseen_devices | `6594db4569235fe538a19b6eda855d3c77b4a688c2f7a8fbf9efdffe593183c5` | `cc91320623bddd51c5169384e6a9ce89220ef4538cf9f9743aebda391d3e3b68` | `0338742c77f0be7040360373e18292868b6377b4fc928b2f160efc186dbcec46` |

### Release config and other artifacts (not pinned)

| Artifact | Path | SHA-256 |
|---|---|---|
| Release config | `configs/release.yaml` | `a70f977c4f1b378fc8976e8056e210a839ec4f65b0befc794ff52c8396ebcb15` |
| Nightly GBM scorer model | `models/gbm_outage_oct2026/model.joblib` | `ca9fc5784db01a2b39129b119a9bbaa59f956b6766c028880949401aca55d1b2` |
| Nightly GBM feature names | `models/gbm_outage_oct2026/feature_names.json` | `191e7595258bbbb6cf24ed8981e811467e79caa08f6b74daaf2df7931c2ca01b` |
| Nightly GBM metadata | `models/gbm_outage_oct2026/metadata.json` | `7d9af88ff1747531059c47b91b1e09b98af5535b0a1f05c9bca8160798039fda` |
| Legacy LightGBM (unused by release) | `models/outage_predictor/lightgbm_outage_predictor.joblib` | `430f1afc5c1d549bc61a933dcec9d777085a6aece04409de326731ff44a45bc8` |
| Legacy Platt (unused by release) | `models/outage_predictor/platt_calibrator.joblib` | `96fe8a8916b666f4be792aaab2b3b4c1758b40f7c259506e93e4a82387bacc0d` |
| Legacy scaler | `models/outage_predictor/standard_scaler.joblib` | `48d7f8d776250a6c14c83eaf07ca38abf3c33e83dd1be1ff0e9aef23aaabf91d` |
| Review pack blank forms | `reports/phase_r5_stage_e_human_review_pack/blank_review_forms_30_cases.json` | `b21bd594e23814e70be508c8e7607b0a9adbc915e5685a31c6524fae31b54547` |
| Review pack blinding key | `reports/phase_r5_stage_e_human_review_pack/blinding_key_DO_NOT_OPEN_DURING_REVIEW.json` | `677ff020aca5049669a3e88db93925355b2e37bcceaae471b811349b8c7634d4` |
| Review protocol | `reports/phase_r5_stage_e_human_review_pack/README_REVIEW_PROTOCOL.md` | `62e126d11ff1af87ad0add25a8780d6c8791f6dc47309b0eb74b132d57a1b6e7` |

`models/gbm_outage_production_v2/model.joblib` is byte-identical to the oct2026 model.

## 5. Safety status

| Control | Status | Evidence |
|---|---|---|
| Deterministic renderer | ENABLED, sole operational ticket path | `release.yaml: ticket_renderer: enabled`; `src/tickets/renderer.py` (pure string building) |
| Experimental LLM | DISABLED by default | `release.yaml: experimental_llm_enabled: false`; `src/tickets/llm_bridge.py:30` |
| Telemetry mode | READ-ONLY | `release.yaml: telemetry_mode: read-only`; `src/` issues only GET + login POST to ThingsBoard |
| ThingsBoard alarm creation | DISABLED | no alarm-write calls in `src/` |
| Dispatch | DISABLED | no dispatch path exists |
| Notifications | DISABLED in release config | see limitation L3 |
| Human review | PENDING | 0 completed forms; blank pack only |
| Semantic accuracy | NOT MEASURED | `reports/phase_r5_stage_e_llm_value_assessment.md` |

### Correction to earlier report

`reports/final_claims_audit.md` claim 6 labels Stage E aggregates as "REPRODUCED".
`tests/test_stage_e_comparison.py` only reads stored JSON; Stage E was not re-run.
Correct status: **STORED (consistency-checked), not reproduced.** The earlier report
is left unedited per the no-modification rule.

### Known limitations (found in G1 audit; not fixed in this stage)

- **L1 (HIGH)** — The startup check is not invoked by the scheduled nightly chain
  (`run_nightly_risk.bat` → `nightly_risk_chain.py`). Fail-closed holds only when run
  manually/in tests. Must be wired in before G2 shadow launch.
- **L2 (HIGH)** — The nightly scorer (`score_daily_risk.py:32`) loads
  `models/gbm_outage_oct2026/model.joblib`, which is **not** pinned in `release.yaml`.
  The pinned artifacts are the remediated LightGBM + Platt pair. The G2 shadow
  scheduler must load pinned artifacts only, or this model must be pinned.
- **L3 (MEDIUM)** — `run_nightly_audit.py:57-94` sends Slack/SMTP messages whenever
  `SLACK_WEBHOOK` / `SMTP_PASSWORD` env vars are set, ignoring `notifications: disabled`.
  Not registered by repo schedulers. It also hard-codes a recipient address.
- **L4 (HIGH)** — `set_experimental_llm_enabled(True)` (`llm_bridge.py:39`) has no
  override gate and the flag is not initialised from `release.yaml`.
- **L5 (MEDIUM)** — `startup_check.py:90-96` treats only `True`/`"enabled"` as unsafe;
  other truthy spellings pass. Should allow-list exact safe values.
- **L6 (MEDIUM)** — `TB_Full_Harvest_v11.ipynb` cell 28 contains a ThingsBoard attribute
  write-back guarded only by `WRITE_BACK = False`.
- **L7 (MEDIUM)** — ThingsBoard TLS verification is disabled in the nightly pull
  (`nightly_risk_chain.py:72`, `src/ingest/pull_data.py:43`).

Text-file hashes above are of the LF working copies; a checkout with
`core.autocrlf=true` may produce different hashes for text files (weights and data
files are unaffected).

## 6. Repository hygiene and push blockers

- 10 model weight files and `desktop.ini` untracked in `db4ac51` (local copies kept).
- Ignored: `data/` (5.5 GB), `archive/` (1 GB legacy run), session transcripts,
  `.playwright-mcp/`, permission-locked pytest audit dirs, tool caches.
- Deleted: stray `0.26.0` (pip redirect output).
- **PUSH BLOCKER:** unpushed base commit `1332c66` contains two 988 MB `model.safetensors`
  blobs (over GitHub's 100 MB limit). Rewrite history or migrate to LFS before any push.
- **Disclosure note:** `origin` is a PUBLIC GitHub repository. By owner decision this
  commit set includes the review pack (including the blinding key), raw evaluation
  JSON with real device UUIDs and branch names, and `PHASE_0_PLAN.md` naming the
  ThingsBoard tenant host. Pushing publishes these and exposes the blinding key to any
  reviewer with repo access. Review before pushing.
