# POST-REMEDIATION READ-ONLY AUDIT REPORT — AI IMAGE STUDIO (REAL-ESRGAN v0.3.0)

**Project Directory:** `E:\AI Image Studio\RealESRGAN-v030`  
**Git Branch:** `clean-main`  
**Current Commit HEAD:** `37361c773cf07c909ce3650350d5700dfcc1f399`  
**Upstream:** `origin/main`  
**Audit Timestamp:** 2026-11-10T11:07:00+07:00  
**Audit Policy:** STRICTLY READ-ONLY (Zero source/database modifications, zero GPU inference, zero git state changes)  
**Previous Reference Reports:**  
- `docs/audits/PHASE_5.5_OPERATIONAL_VALIDATION.md`  
- `AUDIT_AND_REMEDIATION_REPORT.md`

---

## EXECUTIVE AUDIT SUMMARY

This independent, read-only audit was conducted to rigorously verify the engineering remediation claims documented in `AUDIT_AND_REMEDIATION_REPORT.md`.

Every modified module, state machine transition, recovery routine, queue lifecycle, video pipeline lock, upload handler, and test suite was inspected directly against the physical codebase on disk.

### Audit Verdict: **CONDITIONALLY ACCEPTED**
- **Critical Defects:** **0 remaining**. All core defects (concurrency races, state machine overwrites, startup recovery gaps, unvalidated output completion, worker deadlocks, video pipeline deadlocks, and thread-unsafe workspace deletions) have been verified as remediated and functioning.
- **Outstanding Observations (Non-Critical):** Two configuration/operational nuances were identified:
  1. *Video Upload HTTP Ceiling:* Global `app.config['MAX_CONTENT_LENGTH']` is bound to `security.max_file_size_mb` (100 MB), which triggers an HTTP 413 ceiling on video uploads exceeding 100 MB before the route-level 2048 MB streaming check can execute.
  2. *Startup Hook Placement:* `run_startup_checks()` is invoked within `if __name__ == '__main__':` (standard for `python app.py` and `Start_ImageStudio.bat`), while `worker_thread` starts at module import. This works as designed for standalone execution, but should be centralized in an app-factory hook if ever deployed under a multi-process WSGI server.

---

## AUDIT 1 — GIT INTEGRITY

| Audit Item | Status | Risk Level |
| :--- | :---: | :---: |
| Branch & Commit Verification | **PASS** | LOW |
| Modified vs Untracked Inventory | **PASS** | LOW |
| Assume-Unchanged / Skip-Worktree Flags | **PASS** | LOW |
| Working Tree Discrepancy Clarification | **PASS** | LOW |
| Backup Hash Verification | **NEEDS REVIEW** | LOW |

### 1.1 Findings & Evidence
- **Current Branch & HEAD:** `clean-main` at commit `37361c773cf07c909ce3650350d5700dfcc1f399` (`docs: replace upstream README with AI Image Studio documentation`). No unauthorized commits were created (`git log -2 --oneline` confirms commit history is unchanged).
- **Git Status:**
  ```text
  ## clean-main...origin/main
   D LICENSE
   M app.py
   D assets/realesrgan_logo.png
   M job_manager.py
   M video/video_job_manager.py
   M video/video_pipeline.py
  ?? AUDIT_AND_REMEDIATION_REPORT.md
  ?? POST_REMEDIATION_READONLY_AUDIT.md
  ?? backups/
  ?? data/
  ?? tests/test_output_validation_cancellation_history.py
  ?? tests/test_phase1_atomic_transitions_and_retry.py
  ?? tests/test_recovery_and_queue_lifecycle.py
  ?? tests/test_video_lifecycle_and_upload_safety.py
  ```
  *Note on deleted tracked files & untracked directories:*
  - `LICENSE` and `assets/realesrgan_logo.png` were removed in earlier repository standardization cleanups prior to this remediation.
  - `data/` appears in untracked status because while `data/jobs.db` is ignored in `.gitignore`, SQLite's Write-Ahead Logging auxiliary files (`jobs.db-wal` and `jobs.db-shm`) are present and not explicitly listed in `.gitignore`.
