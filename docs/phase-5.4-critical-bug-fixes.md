# PHASE 5.4 — CRITICAL & HIGH PRIORITY BUG FIXES REPORT
## CONCURRENCY CONTROL · AI WORKER STATE RECOVERY · VIDEO CANCELLATION SEMANTICS

**Dự án:** AI Image Studio  
**Thư mục dự án:** `E:\AI Image Studio\RealESRGAN-v030`  
**Giai đoạn trước:** Phase 5.3 — Comprehensive Code Audit  
**Tài liệu tham chiếu:**
- `docs/phase-5.3-comprehensive-code-audit.md`
- `docs/phase-5.3-code-audit-findings.csv`

**Môi trường thực thi:**
- OS: Windows 11 Home Single Language (Build 26100)
- Python: 3.10.21 (`realesrgan` Conda Environment)
- PyTorch: 2.6.0+cu126
- CUDA: 12.6
- GPU: NVIDIA GeForce RTX 2050 Laptop GPU (4GB VRAM)
- Video Framework: FFmpeg / FFprobe (Zeranoe/Gyan builds)
- Database: SQLite 3 with WAL Mode (`PRAGMA journal_mode=WAL`)
- AI Model: `weights/RealESRGAN_x4plus.pth`  
  **SHA-256:** `4FA0D38905F75AC06EB49A7951B426670021BE3018265FD191D2125DF9D682F1` (Verified Unchanged)

---

## 1. TỔNG QUAN VÀ MỤC TIÊU (EXECUTIVE SUMMARY)

Giai đoạn **Phase 5.4** được kích hoạt để xử lý dứt điểm 3 lỗi kỹ thuật cốt lõi (1 HIGH, 2 MEDIUM) được phát hiện trong cuộc kiểm toán độc lập toàn diện Phase 5.3:

1. **AUDIT53-02 (HIGH PRIORITY) — Video Job Concurrency & Race Conditions:**  
   Thiếu cơ chế claim nguyên tử (atomic claim) ở tầng cơ sở dữ liệu khi khởi chạy hoặc tiếp tục job video, dẫn đến nguy cơ xung đột thread, tạo worker song song xử lý cùng một job, làm tràn bộ nhớ VRAM 4GB và ghi đè dữ liệu workspace.
2. **AUDIT53-01 (MEDIUM PRIORITY) — AI Worker State Recovery & Token Lifecycle:**  
   Trạng thái nội tại của `AIWorker` có nguy cơ bị kẹt tại `STATE_BUSY` khi tác vụ video kết thúc hoặc bị hủy, làm tê liệt các tính năng ảnh đơn, Quick Preview và kiểm tra trạng thái sức khỏe worker.
3. **AUDIT53-03 (MEDIUM PRIORITY) — Video Cancellation Semantics & Subprocess Cleanup:**  
   Khi người dùng hủy tác vụ video, hệ thống ghi nhận sai trạng thái thành `FAILED` thay vì `CANCELLED`, có nguy cơ để lại tiến trình FFmpeg mồ côi (orphan process), và có nguy cơ xóa nhầm các frame đã upscale thành công.

### Kết quả nghiệm thu Phase 5.4:
- Toàn bộ **3 lỗi kỹ thuật** đã được khắc phục triệt để bằng các giải pháp kỹ thuật phòng thủ (defensive programming), locking phân cấp và atomic SQL operations.
- Hệ thống bảo tồn **100% tính tương thích ngược**: toàn bộ 191 bài kiểm thử hồi quy từ Phase 1 đến Phase 5 đều vượt qua mà không cần sửa đổi bất kỳ logic kiểm thử nào.
- Bổ sung **29 bài kiểm thử chuyên sâu mới** (`tests/test_phase5_4_fixes.py`) bao phủ concurrency, token lifecycles, exception handling và cancellation idempotency.
- Tổng số kiểm thử chạy tự động: **220 / 220 PASS (100% SUCCESS, 0 FAILS, 0 ERRORS, 0 SKIPPED)**.
- Model trọng số `RealESRGAN_x4plus.pth` được bảo toàn nguyên vẹn với mã băm SHA-256 đối chiếu trùng khớp hoàn toàn.

---

## 2. AN TOÀN HỆ THỐNG VÀ BẢO TOÀN TRỌNG SỐ (PRE-FLIGHT AUDIT & BACKUP)

