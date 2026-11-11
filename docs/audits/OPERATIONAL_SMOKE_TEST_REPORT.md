# ANTIGRAVITY — OPERATIONAL SMOKE TEST REPORT
## STRICTLY CONTROLLED — POST PACKAGE 1 & 2 ACCEPTANCE

**Project:** AI Image Studio — Real-ESRGAN v0.3.0  
**Project Root:** `E:\AI Image Studio\RealESRGAN-v030`  
**Git Branch:** `clean-main` (HEAD: `37361c773cf07c909ce3650350d5700dfcc1f399`)  
**Audit Timestamp:** 2026-11-10T20:28:00+07:00  
**Audit Execution Mode:** Controlled operational validation (Zero manual source/config modifications, strictly non-destructive)  
**Deliverable Path:** `docs/audits/OPERATIONAL_SMOKE_TEST_REPORT.md`  

---

## A. ENVIRONMENT & PRE-FLIGHT VERIFICATION

| Check | Specification / Requirement | Actual Result | Status |
| :--- | :--- | :--- | :---: |
| **Project Root** | `E:\AI Image Studio\RealESRGAN-v030` | `E:\AI Image Studio\RealESRGAN-v030` | **PASS** |
| **Git Branch** | `clean-main` | `clean-main` (HEAD: `37361c773cf0...`) | **PASS** |
| **Python Environment** | Conda `realesrgan` (Python 3.10) | `C:\Users\Asus\anaconda3\envs\realesrgan\python.exe` | **PASS** |
| **Model Weights** | `weights/RealESRGAN_x4plus.pth` | 67,040,989 bytes, exists | **PASS** |
| **Model SHA-256** | `4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1` | `4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1` (exact match) | **PASS** |
| **CUDA & GPU** | NVIDIA GeForce RTX 2050 Laptop GPU | CUDA 12.7 available, Driver 566.07, GPU recognized | **PASS** |
| **Initial GPU VRAM** | 4096 MB VRAM available | 0.0 MB allocated / 0.0 MB reserved (Idle) | **PASS** |
| **Initial System RAM** | >= 4 GB available physical RAM | Total 15.32 GB, Available 4.66 GB (69% used) | **PASS** |
| **Disk Space (Project)** | Drive `E:\` sufficient free space | 28.75 GB free / 60.27 GB total | **PASS** |
| **Disk Space (Temp)** | Drive `C:\` sufficient temp space | 58.50 GB free / 284.22 GB total | **PASS** |
| **Config Pixel Limits** | `UPLOAD_MAX_PIXELS = 150000000` | Synchronized across `app.py`, worker, scanner | **PASS** |

---

## B. APPLICATION STARTUP & RECOVERY

The application server was launched via the supported environment launcher (`realesrgan` python `app.py`).

- **Server Status:** HTTP service listening and responding at `http://127.0.0.1:5678` (HTTP 200).
- **Persistent AI Worker:** Initialized successfully (`status="READY"`).
- **Model In-Memory Load:** `RealESRGAN_x4plus` loaded with Half-Precision (`Half=True, Device=cuda`).
- **Crash Recovery Execution:**
  - Startup recovery executed cleanly via `recover_on_startup()`.
  - Exactly 1 in-flight `CANCEL_REQUESTED` job (`f346648a-503f-4fa0-9ac1-c04ebe354b20`) was finalized to `CANCELLED`.
  - **Zero missing-source historical jobs were enqueued** (`eligible_job_ids: []`).
  - Total newly enqueued jobs on startup: **0**.
- **Historical Job Protection:** All 82 protected historical orphan jobs remained completely unchanged during startup.

---

## C. CONTROLLED INFERENCE RESULTS

In accordance with strict safety limits, exactly 2 sequential inference jobs were executed, and 1 high-resolution test was evaluated and blocked for resource safety.

| Test ID | Test Category | Input Dimensions | Result | Output Dimensions | Peak VRAM | Peak RAM | Duration | Tile Size | Output Size |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Test A** | Normal Image (Synthetic PNG) | 512 × 512 | **PASS** | 2048 × 2048 | 1,240 MB | 11.2 GB | 7.4 s | 256 | 2,611,143 B |
| **Test B** | High-Res Image (27.16 MP JPG) | 4244 × 6400 | **FAIL (Output Limit)** | *(Expected: 16976 × 25600)* | 3,932 MB | 11.6 GB | 563.4 s | 128 | 0 B |
| **Test C** | Very High-Res (111.88 MP) | 11520 × 9712 | **BLOCKED** | *(Expected: 46080 × 38848)* | N/A | N/A | N/A | N/A | N/A |

### Detailed Test Analysis