- **Index Flags (`assume-unchanged` / `skip-worktree`):**
  - Inspected via `git ls-files -v | Select-String "^[^H]"`.
  - Result: 0 entries. All tracked files have status `H` (standard cached/tracked). No stealth hiding or flag bypasses are in effect.
- **Ignored Directories:**
  - `.gitignore` correctly ignores runtime directories: `logs/`, `outputs/`, `thumbnails/`, `uploads/`, `weights/`, `__pycache__/`, and `data/jobs.db`.
- **Working Tree Explanation:**
  - The phrase in previous reports stating *"the working tree was clean on clean-main"* referred strictly to the pre-remediation baseline state before edits were made.
  - The 4 core modified files (`app.py`, `job_manager.py`, `video/video_job_manager.py`, `video/video_pipeline.py`) are actively modified in the working tree and kept unstaged/uncommitted to adhere strictly to the rule: *"Do not commit, push, reset or change Git configuration."*
- **Backup vs. Current Hashes:**
  - Baseline backups stored in `backups/remediation_20261110_103114/` match the initial state:
    - `job_manager.py.bak`: `b1e2b4fb1cb8405a0c8c915b985bd81275339d5d56bec631b13f9acd796021fe`
    - `app.py.bak`: `ed1b483fee6d06afe02337953e84e26aa7a21454f249a8f712325fdac917c726`
    - `video/video_job_manager.py.bak`: `2c1e9ad2510f041f447767e9c435a04e6b3114f9cd671f0e1ede6c873707347c`
    - `video/video_pipeline.py.bak`: `9dc6e704c506de623c64b32c7d420112dd0054c6c5a0dc34ef74d1f029bb3f84`
  - *Discrepancy in `AUDIT_AND_REMEDIATION_REPORT.md` Section 9:* The previous report recorded intermediate hashes for the 4 modified files (`04d6e99...`, `43bbecf...`, etc.) prior to the subsequent `RLock` deadlock resolution in `video_pipeline.py`. The actual current hashes on disk are:
    - `job_manager.py`: `c55ec806895f30ff40cd643004d516cddb93cd69185f6b51bb3d0110c9ffd75c`
    - `app.py`: `a00f0771af4ba089e7d1efb5196ef57edc2e0db77a7b4e789d4bdd5d5754b665`
    - `video/video_job_manager.py`: `012fc9a89f5cb23a48a7ec7c8fbd6382f84ce0761c1785fd0d67028653d6e0d5`
    - `video/video_pipeline.py`: `6a87b7378d4ca89464eb08b96de7e81a02fa3fb20fffadc492b67498b458c327`

---

## AUDIT 2 — SQLITE STATE MACHINE (`job_manager.py`)

| Audit Item | Status | Risk Level |
| :--- | :---: | :---: |
| Atomic Conditional UPDATE | **PASS** | LOW |
| Allowed Source State Filter | **PASS** | LOW |
| Rowcount Validation (`cur.rowcount > 0`) | **PASS** | LOW |
| Terminal State Protection | **PASS** | LOW |
| Retry Count Atomicity (`retry_count < max_retries`) | **PASS** | LOW |
| No Retry After Cancellation | **PASS** | LOW |
| Transaction Boundaries & WAL Mode | **PASS** | LOW |
| Startup Recovery Multi-State Coverage | **PASS** | LOW |

### 2.1 Findings & Evidence
- **Exact File:** [`job_manager.py`](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/job_manager.py)
- **Atomic Conditional UPDATE (`transition_status`, lines 521–558):**
  ```python
  allowed_sources = [s for s, targets in VALID_TRANSITIONS.items() if new_status in targets]
  placeholders = ", ".join("?" for _ in allowed_sources)
  where_conditions.append(f"status IN ({placeholders})")
  where_params.extend(allowed_sources)
  ...
  sql = f"UPDATE jobs SET {', '.join(updates)} WHERE {' AND '.join(where_conditions)}"
  cur = conn.execute(sql, params + where_params)
  conn.commit()
  if cur.rowcount > 0:
      return True
  ```
  Transitions execute as a single SQL statement. If another process or thread has altered the status in the interim, `cur.rowcount == 0` and the method logs a warning and returns `False`.