Trước khi thực hiện bất kỳ chỉnh sửa nào vào mã nguồn, hệ thống đã thực hiện đầy đủ các bước tiền kiểm và tạo bản lưu dự phòng:

### 2.1. Xác thực mã băm Model Weights
Mã băm SHA-256 của file trọng số model duy nhất được xác thực độc lập trước và sau khi triển khai:
```powershell
Path: E:\AI Image Studio\RealESRGAN-v030\weights\RealESRGAN_x4plus.pth
SHA-256 Target: 4FA0D38905F75AC06EB49A7951B426670021BE3018265FD191D2125DF9D682F1
SHA-256 Actual: 4FA0D38905F75AC06EB49A7951B426670021BE3018265FD191D2125DF9D682F1
Status: VERIFIED EXACT MATCH (Zero byte modification)
```

### 2.2. Danh mục bản sao lưu tiền triển khai (Pre-execution Backups)
Năm file nguồn cốt lõi đã được sao lưu với hậu tố `.bak_phase5_4`:
1. `video/video_job_manager.py.bak_phase5_4`
2. `video/video_pipeline.py.bak_phase5_4`
3. `video/frame_processor.py.bak_phase5_4`
4. `ai_worker.py.bak_phase5_4`
5. `app.py.bak_phase5_4`

---

## 3. PHÂN TÍCH NGUYÊN NHÂN VÀ GIẢI PHÁP CHI TIẾT

