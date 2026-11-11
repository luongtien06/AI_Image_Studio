# ANTIGRAVITY — URGENT HOTFIX REPORT
## CHOOSE FOLDER END-TO-END ROOT-CAUSE REMEDIATION & VERIFICATION

**Project:** AI Image Studio — Real-ESRGAN v0.3.0  
**Root:** `E:\AI Image Studio\RealESRGAN-v030`  
**Branch:** `clean-main`  
**Audit Date:** 2026-11-11  
**Status:** `PASS — CHOOSE FOLDER END-TO-END VERIFIED`

---

## 1. EXECUTIVE SUMMARY

The "Choose Folder" workflow has undergone an end-to-end forensic investigation, surgical root-cause remediation, and multi-tier live desktop verification on Windows 10/11.

All failure modes previously reported by the user have been systematically reproduced, isolated, resolved, and verified through automated Win32 and HTTP integration tests:
1. **Dialog Visibility Failure (RESOLVED):** Previously, the native folder selection dialog spawned behind other desktop windows or remained invisible. It now maps as an active topmost foreground window with Win32 extended style `WS_EX_TOPMOST` (`0x10109`).
2. **False "Error" Status in Queue (RESOLVED):** Previously, selecting a folder containing images from prior runs marked all images as `Error` immediately upon discovery, leaving the queue in an unprocessable state with the Start button disabled. Now, historical `FAILED` records are preserved in SQLite while fresh `QUEUED` (`pending`) jobs are created in the active queue with `error = None`.
3. **Subfolder Discovery Failure (RESOLVED):** Directory scanning now recursively traverses subfolders using `os.walk` while filtering out hidden files and non-image formats.
4. **Localization Standardization (RESOLVED):** All remaining Vietnamese diagnostic and error strings in `app.py` and `folder_dialog.py` were translated to 100% English.
5. **Zero Data Regression (CONFIRMED):** All historical SQLite records in `data/jobs.db` remain 100% immutable.

---

## 2. ROOT-CAUSE FORENSIC ANALYSIS

| Failure Stage | Technical Root Cause | Impact |
|---|---|---|
| **Dialog Window Z-Order & Visibility** | In `folder_dialog.py`, `root.withdraw()` unmapped the Tkinter root window before calling `filedialog.askdirectory()`. In Win32 on Windows 10/11, a dialog parented to a withdrawn window cannot establish foreground hierarchy or topmost status, spawning behind the browser window (`is_foreground: False`). Furthermore, calling `user32.SetThreadDesktop()` on the main thread created thread-desktop separation from the background monitor thread. | User saw toast notification "Opening folder...", but no dialog window appeared on screen. |
| **Queue Images Marked "Error" on Selection** | In `app.py` line 1840 (`api_select_folder`), when checking existing jobs for discovered image paths: `'status': 'error' if db_job['status'] == STATUS_FAILED else ...` was assigned. If an image had a historical `FAILED` row in SQLite, it was loaded into memory as `status = 'error'`. In `templates/index.html`, `updateQueueInfo()` tallied `pending = 0`, permanently disabling `btnStart`. | Re-selecting the user's folder displayed all 13 images with red `Error` badges and disabled the Start Processing button. |
| **Missing Nested Subfolder Images** | In `app.py`, `api_select_folder` performed a flat `os.listdir()`, ignoring subdirectories. | Images stored inside nested folders were omitted from the queue. |
| **Localization Inconsistency** | `launch_native_folder_dialog` and `api_browse_folder` returned Vietnamese error strings (`"Hộp thoại chọn thư mục đang được mở"`, `"Người dùng đã đóng hoặc hủy hộp thoại"`). | Violated Package 1.4 English-only standardization. |

---

## 3. STRICT SCOPE & INTEGRITY CONTROL

### 3.1 Production Files Modified
Strictly confined to two production files:
1. `folder_dialog.py`
2. `app.py`

### 3.2 Backup Manifest
Timestamped backups were archived prior to any modifications:
- **Backup Directory:** `backups/choose_folder_hotfix_20261111_112001/`

| File | Pre-Fix SHA-256 (Backup) | Post-Fix SHA-256 (Active) |
|---|---|---|
| `folder_dialog.py` | `a245531dbb84fd073ffcc58b095207bf17876d59748c2dfe133bbf0debed3eeb` | `ce05ba6ea50e8435a1c01638572da036c3a97b8ea16ba9ba4c2a75d25b03d4d2` |
| `app.py` | `104311f658ae25514e938eed376f25620ac4d1e5d5b85edc5243c76f1e7a9ba2` | `e256668c0dcdf4b4515cd76b5e4518263d3a5a978bf6d3ac10be19b2e9cdec2b` |

### 3.3 Historical Data Preservation
- **`data/jobs.db` Baseline Invariant:** All 871 historical failed records, including the 13 user production photoshoot failures (`E:\WEB\3Dimage\!!!\originals\i31`), remain 100% immutable and intact.
- **`logs/history.json`:** Untouched.
- **Original User Images:** Never modified, moved, or deleted.

---

## 4. IMPLEMENTATION DETAILS

### 4.1 Native Windows Dialog Topmost & Foreground Pinning (`folder_dialog.py`)
1. **Mapped 1x1 Offscreen Window:**
   - Replaced `root.withdraw()` with:
     ```python
     root.geometry("1x1+-2000+-2000")
     root.attributes("-topmost", True)
     root.attributes("-alpha", 0.0)
     root.update()
     ```
   - This keeps the window mapped into the desktop window hierarchy while completely invisible to the user.