#### Test A — Normal Image (512 × 512)
- **Job ID:** `539dac3c-1f66-4196-a013-e91e522292fe`
- **Execution Lifecycle:** `QUEUED -> PROCESSING -> COMPLETED`.
- **Inference Verification:** AI Worker processed the image in 7.4 seconds with auto-tiling selecting `tile=256`.
- **Output Validation:** Output saved to `smoke_test_outputs/smoke_test_a_512x512_4x.png` (2,611,143 bytes). Pillow opened and verified dimensions `2048 × 2048` (exactly 4x).
- **History Record:** Entry created in `logs/history.json` with status `completed`.

#### Test B — High-Resolution Image (4244 × 6400, 27.16 MP)
- **Job ID:** `67145966-28ea-4d52-b543-7c4b559f2110`
- **Package 1 Validation:** Input image of 27.16 Megapixels was successfully accepted by `/api/upload` under the 150M pixel limit (summary: `valid: 1, too_large: 0`). The worker did **not** fall back to 25M.
- **GPU Inference:** AI Worker automatically assigned `tile=128`. The 1,700 tiles were processed on the RTX 2050 GPU without encountering CUDA Out-Of-Memory (Peak VRAM: 3,932 MB out of 4,096 MB).
- **Output Validation Behavior:** Upon completing tile inference and stitching, the 4x upscaled image dimensions were `16976 × 25600 = 434,585,600 pixels` (~434.6 Megapixels). At `app.py:1210`, output verification calls `validate_image_file(expected_output)`, which checks against `MAX_PIXELS` (150,000,000 pixels). Because 434.6 MP exceeds 150 MP, the output validator rejected the generated file:
  `"Generated output validation failed: Image exceeds maximum pixel limit: 16976x25600 (434,585,600 pixels, max allowed: 150,000,000)"`.
- **Safety Handling:** The job transitioned cleanly to `STATUS_FAILED` with `retry_count=0` (non-retryable error). No infinite retry loop occurred.

#### Test C — Very High-Resolution Image (11520 × 9712, 111.88 MP)
- **Evaluation Status:** **BLOCKED — RESOURCE SAFETY**
- **Memory Requirement Calculation:**
  - Input: 11,520 × 9,712 = 111.88 Megapixels (~335.6 MB uncompressed).
  - Expected Output: 46,080 × 38,848 = 1,789,900,800 pixels (~1.79 Gigapixels).
  - Raw uncompressed RGB output buffer: 1,789,900,800 × 3 bytes = **5.37 GB contiguous RAM**.
  - Dual-buffer stitching and image export: **~10.74 GB RAM**.
  - Current Available System RAM: **4.66 GB**.
- **Determination:** Physical RAM capacity (4.66 GB) is strictly less than the minimum raw buffer required (5.37 GB). Attempting this test would cause severe memory exhaustion, OS thrashing, or worker crash. Under Gate 3 guidelines, Test C was safely blocked.

---

## D. DATABASE & HISTORY RECONCILIATION

Database comparisons were conducted using read-only SQLite connections against `docs/audits/SMOKE_TEST_BASELINE.json`.

### Job Count Reconciliation

| Metric | Baseline | Final Post-Smoke Test | Delta / Explanation |
| :--- | :---: | :---: | :--- |
| `COMPLETED` | 1 | 2 | +1 (Test A job `539dac3c-...`) |
| `FAILED` | 221 | 222 | +1 (Test B job `67145966-...`) |
| `CANCELLED` | 15 | 16 | +1 (1 `CANCEL_REQUESTED` job finalized during boot) |
| `CANCEL_REQUESTED` | 1 | 0 | -1 (Finalized to `CANCELLED` during boot recovery) |
| `QUEUED` | 0 | 0 | 0 (No jobs left in queue) |
| `PROCESSING` | 0 | 0 | 0 (No dangling jobs) |
| **Total Jobs** | **238** | **240** | **+2 (Exactly the 2 test jobs created)** |

### Integrity Verification
- **SQLite `PRAGMA integrity_check`:** Returned `['ok']`.
- **Protected 82 Historical Orphan Jobs:** All 82 records were verified against baseline fields (`job_id`, `status`, `retry_count`, `original_filename`). **Zero modifications or status changes occurred (0 violations)**.
- **History Records (`logs/history.json`):** Total records: 275 (exactly 2 entries added for Test A and Test B; zero existing historical entries deleted or altered).

---

## E. FILE & BACKUP INTEGRITY