```
+---------------------------------------------------------------------------------------------------+
|                               PHASE 5.4 REMEDIATION ARCHITECTURE                                  |
+---------------------------------------------------------------------------------------------------+
|                                                                                                   |
|  [ HTTP API Requests ]                                                                            |
|        |                                                                                          |
|        +--> /api/video/jobs/<id>/start                                                            |
|        +--> /api/video/jobs/<id>/resume                                                           |
|                 |                                                                                 |
|                 v (Atomic SQL Claim)                                                              |
|        [ VideoJobManager.claim_job_for_execution ]                                                |
|        `-- UPDATE video_jobs SET status='PROCESSING' WHERE id=? AND status IN (...)               |
|                 |                                                                                 |
|                 +--- (Claim Failed) ---> Returns 409 Conflict / 400 Bad Request                   |
|                 |                                                                                 |
|                 v (Claim Acquired)                                                                |
|        [ VideoPipeline.run_job Thread Registry ]                                                  |
|        `-- Double check _active_threads[job_id]; reject if duplicate                              |
|                 |                                                                                 |
|                 v                                                                                 |
|        [ AIWorker Centralized Token Management ]                                                  |
|        `-- acquire_busy(task_type, task_id) -> returns unique token                               |
|                 |                                                                                 |
|                 v                                                                                 |
|        [ FrameProcessor.process_frames Loop ]                                                     |
|        `-- Wrapped in try ... finally: release_busy(token)                                        |
|                 |                                                                                 |
|                 +--- (Cancel Signal Detected)                                                     |
|                 |         |                                                                       |
|                 |         v                                                                       |
|                 |   [ FFmpegService Subprocess Termination ]                                      |
|                 |   `-- proc.terminate() -> proc.wait() -> raises FFmpegCancelledException        |
|                 |         |                                                                       |
|                 |         v                                                                       |
|                 |   [ VideoPipeline Exception Handler ]                                           |
|                 |   `-- Preserves enhanced/ frames, deletes partial temp_final.mp4,               |
|                 |       transitions status to 'CANCELLED' (NOT 'FAILED')                          |
|                 |                                                                                 |
|                 v (Normal Completion or Error)                                                    |
|        `-- release_busy(token) -> Worker State cleanly returns to STATE_READY                     |
+---------------------------------------------------------------------------------------------------+
```

---

### 3.1. KHẮC PHỤC AUDIT53-02 (HIGH): VIDEO JOB CONCURRENCY & ATOMIC CLAIM

#### A. Nguyên nhân gốc rễ (Root Cause)
Trước Phase 5.4, quá trình khởi chạy tác vụ video thông qua API `/api/video/jobs/<id>/start` hoặc `/resume` đọc trạng thái job bằng một câu lệnh `SELECT`, sau đó kiểm tra điều kiện bằng Python rồi mới gửi lệnh cập nhật trạng thái `PROCESSING`. Khi có hai yêu cầu HTTP đồng thời (hoặc người dùng bấm double-click chuột, hoặc hai client cùng truy cập), cả hai luồng đều đọc thấy trạng thái hợp lệ (`QUEUED` hoặc `PAUSED`) trước khi câu lệnh ghi được thực hiện. Hệ quả:
- Cả hai luồng đều coi mình có quyền thực thi và tạo hai background worker thread cùng lúc (`threading.Thread(target=pipeline.run_job)`).
- Hai worker cùng đọc ghi vào cùng một thư mục workspace (`workspace/<job_id>`), cùng nạp frame vào GPU và chạy FFmpeg đồng thời, dẫn đến tràn bộ nhớ VRAM (OOM trên RTX 2050 4GB) và làm hỏng file video đầu ra.

#### B. Giải pháp kỹ thuật đã triển khai
1. **Atomic Conditional SQL Claim:**
   Trong `video/video_job_manager.py`, triển khai hàm `claim_job_for_execution(job_id, allowed_statuses)` thực hiện câu lệnh SQL nguyên tử duy nhất:
   ```sql
   UPDATE video_jobs 
   SET status = 'PROCESSING',
       started_at = COALESCE(started_at, ?),
       updated_at = ?
   WHERE id = ? AND status IN (<allowed_statuses_placeholders>)
   ```
   Nhờ SQLite WAL mode và cơ chế kiểm tra `cursor.rowcount == 1`, chỉ duy nhất 1 thread thực hiện thành công câu lệnh `UPDATE` này và nhận được quyền claim. Bất kỳ thread nào đến sau (hoặc thread chạy đồng thời) đều nhận `rowcount == 0` và bị từ chối ngay lập tức ở tầng dữ liệu.

2. **Claim Release & Exception Rollback:**
   Bổ sung phương thức `release_job_claim(job_id, target_status, error_message)` để nếu quá trình tạo background thread gặp lỗi hoặc xảy ra sự cố trước khi pipeline khởi chạy, trạng thái job sẽ được rollback an toàn về trạng thái mục tiêu (`QUEUED`, `PAUSED` hoặc `FAILED`) mà không để job bị kẹt ở trạng thái `PROCESSING`.

3. **In-Memory Thread Tracking trong VideoPipeline:**
   Trong `video/video_pipeline.py`, bổ sung dictionary `self._active_threads: Dict[str, threading.Thread]` được bảo vệ bằng mutex `self._lock`. Phương thức `run_job()` kiểm tra xem job_id đã có thread đang active (`thread.is_alive()`) hay chưa. Nếu đã có, `run_job` sẽ lập tức từ chối thực thi và log cảnh báo, ngăn chặn triệt để trường hợp duplicate thread.

4. **HTTP Status Code Mapping Chuẩn mực:**
   Trong `app.py`:
   - Nếu job đã ở trạng thái `PROCESSING`, API trả về mã lỗi **409 Conflict** kèm thông tin trạng thái hiện tại.
   - Nếu job ở trạng thái kết thúc (`COMPLETED`, `FAILED`, `CANCELLED`), API trả về mã lỗi **400 Bad Request**.
   - Nếu claim thành công, background thread được khởi chạy và API trả về **200 OK**.

---

### 3.2. KHẮC PHỤC AUDIT53-01 (MEDIUM): AI WORKER STATE RECOVERY & TOKEN LIFECYCLE

#### A. Nguyên nhân gốc rễ (Root Cause)
Trước Phase 5.4, `AIWorker` quản lý trạng thái (`self.state`) bằng việc gán trực tiếp biến chuỗi (`self.state = STATE_BUSY` và `self.state = STATE_READY`). Khi tác vụ video gọi `process_frames`, nó gán `worker.state = STATE_BUSY`. Tuy nhiên:
- Nếu xảy ra lỗi ngoại lệ, tín hiệu Pause hoặc Cancel trong lúc xử lý frame, `worker.state` không được khôi phục chắc chắn về `STATE_READY`.
- Quick Preview (`generate_quick_preview`) và các tác vụ upscale ảnh đơn kiểm tra `if self.state == STATE_BUSY:` và từ chối thực hiện. Khi worker bị kẹt `BUSY`, toàn bộ ứng dụng bị "đóng băng" khả năng sinh ảnh xem trước và upscale ảnh thông thường cho đến khi khởi động lại server.
- Sử dụng biến đếm số nguyên đơn thuần dễ gặp lỗi đếm âm (negative counter) hoặc race condition khi nhiều luồng cùng gọi giải phóng.

#### B. Giải pháp kỹ thuật đã triển khai
1. **Token-Based Ownership & Lifecycle Management:**
   Trong `ai_worker.py`, trang bị cơ chế cấp phát token sở hữu độc quyền:
   - `self._state_lock = threading.RLock()`: Mutex bảo vệ toàn bộ cấu trúc trạng thái worker.
   - `self._active_tokens: Dict[str, Dict[str, Any]]`: Bản đồ lưu trữ các token đang hoạt động, kèm metadata (`task_type`, `task_id`, `created_at`).
   - `acquire_busy(task_type, task_id) -> str`: Tạo UUID token mới, ghi nhận vào `_active_tokens`, chuyển `self.state = STATE_BUSY`.
   - `release_busy(token) -> bool`: Xóa token khỏi `_active_tokens`. Khi và chỉ khi không còn bất kỳ active token nào (`len(_active_tokens) == 0`), worker state mới chuyển về `STATE_READY`. Nếu gọi release trùng lặp (idempotent), hệ thống bỏ qua an toàn mà không làm âm biến đếm.

2. **Context Manager an toàn tuyệt đối (`busy_scope`):**
   Cung cấp context manager `@contextmanager def busy_scope(task_type, task_id)` sử dụng khối `try ... finally` để đảm bảo 100% token được giải phóng ngay cả khi xảy ra unhandled exception:
   ```python
   token = self.acquire_busy(task_type=task_type, task_id=task_id)
   try:
       yield token
   finally:
       self.release_busy(token)
   ```

3. **Tích hợp FrameProcessor với Lifecycle Token:**
   Trong `video/frame_processor.py`, toàn bộ vòng lặp xử lý frame được bao bọc trong cấu trúc bảo vệ token của worker:
   ```python
   task_token = self.worker.acquire_busy(task_type="video", task_id=job_id or "unknown_video")
   try:
       # Vòng lặp upscale từng frame...
   finally:
       self.worker.release_busy(task_token)
   ```
   Dù vòng lặp thoát do hoàn thành, do pause event, do cancel event hay do CUDA out-of-memory exception, worker luôn được hoàn trả về `STATE_READY`.

4. **Bảo vệ Quick Preview & Single Image Processing:**
   Cập nhật `generate_quick_preview` và `process_image` trong `ai_worker.py`:
   - Nếu worker đang giữ active tokens từ tác vụ video, Quick Preview trả về thông báo lỗi rõ ràng `"AI Worker đang bận xử lý tác vụ khác..."` mà không làm crash hệ thống.
   - Khi tác vụ video vừa nhả token, Quick Preview và ảnh đơn lập tức thực thi bình thường mà không cần can thiệp thủ công.
   - Hàm `get_status()` trả về bổ sung trường `active_tasks: len(self._active_tokens)` giúp frontend giám sát chính xác tải của worker.

---

### 3.3. KHẮC PHỤC AUDIT53-03 (MEDIUM): VIDEO CANCELLATION SEMANTICS & SUBPROCESS CLEANUP

#### A. Nguyên nhân gốc rễ (Root Cause)
Trước Phase 5.4, ngữ nghĩa hủy tác vụ video (cancellation) chưa được chuẩn hóa:
- Khi người dùng gửi lệnh Hủy (`/cancel`), luồng xử lý FFmpeg hoặc FrameProcessor ném ra lỗi hoặc ngắt tiến trình. Tuy nhiên, khối `try...except Exception` tổng quát của `VideoPipeline.run_job` bắt lấy ngoại lệ và tự động chuyển trạng thái job thành `STATUS_FAILED` thay vì `STATUS_CANCELLED`.
- Việc nhầm lẫn giữa `FAILED` (lỗi hệ thống/phần mềm) và `CANCELLED` (hành động chủ động của người dùng) gây sai lệch báo cáo, khiến cơ chế tự động phục hồi khi khởi động lại ứng dụng (Crash Recovery) xử lý sai ngữ cảnh.
- Tiến trình con FFmpeg chạy ngầm khi bị hủy không có cơ chế `proc.terminate()` và `proc.wait(timeout=...)` chuẩn xác, tiềm ẩn nguy cơ sinh ra orphan process chiếm dụng CPU/GPU.
- Nếu hủy ở giai đoạn cuối (Audio Merge), file tạm chưa hoàn chỉnh có nguy cơ bị ghi đè hoặc để lại file rác gây hiểu lầm.

#### B. Giải pháp kỹ thuật đã triển khai
1. **Định nghĩa Ngoại lệ Hủy Chuyên biệt (Specialized Cancellation Exceptions):**
   - Trong `video/ffmpeg_service.py`: Tạo lớp `class FFmpegCancelledException(RuntimeError): pass`.
   - Trong `video/video_pipeline.py`: Tạo lớp `class VideoJobCancelledException(RuntimeError): pass`.
   Khi phát hiện `cancel_event.is_set()`, dịch vụ FFmpeg và các bước pipeline ném ra chính xác lớp ngoại lệ này thay vì các ngoại lệ runtime chung chung.

2. **Dọn dẹp Subprocess FFmpeg phòng thủ:**
   Trong `video/ffmpeg_service.py`, cơ chế giám sát tiến trình FFmpeg bằng `select`/`time.sleep` kiểm tra `cancel_event` liên tục. Khi cờ hủy được bật:
   - Gửi tín hiệu `proc.terminate()`.
   - Chờ tối đa 5 giây với `proc.wait(timeout=5)`.
   - Nếu tiến trình chưa thoát, cưỡng chế bằng `proc.kill()`.
   - Đảm bảo 100% không để lại tiến trình mồ côi (zero orphan process).

3. **Chuyển đổi trạng thái chính xác (`STATUS_CANCELLED`):**
   Trong `video/video_pipeline.py`, khối bắt ngoại lệ tách biệt rõ ràng:
   ```python
   except (VideoJobCancelledException, FFmpegCancelledException):
       logger.info(f"[VideoPipeline] Job {job_id} cancelled by user request.")
       self.job_manager.transition_status(job_id, STATUS_CANCELLED)
       # Xóa file video đích tạm nếu chưa hoàn thiện
       if temp_final.exists():
           temp_final.unlink()
       return False
   except Exception as e:
       logger.error(f"[VideoPipeline] Job {job_id} failed with error: {e}")
       self.job_manager.transition_status(job_id, STATUS_FAILED, error_message=str(e))
       return False
   ```

4. **Bảo toàn các Frame đã xử lý (Preservation of Enhanced Frames):**
   Khi job bị hủy, toàn bộ các frame đã được upscale thành công trong `workspace/<job_id>/enhanced/` được bảo toàn nguyên vẹn trên đĩa, cho phép người dùng tiếp tục hoặc tái sử dụng mà không làm mất công sức tính toán của GPU.

5. **Tính bất biến và Idempotency của API Cancel:**
   Trong `app.py`:
   - Nếu job đã là `COMPLETED` hoặc `FAILED`, API từ chối hủy và trả về **400 Bad Request**.
   - Nếu job đã là `CANCELLED`, API trả về **200 OK** một cách an toàn (idempotent).
   - Kiểm tra cờ hủy ngay tại vạch xuất phát của `run_job()`, ngăn chặn việc pipeline khởi chạy nếu cờ hủy đã được kích hoạt trước đó.

---

## 4. BẢNG ĐỐI CHIẾU THAY ĐỔI MÃ NGUỒN (CODE MODIFICATION INVENTORY)

| File | Module | Chức năng thay đổi chính |
| :--- | :--- | :--- |
| `video/video_job_manager.py` | Video Job Management | Thêm `claim_job_for_execution()` với atomic SQL UPDATE; thêm `release_job_claim()`; cập nhật `VALID_VIDEO_TRANSITIONS` cho phép chuyển sang `STATUS_CANCELLED` từ mọi trạng thái in-progress. |
| `video/video_pipeline.py` | Video Orchestration | Thêm `_active_threads` tracking; định nghĩa `VideoJobCancelledException`; bắt riêng ngoại lệ Cancel để ghi nhận `STATUS_CANCELLED`; dọn dẹp `temp_final` an toàn; bảo toàn cancel signal trước khi chạy. |
| `video/frame_processor.py` | Frame Inference Loop | Tích hợp `worker.acquire_busy()` và `worker.release_busy()` trong khối `try...finally`; khôi phục trạng thái READY khi Pause, Cancel, hoặc Hoàn tất. |
| `video/ffmpeg_service.py` | FFmpeg Subprocess Wrap | Định nghĩa `FFmpegCancelledException`; hoàn thiện cơ chế ngắt tiến trình `proc.terminate()` / `proc.kill()` không để lại orphan process. |
| `ai_worker.py` | AI Model Worker | Bổ sung `_state_lock`, `_active_tokens` map; triển khai `acquire_busy()`, `release_busy()`, `busy_scope()`; bảo vệ Quick Preview và ảnh đơn khi worker bận; báo cáo `active_tasks`. |
| `app.py` | Flask Web Backend | Cập nhật `/api/video/jobs/<id>/start` và `/resume` dùng atomic claim; trả về 409 Conflict khi trùng lặp; chuẩn hóa `/cancel` với kiểm tra trạng thái và tính idempotent. |
| `tests/test_phase5_4_fixes.py` | Test Verification Suite | Tạo mới bộ 29 unit, integration và concurrency test cases kiểm thử chuyên sâu 3 finding AUDIT53-01, AUDIT53-02, AUDIT53-03. |

---

## 5. KẾT QUẢ KIỂM THỬ TOÀN DIỆN (COMPREHENSIVE TEST RESULTS)

### 5.1. Tổng hợp kết quả toàn bộ Test Suites
Toàn bộ quy trình kiểm thử tự động được thực hiện trực tiếp trên môi trường Python 3.10.21 (`realesrgan` conda env) với phần cứng GPU NVIDIA RTX 2050:

| Nhóm bài kiểm thử | File Test | Số lượng Test | Kết quả | Thời gian chạy |
| :--- | :--- | :---: | :---: | :---: |
| **Phase 5.4 Fixes Suite** | `tests.test_phase5_4_fixes` | **29** | **29 / 29 PASS** | 3.84s |
| **Phase 1: Standardization** | `tests.test_phase1_standardization` | **12** | **12 / 12 PASS** | 1.85s |
| **Pre-Flight Remediation** | `tests.test_preflight_remediation` | **16** | **16 / 16 PASS** | 2.10s |
| **Phase 2: AI Engine Optimization** | `tests.test_phase2_ai_optimization` | **19** | **19 / 19 PASS** | 6.80s |
| **Phase 3: Reliability & Recovery** | `tests.test_phase3_reliability` | **24** | **24 / 24 PASS** | 4.95s |
| **Phase 4: UX & Batch Management**| `tests.test_phase4_ux` | **44** | **44 / 44 PASS** | 6.30s |
| **Phase 5: Video Enhancement** | `tests.test_phase5_video` | **76** | **76 / 76 PASS** | 10.92s |
| **TỔNG CỘNG HỆ THỐNG** | **Toàn bộ 7 test modules** | **220** | **220 / 220 PASS** | **36.76s** |

> **Ghi chú quan trọng:** Tỷ lệ đạt **100% (220/220 test cases)**. Không có bất kỳ lỗi nào (`errors=0`), không có bài kiểm thử thất bại (`failures=0`), và không có bài kiểm thử bị bỏ qua (`skipped=0`).

---

### 5.2. Chi tiết 29 bài kiểm thử Phase 5.4 (`tests/test_phase5_4_fixes.py`)

#### Nhóm AUDIT53-02: Video Job Concurrency & Atomic Claim (11 Tests)
1. `test_01_atomic_claim_success_from_queued`: Xác minh job ở trạng thái QUEUED chỉ có thể claim thành công 1 lần duy nhất; lần gọi thứ 2 bị từ chối ngay lập tức. (**PASS**)
2. `test_02_concurrent_claim_threads_same_job`: Khởi chạy 2 thread đồng thời qua `threading.Barrier` cùng tranh chấp claim 1 job; đúng 1 thread thành công, 1 thread thất bại. (**PASS**)
3. `test_03_concurrent_resume_same_job`: 2 thread đồng thời yêu cầu resume một job đang PAUSED; chỉ đúng 1 thread claim thành công. (**PASS**)
4. `test_04_concurrent_start_and_resume_same_job`: 1 thread gọi start và 1 thread gọi resume đồng thời trên job QUEUED; chỉ thread start hợp lệ thành công. (**PASS**)
5. `test_05_two_different_jobs_can_claim_concurrently`: 2 job khác nhau có thể được claim đồng thời mà không bị khóa chéo hay nghẽn. (**PASS**)
6. `test_06_claim_fails_for_terminal_statuses`: Xác minh không thể claim các job đã ở trạng thái kết thúc (COMPLETED, FAILED, CANCELLED). (**PASS**)
7. `test_07_api_start_duplicate_returns_409`: API `/api/video/jobs/<id>/start` trả về mã HTTP 409 Conflict khi job đang trong trạng thái PROCESSING. (**PASS**)
8. `test_08_api_start_terminal_returns_400`: API start trả về HTTP 400 Bad Request nếu job đã COMPLETED. (**PASS**)
9. `test_09_pipeline_prevents_duplicate_active_thread`: VideoPipeline từ chối khởi chạy thread thứ 2 cho cùng một job_id khi thread đầu tiên vẫn đang hoạt động. (**PASS**)
10. `test_10_release_job_claim_on_failure`: Hàm `release_job_claim` trả trạng thái job về đúng trạng thái lỗi mục tiêu kèm thông báo lỗi. (**PASS**)
11. `test_11_startup_crash_recovery_resets_processing`: Khi ứng dụng khởi động lại sau sự cố, job đang PROCESSING được tự động khôi phục về PAUSED/QUEUED. (**PASS**)

#### Nhóm AUDIT53-01: AI Worker State Recovery & Lifecycle Management (8 Tests)
12. `test_01_token_acquisition_and_release`: Token cấp phát và thu hồi an toàn theo cơ chế ref-count, giải phóng 2 lần không làm âm biến đếm. (**PASS**)
13. `test_02_busy_scope_context_manager`: Context manager `busy_scope` luôn hoàn trả trạng thái READY ngay cả khi khối code bên trong ném ra ngoại lệ. (**PASS**)
14. `test_03_frame_processor_restores_state_on_completion`: FrameProcessor tự động giải phóng token và đưa worker về trạng thái READY khi xử lý xong toàn bộ frame. (**PASS**)
15. `test_04_frame_processor_restores_state_on_pause`: FrameProcessor hoàn trả worker về trạng thái READY ngay khi phát hiện tín hiệu Pause. (**PASS**)
16. `test_05_frame_processor_restores_state_on_cancel`: FrameProcessor hoàn trả worker về trạng thái READY ngay khi phát hiện tín hiệu Cancel. (**PASS**)
17. `test_06_quick_preview_rejected_when_worker_busy`: Quick Preview trả về lỗi an toàn `"AI Worker đang bận..."` khi worker đang giữ token video. (**PASS**)
18. `test_07_quick_preview_succeeds_after_video_releases`: Quick Preview hoạt động trơn tru ngay lập tức sau khi tác vụ video nhả token. (**PASS**)
19. `test_08_get_status_reflects_active_tasks`: API trạng thái của worker phản ánh chính xác số lượng `active_tasks` và trạng thái `state` theo thời gian thực. (**PASS**)

#### Nhóm AUDIT53-03: Video Cancellation Semantics & Subprocess Cleanup (10 Tests)
20. `test_01_ffmpeg_service_raises_cancelled_exception`: FFmpegService ném chính xác `FFmpegCancelledException` khi cờ cancel được bật. (**PASS**)
21. `test_02_pipeline_cancel_during_extraction_sets_cancelled_status`: Hủy trong giai đoạn trích xuất frame chuyển job thành CANCELLED (không phải FAILED). (**PASS**)
22. `test_03_pipeline_cancel_during_reconstruction_sets_cancelled_status`: Hủy trong giai đoạn ghép frame chuyển job thành CANCELLED (không phải FAILED). (**PASS**)
23. `test_04_pipeline_cancel_during_audio_merge_sets_cancelled_status`: Hủy trong giai đoạn lồng tiếng chuyển job thành CANCELLED. (**PASS**)
24. `test_05_cancel_immediately_after_start`: Cờ hủy được kích hoạt ngay trước khi pipeline chạy sẽ dừng ngay và đặt trạng thái CANCELLED. (**PASS**)
25. `test_06_cancel_twice_is_safe_idempotent`: Gọi API cancel 2 lần liên tiếp đều trả về HTTP 200 an toàn, trạng thái giữ nguyên CANCELLED. (**PASS**)
26. `test_07_cancel_completed_job_returns_400`: Hủy một job đã hoàn thành (COMPLETED) bị từ chối với HTTP 400 Bad Request. (**PASS**)
27. `test_08_cancel_failed_job_returns_400`: Hủy một job đã thất bại (FAILED) bị từ chối với HTTP 400 Bad Request. (**PASS**)
28. `test_09_independent_error_remains_failed`: Ngoại lệ thông thường không có cờ hủy vẫn được ghi nhận chuẩn xác là FAILED kèm stack trace. (**PASS**)
29. `test_10_enhanced_frames_preserved_on_cancel`: Khi job bị hủy, toàn bộ các frame đã upscale trong thư mục workspace không bị xóa mất. (**PASS**)

---

## 6. ĐÁNH GIÁ RỦI RO CÒN LẠI (RESIDUAL RISK ASSESSMENT)

1. **Rủi ro xung đột tiến trình đa phiên bản:**  
   - *Phân tích:* Cơ chế `claim_job_for_execution` hoạt động ở cấp độ cơ sở dữ liệu SQLite trong chế độ WAL mode. Nếu người dùng mở nhiều instance ứng dụng cùng trỏ vào một file database, cơ chế atomic `UPDATE` vẫn đảm bảo chỉ 1 process nhận được quyền claim.
   - *Mức độ rủi ro:* **RẤT THẤP (NEGLIGIBLE)**.
2. **Rủi ro đầy đĩa khi hủy nhiều job video lớn:**  
   - *Phân tích:* Do hệ thống cố tình bảo toàn thư mục `enhanced/` để người dùng không bị mất kết quả khi lỡ tay bấm cancel, các job video dài bị hủy nhiều lần có thể chiếm dụng dung lượng đĩa.
   - *Biện pháp:* Hệ thống đã có cơ chế dọn dẹp workspace khi xóa job (`delete_video_job`), người dùng chỉ cần xóa job trên giao diện để giải phóng dung lượng đĩa hoàn toàn.
   - *Mức độ rủi ro:* **THẤP (LOW)**.
3. **Các phát hiện thuộc phạm vi Phase 5.5 (AUDIT53-04, AUDIT53-05):**  
   - *Phân tích:* Các khuyến nghị tối ưu hóa bộ nhớ tạm (Temporary Buffer Optimization) và cấu hình bitrate nâng cao thuộc diện Low/Informational findings đã được khoanh vùng đúng phạm vi và sẽ được xử lý ở các giai đoạn tiếp theo nếu có yêu cầu. Không ảnh hưởng đến độ ổn định cốt lõi của Phase 5.4.

---

## 7. KẾT LUẬN VÀ TUYÊN BỐ NGHIỆM THU (FINAL ACCEPTANCE STATEMENT)

Giai đoạn **Phase 5.4 — Critical & High Priority Bug Fixes** đã hoàn thành xuất sắc 100% mục tiêu đề ra:

1. Đã khắc phục triệt để lỗi concurrency và race condition trong quản lý vòng đời tác vụ video (**AUDIT53-02**).
2. Đã thiết lập cơ chế quản lý trạng thái AI Worker dựa trên token và lifecycle context manager an toàn, ngăn chặn hoàn toàn hiện tượng kẹt trạng thái `BUSY` (**AUDIT53-01**).
3. Đã chuẩn hóa ngữ nghĩa hủy tác vụ video, bảo đảm dọn dẹp tiến trình con FFmpeg và bảo toàn kết quả frame trên đĩa (**AUDIT53-03**).
4. Xác nhận **220 / 220 bài kiểm thử (100%)** vượt qua thành công, không phát sinh bất kỳ lỗi hồi quy nào trên toàn bộ hệ thống từ Phase 1 đến Phase 5.
5. Model AI `weights/RealESRGAN_x4plus.pth` được bảo toàn nguyên vẹn với mã băm SHA-256 đối chiếu tuyệt đối chính xác.

Theo nguyên tắc chỉ đạo nghiêm ngặt, hệ thống dừng lại tại điểm hoàn thành Phase 5.4 và bàn giao toàn bộ tài liệu kiểm toán nghiệm thu cho người dùng.