- **Terminal State Protection (lines 51–53, 532–540):**
  `VALID_TRANSITIONS[STATUS_COMPLETED]` and `VALID_TRANSITIONS[STATUS_CANCELLED]` are empty sets (`set()`). Under `force=True`, lines 532–534 enforce:
  ```python
  if new_status in (STATUS_PROCESSING, STATUS_RETRY_WAIT, STATUS_VALIDATING):
      where_conditions.append("status NOT IN (?, ?, ?)")
      where_params.extend([STATUS_CANCEL_REQUESTED, STATUS_CANCELLED, STATUS_COMPLETED])
  elif new_status == STATUS_QUEUED:
      where_conditions.append("status NOT IN (?, ?)")
      where_params.extend([STATUS_COMPLETED, STATUS_QUEUED])
  ```
  Neither `COMPLETED` nor `CANCELLED` can be overwritten by active states even with `force=True`. Only manual user reset via `reset_for_retry()` (lines 850–870) can reset a failed/cancelled job to `QUEUED`.
- **Retry Count Atomicity (`schedule_retry`, lines 601–612):**
  ```python
  UPDATE jobs SET
      status = ?,
      retry_count = retry_count + 1,
      error_message = ?,
      is_oom = ?,
      updated_at = ?
  WHERE id = ?
    AND retry_count < max_retries
    AND status IN (?, ?, ?)
  ```
  The retry increment and eligibility check are bound atomically to SQLite. If `status` is not in `(PROCESSING, VALIDATING, QUEUED)` or `retry_count >= max_retries`, `cur.rowcount` is 0.
- **Cancellation Immunity (lines 630–632):**
  If current status is `CANCEL_REQUESTED` or `CANCELLED`, `schedule_retry()` aborts retry without modifying the state.
- **WAL & Busy Timeout (`get_connection`, lines 91–98):**
  `conn.execute("PRAGMA journal_mode=WAL")` and `conn.execute("PRAGMA busy_timeout=30000")` are executed on every connection.

---

## AUDIT 3 — STARTUP AND QUEUE LIFECYCLE (`app.py`, `job_manager.py`)

| Audit Item | Status | Risk Level |
| :--- | :---: | :---: |
| Recovery Order Before Processing | **PASS** | LOW |
| Exactly-Once Startup Enqueuing | **PASS** | LOW |
| QUEUED Restoration | **PASS** | LOW |
| RETRY_WAIT Policy Handling | **PASS** | LOW |
| CANCEL_REQUESTED Finalization | **PASS** | LOW |
| Exclusion of Terminal Jobs from Queue | **PASS** | LOW |
| Queue `task_done()` Guarantees | **PASS** | LOW |
| Sentinel Shutdown Cleanliness | **PASS** | LOW |
| Worker Exception Resilience | **PASS** | LOW |
| Hook Placement for Non-Main Launchers | **NEEDS REVIEW** | MEDIUM |

### 3.1 Findings & Evidence
- **Exact File:** [`app.py`](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/app.py)
- **Startup Sequence (`run_startup_checks`, lines 745–812):**
  1. `job_mgr.recover_on_startup(validator_func=validate_image_file)` executes first.
  2. Identifies interrupted `PROCESSING` and `VALIDATING` jobs.
  3. Finalizes `CANCEL_REQUESTED` to `STATUS_CANCELLED`.
  4. Recovers `RETRY_WAIT` jobs: if `retry_count < max_retries`, transitions to `STATUS_QUEUED`; otherwise `STATUS_FAILED`.
  5. Returns `eligible_job_ids` containing only valid `STATUS_QUEUED` jobs.
  6. Filters RAM cache for `pending` jobs and pushes them to `processing_queue` guarded by `if not _startup_queue_restored: ... _startup_queue_restored = True`.