| Asset | Baseline / Backup SHA-256 | Post-Test SHA-256 | Verification Status |
| :--- | :--- | :--- | :---: |
| `weights/RealESRGAN_x4plus.pth` | `4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1` | `4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1` | **MATCH (Intact)** |
| `config.json` | `2ca80672e08077ef27e9ef5e9a6081b3259f518304608fd002ebca32c8241773` | `2ca80672e08077ef27e9ef5e9a6081b3259f518304608fd002ebca32c8241773` | **MATCH (Intact)** |
| `app.py` | `0fd2fea0a041aa0e8098274e030618c6ae31f4e059c5f75fd3269c3e27971d60` | `0fd2fea0a041aa0e8098274e030618c6ae31f4e059c5f75fd3269c3e27971d60` | **MATCH (Intact)** |
| `job_manager.py` | `2a87ac8d9b02d303c36964bd1abf9d820b7fcfcbdc37cdfa43457d4ae835e92f` | `2a87ac8d9b02d303c36964bd1abf9d820b7fcfcbdc37cdfa43457d4ae835e92f` | **MATCH (Intact)** |
| `ai_worker.py` | `8d05ea2db2673fd32423c3092bf121d5308d3533f8fe1763490d6131bbaa7206` | `8d05ea2db2673fd32423c3092bf121d5308d3533f8fe1763490d6131bbaa7206` | **MATCH (Intact)** |
| `video_job_manager.py` | `012fc9a89f5cb23ac69f9ca50ae14b9ae2bfe7e72dbe0bcf8ad20ad832810a95` | `012fc9a89f5cb23ac69f9ca50ae14b9ae2bfe7e72dbe0bcf8ad20ad832810a95` | **MATCH (Intact)** |
| `video_pipeline.py` | `6a87b7378d4ca894cf74f76263ae78cbda50a49704aa3d2ea5232dfc5d0811b7` | `6a87b7378d4ca894cf74f76263ae78cbda50a49704aa3d2ea5232dfc5d0811b7` | **MATCH (Intact)** |
| **Pre-Test Backup** | `backups/operational_smoke_test_20261110_200819` | Manifest verified with all 8 files | **VERIFIED** |

---

## F. IDENTIFIED TECHNICAL OBSERVATIONS

1. **Output Pixel-Limit Asymmetry (Observation):**
   - Package 1 raised `UPLOAD_MAX_PIXELS` to 150,000,000 pixels so that high-resolution input images (up to 150 MP) can be uploaded.
   - However, upscaling a 27.16 MP image by 4x produces an output of **434.6 Megapixels**.
   - During post-inference output verification (`app.py:1210`), `validate_image_file(expected_output)` checks the output file against `MAX_PIXELS` (which is 150,000,000).
   - Consequently, any input image greater than `150M / 16 = 9.375 Megapixels` will produce a 4x output that exceeds 150 Megapixels and fail output verification unless an independent `OUTPUT_MAX_PIXELS` parameter or exemption is defined.
   - Importantly, the application handled this failure safely: it logged the exact validation error, transitioned the job to `STATUS_FAILED`, avoided infinite retries, and preserved system stability.

2. **Test C Resource Safety:**
   - 111.88 MP input images scaling to 1.79 GP output requires over 10.7 GB contiguous RAM for output stitching, which is unsafe on systems with 16 GB total RAM and ~4.66 GB available physical memory. Blocking this test protected system operational integrity.

---

## G. TEMPORARY ARTIFACTS INVENTORY

All test artifacts remain isolated and have not modified user data:
1. `smoke_test_inputs/smoke_test_a_512x512.png` (6,250 bytes)
2. `smoke_test_inputs/smoke_test_b_4244x6400.jpg` (768,642 bytes)
3. `smoke_test_outputs/smoke_test_a_512x512_4x.png` (2,611,143 bytes)
4. `uploads/smoke_test_a_512x512.png` (6,250 bytes)
5. `uploads/smoke_test_b_4244x6400.jpg` (768,642 bytes)
6. `thumbnails/539dac3c-1f66-4196-a013-e91e522292fe.jpg` (2,048 bytes)
7. `thumbnails/67145966-28ea-4d52-b543-7c4b559f2110.jpg` (4,096 bytes)
8. `backups/operational_smoke_test_20261110_200819/` (Pre-test safety backup)
9. `docs/audits/SMOKE_TEST_BASELINE.json` (Pre-test baseline manifest)

---

## H. FINAL VERDICT

# **CONDITIONALLY ACCEPTED**

### Verdict Rationale:
1. **Core Startup & Worker:** Flask server boot, CUDA GPU recognition, Half-precision model loading, and startup recovery passed completely without error.
2. **Orphan-Safe Recovery (Package 2):** Startup recovery validated source files and prevented missing-source jobs from entering the active processing queue.
3. **Normal Image Inference (Test A):** Standard image upscale passed with 100% success (512x512 -> 2048x2048 in 7.4s), verified by Pillow, with valid history persistence.
4. **High-Resolution Processing (Test B):** 27.16 MP image was accepted under Package 1's 150M upload validation without 25M fallback. 1,700 tiles were processed on the GPU without OOM (Peak VRAM: 3,932 MB). Output verification failed as the 434.6 MP output exceeded the 150 MP output limit, but was handled safely without crashes or retry loops.
5. **Very High-Resolution Safety (Test C):** Correctly blocked under Gate 3 resource safety rules to prevent memory exhaustion on 4.66 GB available RAM.
6. **Data Integrity:** All 82 protected historical orphan jobs, SQLite database integrity, model weights, config, and source files were preserved 100% intact.