2. **Win32 Monitor Thread (`enforce_dialog_foreground`):**
   - Removed `SetThreadDesktop` to ensure both Tkinter and the monitor thread operate in the same interactive window station (`WinSta0\Default`).
   - Background thread continuously detects `#32770` dialog and enforces:
     - `user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)`
     - `user32.AllowSetForegroundWindow(-1)`
     - `user32.SetForegroundWindow(hwnd)`
     - `user32.BringWindowToTop(hwnd)`
     - `user32.SetActiveWindow(hwnd)`
     - `user32.SetFocus(hwnd)`

### 4.2 Selective State Reuse & Recursive Discovery (`app.py`)
1. **Recursive `os.walk` Discovery:**
   - `api_select_folder` now traverses the entire directory tree recursively.
   - Skips hidden files/directories (starting with `.`) and system directories (`$`).
   - Uses Pillow `validate_image_file` to strictly filter valid image containers and reject corrupt/unsupported files.
2. **Selective State Mapping:**
   - **Active Jobs:** Reuses in-memory jobs with status `pending`, `processing`, `validating`, `retry` to prevent duplicate submissions on repeat folder selection.
   - **Completed Jobs:** Reuses historical `completed` jobs to avoid re-running finished work.
   - **Historical Failed Jobs:** If an image was previously `FAILED` in SQLite, it is **NOT** mapped as `error`. Instead, a fresh job ID is assigned with status `pending` (`QUEUED`), `error = None`, `retry_count = 0`, allowing the user to click "Start Processing" immediately.
   - The historical `FAILED` row in SQLite remains untouched.
3. **English Standardization:**
   - Translated all error/status strings in `launch_native_folder_dialog` and `api_browse_folder` to English.

---

## 5. VERIFICATION EVIDENCE & TEST RESULTS

### 5.1 End-to-End Test Suite (`tests/test_choose_folder_hotfix_e2e.py`)
A dedicated 6-point end-to-end integration test suite was developed and executed:

```
Ran 6 tests in 2.478s
OK
```

| Test Case | Method | Outcome | Detail |
|---|---|---|---|
| **Point 1: Topmost & Cancellation** | `test_01_native_dialog_topmost_style_and_lifecycle` | **PASS** | Win32 `#32770` dialog verified with extended style `0x10109` (`WS_EX_TOPMOST`), `is_topmost: True`. Subprocess clean cancellation verified (empty output file). |
| **Point 2: Direct Path Validation** | `test_02_browse_folder_api_path_validation` | **PASS** | Validates folder paths without launching interactive dialogs. Correctly rejects non-existent / restricted paths. |
| **Point 3: Recursive Discovery & Status** | `test_03_folder_selection_recursive_and_filtering` | **PASS** | Discovered exactly 4 valid images (3 root + 1 nested subfolder). Safely skipped `readme.txt` and `.hidden.png`. All 4 jobs assigned status `pending` (`QUEUED`) with `error = None`. |
| **Point 4: Duplicate Prevention** | `test_04_repeat_folder_selection_no_duplicates` | **PASS** | Calling `/api/select-folder` repeatedly preserves the existing 4 active jobs without creating duplicate queue entries. |
| **Point 5: Historical Failed Recovery** | `test_05_historical_failed_jobs_not_shown_as_error` | **PASS** | Simulated image with pre-existing `FAILED` record in SQLite enters active queue with status `pending` and `error = None`. Historical record untouched. |
| **Point 6: Database Invariant** | `test_06_database_immutability` | **PASS** | Verified all historical user photoshoot records in `data/jobs.db` are 100% identical in ID, filename, status, and error message. |

### 5.2 Full Regression Test Execution

| Test Suite | Tests Run | Result | Duration | Notes |
|---|---|---|---|---|
| `test_package_1_6_verification.py` | 9 / 9 | **PASS** | 152.2s | Validated tile placement, CPU output canvas, bit-for-bit equivalence, and 124.4 MP image inference (Peak RSS: 3,494.0 MB, Peak CUDA: 823.1 MB). |
| `test_output_validation_cancellation_history.py` | 7 / 7 | **PASS** | 11.0s | Output verification, zero-byte file rejection, cooperative cancellation, and history persistence verified. |
| `test_phase3_reliability.py` | 24 / 24 | **PASS** | 3.6s | SQLite WAL concurrency, transactional retries, crash recovery, and security path traversal prevention verified. |
| `test_choose_folder_hotfix_e2e.py` | 6 / 6 | **PASS** | 2.5s | Live Win32 dialog topmost, recursive discovery, and queue status mapping verified. |

---

## 6. FINAL ACCEPTANCE VERDICT

```
================================================================================
                    FINAL ACCEPTANCE GATE VERDICT: PASS
================================================================================
  [x] Native Windows folder dialog opens visibly in foreground (topmost: True).
  [x] User cancellation handled cleanly (status: cancelled, no crash).
  [x] Backend validates folder and recursively discovers images in subfolders.
  [x] Unsupported and hidden files are safely filtered out.
  [x] Discovered images enter the UI queue with status QUEUED (pending).
  [x] Historical FAILED jobs in SQLite are NOT rendered as Error on new selection.
  [x] Start Processing button is enabled with pending > 0.
  [x] Repeat folder selections prevent duplicate job entries.
  [x] Zero mutations to historical SQLite records or user images.
  [x] 100% English UI standardization across all touched endpoints.
================================================================================
```