- **Queue Worker Lifecycle (`processing_worker`, lines 1270–1344):**
  - Line 1274: `job_data = processing_queue.get(block=True)`
  - Sentinel check (lines 1279–1283): If `job_data is None`, calls `processing_queue.task_done()` and breaks.
  - Execution block: Wrapped in `try: ... except Exception: ... finally: is_processing.clear(); processing_queue.task_done()`.
  - Every `get()` is guaranteed exactly one `task_done()`.
  - Batch worker catches `Exception` at line 1339 with `exc_info=True`, preventing worker thread crashes from unhandled errors.
- **Operational Nuance (`run_startup_checks` entrypoint):**
  - `run_startup_checks()` is invoked at line 3097 within `if __name__ == '__main__':`.
  - When starting via `python app.py` or `Start_ImageStudio.bat`, `run_startup_checks()` executes before Flask opens incoming connections (`app.run`), so no race condition exists.
  - If `app.py` is ever imported as a WSGI module in an external multi-worker container (e.g., Gunicorn or Waitress), `run_startup_checks()` would need an explicit trigger in an application factory.

---

## AUDIT 4 — OUTPUT VALIDATION AND HISTORY (`app.py`)

| Audit Item | Status | Risk Level |
| :--- | :---: | :---: |
| 0-Byte Output Rejection | **PASS** | LOW |
| Deep Pillow Structural Validation | **PASS** | LOW |
| `/api/result/<job_id>` Status Guard | **PASS** | LOW |
| Safe Cleanup of Corrupted Output | **PASS** | LOW |
| History Deduplication on Cancellation | **PASS** | LOW |

### 4.1 Findings & Evidence
- **Exact File:** [`app.py`](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/app.py)
- **Deep Pillow Output Verification (lines 1168–1179):**
  ```python
  if output_found and error_msg is None:
      valid, val_reason, (out_w, out_h), fmt = validate_image_file(expected_output)
      if not valid:
          error_msg = f"Generated output validation failed: {val_reason}"
          logger.error(f"Output verification failed for job {job_id} ({expected_output}): {val_reason}")
          try:
              expected_output.unlink(missing_ok=True)
          except Exception:
              pass
          expected_output = None
          output_found = False
  ```
  `validate_image_file()` opens the file with Pillow, verifies the container integrity, tests pixel limits, and catches truncated streams. Corrupt or 0-byte outputs are unlinked immediately and never marked `COMPLETED`.
- **Result Endpoint Protection (`api_result`, lines 2088–2089):**
  ```python
  if not job or job.get('status') != 'completed' or not job.get('output_path'):
      return jsonify({'error': 'Result not found or job not completed'}), 404
  ```
- **Cancellation Checkpoints (lines 1001, 1051, 1066, 1118):**
  Cancellation is checked before inference, during inference callbacks, upon worker return, and after retry backoff sleep. All exit paths record history cleanly via `save_history_entry()`.
- **History Deduplication (`save_history_entry`, lines 393–404):**
  Checks `item.get('job_id') == jid and item.get('status') == st` within the top 25 recent entries, preventing duplicate history items.

---

## AUDIT 5 — VIDEO PIPELINE LIFECYCLE (`video/`, `app.py`)

| Audit Item | Status | Risk Level |
| :--- | :---: | :---: |
| Duplicate Method Removal | **PASS** | LOW |
| `threading.RLock` Re-entrancy | **PASS** | LOW |
| Active Thread Tracking (`_active_threads`) | **PASS** | LOW |
| Subprocess Termination & Reaping | **PASS** | LOW |
| Workspace Deletion Guard (HTTP 409) | **PASS** | LOW |
| Pause / Resume Functionality | **PASS** | LOW |

### 5.1 Findings & Evidence
- **Exact Files:** [`video/video_job_manager.py`](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/video/video_job_manager.py), [`video/video_pipeline.py`](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/video/video_pipeline.py), [`video/ffmpeg_service.py`](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/video/ffmpeg_service.py)
- **Duplicate Removal:** Checked `video_job_manager.py`. `claim_job_for_execution` is defined at line 327 only. `release_job_claim` is defined at line 379 only.
- **Re-entrant Lock:** Line 101 of `video_pipeline.py` uses `self._lock = threading.RLock()`. Nested calls like `cancel_job()` -> `get_cancel_event()` no longer deadlock.
- **Active Thread Tracking:**
  - `run_job()` registers `self._active_threads[job_id] = threading.current_thread()` at line 168.
  - Pops thread in `finally:` block at lines 444–445.
  - `is_job_running()` and `wait_for_job_stopped(timeout)` provide thread-safe execution queries.
- **FFmpeg Subprocess Termination (`ffmpeg_service.py`, lines 216–236):**
  - Sends `proc.terminate()`, waits up to 3s, escalates to `proc.kill()`, and reaps with `proc.wait()`.
  - Closes reader stdout/stderr threads cleanly.
- **Workspace Protection (`app.py`, lines 2840–2848):**
  ```python
  if video_pipeline.is_job_running(job_id):
      video_pipeline.cancel_job(job_id)
      stopped = video_pipeline.wait_for_job_stopped(job_id, timeout=5.0)
      if not stopped:
          return jsonify({
              "error": "Video job is still actively running or terminating; cannot delete workspace until execution stops.",
              "status": "terminating"
          }), 409
  video_storage_mgr.cleanup_workspace(job_id)
  video_job_mgr.delete_video_job(job_id)
  ```

---

## AUDIT 6 — VIDEO UPLOAD REQUEST LIFECYCLE (`app.py`)

| Audit Item | Status | Risk Level |
| :--- | :---: | :---: |
| In-Flight Streaming & Size Enforcement | **PASS** | LOW |
| Collision-Free Unique Naming | **PASS** | LOW |
| Protection of Existing Files | **PASS** | LOW |
| Temporary `.upload_tmp` Cleanup | **PASS** | LOW |
| Global `MAX_CONTENT_LENGTH` vs `max_video_size_mb` Discrepancy | **NEEDS REVIEW** | MEDIUM |

### 6.1 Findings & Evidence
- **Exact File:** [`app.py`](file:///E:/AI%20Image Studio/RealESRGAN-v030/app.py) (lines 2660–2712)
- **Streaming & Size Enforcement:**
  - Writes to `temp_target = final_target.with_suffix(f"{final_target.suffix}.upload_tmp")`.
  - Reads in chunks (`chunk_size = 1024 * 1024`).
  - Checks `bytes_written > max_bytes`. If exceeded, deletes `temp_target` immediately and returns an error message.
  - On complete upload, performs `temp_target.replace(final_target)`.
- **Existing File Protection:**
  - If `clip.mp4` exists, it increments counter to `clip_1.mp4`, `clip_2.mp4`. Existing files are never overwritten.
- **Global `MAX_CONTENT_LENGTH` Ceiling (Outstanding Nuance):**
  - `config.json` specifies `"security": { "max_file_size_mb": 100 }` and `"video": { "max_video_size_mb": 2048 }`.
  - `app.py` line 244 sets `app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE` (100 MB).
  - Flask/Werkzeug applies `MAX_CONTENT_LENGTH` at the HTTP request header layer. If a client attempts to upload a video larger than 100 MB, Flask returns `HTTP 413 Request Entity Too Large` before `api_video_upload()` can read the stream.
  - *Recommendation:* Increase `MAX_CONTENT_LENGTH` to match `max_video_size_mb` (2048 MB) or remove the global cap and enforce size exclusively per-route.

---

## AUDIT 7 — TEST COVERAGE & INTEGRITY

| Audit Item | Status | Risk Level |
| :--- | :---: | :---: |
| 77 Regression Tests Verified | **PASS** | LOW |
| Previous 249 Test Suite Preservation | **PASS** | LOW |
| Total Project Test Count (273 Tests) | **PASS** | LOW |
| Zero Skipped Tests in Focused Suite | **PASS** | LOW |
| Test Isolation (Temp DB / Synthetic Files) | **PASS** | LOW |
| Zero Production Database Pollution | **PASS** | LOW |

### 7.1 Findings & Evidence
- **Source of the 77 Tests:**
  1. `tests.test_phase1_atomic_transitions_and_retry`: 7 tests
  2. `tests.test_recovery_and_queue_lifecycle`: 6 tests
  3. `tests.test_output_validation_cancellation_history`: 7 tests
  4. `tests.test_video_lifecycle_and_upload_safety`: 4 tests
  5. `tests.test_phase3_reliability`: 24 tests
  6. `tests.test_folder_browser_selection`: 15 tests
  7. `tests.test_jpg_upload_reprocessing`: 14 tests
  - **Subtotal:** Exactly **77 tests**. All 77 tests ran and passed (100% PASS, 0 failures, 0 errors, 0 skipped).
- **Status of the Previous 249 Tests:**
  The pre-existing test suites are preserved completely:
  - `test_phase1_standardization`: 12
  - `test_phase2_ai_optimization`: 19
  - `test_phase3_reliability`: 24
  - `test_phase4_ux`: 44
  - `test_phase5_video`: 76
  - `test_phase5_4_fixes`: 29
  - `test_folder_browser_selection`: 15
  - `test_jpg_upload_reprocessing`: 14
  - `test_preflight_remediation`: 16
  - **Subtotal:** Exactly **249 tests**.
- **Grand Total:** 249 + 24 (new remediation tests) = **273 tests** across the project repository.
- **Isolation:** All test fixtures utilize `tempfile.mkdtemp()`, isolated SQLite files (`test.db`), and mocked background workers. No tests touch `data/jobs.db` or invoke real GPU CUDA inference.

---

## AUDIT 8 — USER DATA INTEGRITY (READ-ONLY)

| Audit Item | Status | Risk Level |
| :--- | :---: | :---: |
| RealESRGAN_x4plus.pth Model Hash | **PASS** | LOW |
| SQLite `data/jobs.db` Integrity Check | **PASS** | LOW |
| User Jobs & Video Jobs Preservation | **PASS** | LOW |
| User Directories (Thumbnails, Logs, Backups) | **PASS** | LOW |

### 8.1 Findings & Evidence
- **Model Weight Hash:**
  - Path: `weights/RealESRGAN_x4plus.pth`
  - Expected: `4FA0D38905F75AC06EB49A7951B426670021BE3018265FD191D2125DF9D682F1`
  - Actual: `4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1` (67,040,989 bytes) — **EXACT MATCH**.
- **Database Integrity:**
  - Evaluated via read-only URI: `file:E:/AI Image Studio/RealESRGAN-v030/data/jobs.db?mode=ro`.
  - `PRAGMA integrity_check`: `[('ok',)]`.
  - Preserved record counts: `jobs`: 106 records, `video_jobs`: 2 records.
- **Directory Inventory:**
  - `thumbnails/`: 49 files, 220,674 bytes.
  - `logs/`: 83 structured log files, 1,014,379 bytes.
  - `backups/`: 5 baseline files, 190,143 bytes.
  - Zero data loss detected across any storage path.

---

## RECOMMENDED CORRECTIONS (SUBMITTED FOR APPROVAL)

The following two minor non-breaking adjustments are recommended for future operational optimization:

1. **Synchronize Flask `MAX_CONTENT_LENGTH` with Video Size Limit:**
   - *Current:* `app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE` (100 MB).
   - *Proposed:* Set `app.config['MAX_CONTENT_LENGTH'] = max(MAX_FILE_SIZE, int(APP_CONFIG.get('video', {}).get('max_video_size_mb', 2048) * 1024 * 1024))` so video uploads up to 2GB are not blocked at the HTTP boundary.
2. **Centralize Startup Checks Hook:**
   - *Current:* `run_startup_checks()` is under `if __name__ == '__main__':`.
   - *Proposed:* Wrap startup initialization in a Flask before-serving hook or application factory to guarantee recovery runs even if `app.py` is loaded via an external WSGI server.
