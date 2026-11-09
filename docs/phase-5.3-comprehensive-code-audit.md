# BÁO CÁO KIỂM TOÁN TOÀN DIỆN MÃ NGUỒN, PHÁT HIỆN XUNG ĐỘT & ĐÁNH GIÁ ĐỘ ỔN ĐỊNH HỆ THỐNG
## COMPREHENSIVE CODE AUDIT, CONFLICT DETECTION & RUNTIME STABILITY REPORT — PHASE 5.3

**Dự án:** AI Image Studio  
**Thư mục làm việc:** `E:\AI Image Studio\RealESRGAN-v030`  
**Môi trường:** Conda `realesrgan` | Python 3.10.21 | PyTorch 2.6.0+cu126 | Torchvision 0.21.0+cu126 | CUDA 12.6  
**Phần cứng mục tiêu:** NVIDIA GeForce RTX 2050 Laptop GPU (4GB VRAM)  
**AI Model:** `RealESRGAN_x4plus.pth` (SHA-256: `4FA0D38905F75AC06EB49A7951B426670021BE3018265FD191D2125DF9D682F1`)  
**Backend:** Flask 3.1.0 + SQLite WAL mode (`jobs.db`)  
**Video Subsystem:** FFmpeg 7.1-full_build-www.gyan.dev / FFprobe  
**Hình thức kiểm toán:** READ-ONLY AUDIT (Tuyệt đối không sửa mã nguồn, không sửa database schema/data, không chạy benchmark gây tải nặng GPU)  
**Ngày thực hiện:** 09/11/2026  

---

## 1. TỔNG QUAN ĐIỀU HÀNH (EXECUTIVE SUMMARY)

Giai đoạn 5.3 thực hiện một cuộc kiểm toán kỹ thuật toàn diện, đa chiều và độc lập trên toàn bộ mã nguồn của hệ thống **AI Image Studio** từ Phase 1 đến Phase 5. Mục tiêu trọng tâm là kiểm tra tính tương thích liên module, loại trừ nguy cơ race condition / deadlock trên GPU và cơ sở dữ liệu, rà soát tính toàn vẹn của máy trạng thái công việc (Job State Machine), bảo mật truy cập tệp tin và rò rỉ tài nguyên hệ thống.

### Kết quả kiểm toán tổng quát:
- **Tổng số tệp mã nguồn và cấu hình được kiểm toán:** 28 tệp (bao gồm toàn bộ mã nguồn ứng dụng, module video, công cụ hỗ trợ, cấu hình, kiểm thử và tài liệu).
- **Tổng số API endpoints được đối chiếu:** 44 route endpoints trên Flask backend và toàn bộ các hàm gọi API / SSE listener trên giao diện web (`templates/index.html`).
- **Tổng số phát hiện (Findings):** 5 phát hiện, trong đó:
  - **CRITICAL:** 0 (Không phát hiện lỗi gây crash hệ thống, rò rỉ VRAM nghiêm trọng, hỏng dữ liệu hoặc lỗ hổng bảo mật nghiêm trọng).
  - **HIGH:** 1 (Nguy cơ race condition khi khởi động đồng thời nhiều tác vụ video).
  - **MEDIUM:** 2 (Trạng thái AIWorker bị giữ ở mức `BUSY` sau khi hoàn thành tác vụ video; Lỗi hủy video qua FFmpeg bị đánh dấu nhầm thành `FAILED`).
  - **LOW:** 1 (Vòng đời kết nối SQLite trong các hàm truy vấn phụ thuộc vào bộ thu gom rác CPython).
  - **INFO:** 1 (Lặp đoạn mã bóc tách tham số tại hai endpoint xuất báo cáo / xuất file ZIP).
- **Đánh giá độ ổn định thời gian thực (Runtime Stability):** **ĐẠT (STABLE)** với điều kiện xử lý các khuyến nghị trước khi đưa vào môi trường sản xuất quy mô lớn. Không có xung đột trực tiếp trên tài nguyên tính toán GPU giữa pipeline ảnh và video nhờ cơ chế khóa `worker._lock`.

---

## 2. PHẠM VI VÀ NGUYÊN TẮC AN TOÀN KIỂM TOÁN (AUDIT SCOPE & SAFETY PRINCIPLES)

### 2.1. Danh mục các tệp được kiểm toán trực tiếp:
1. **Lớp Core AI & GPU:** `ai_worker.py` (640 dòng), `gpu_monitor.py` (61 dòng).
2. **Lớp Pipeline Video:** `video/ffmpeg_service.py` (264 dòng), `video/frame_extractor.py` (88 dòng), `video/frame_processor.py` (217 dòng), `video/video_pipeline.py` (366 dòng), `video/video_job_manager.py` (430 dòng), `video/video_report.py` (348 dòng).
3. **Lớp Quản lý Công việc & Dữ liệu:** `job_manager.py` (830 dòng), `job_logger.py` (150 dòng).
4. **Lớp Giao diện & Báo cáo:** `app.py` (2504 dòng), `templates/index.html` (3069 dòng), `batch_exporter.py` (199 dòng), `report_generator.py` (374 dòng).
5. **Cấu hình & Môi trường:** `config.json`, `environment.yml`, `Start_ImageStudio.bat`.
6. **Bộ kiểm thử & Benchmark:** `tests/test_phase5_video.py` (520 dòng), `tests/test_p1_preflight.py`, `tests/test_p2_performance.py`, `tests/test_p3_reliability.py`, `tests/test_p4_ux.py`, `tests/test_p4_reports.py`.

### 2.2. Tuân thủ nguyên tắc an toàn:
- **Chế độ Read-Only tuyệt đối:** Không thực hiện bất kỳ lệnh ghi, sửa đổi mã nguồn, xóa tệp tin hoặc di chuyển vị trí thư mục.
- **Bảo toàn CSDL & Trọng số Model:** Không thực thi DDL/DML chỉnh sửa `jobs.db`; không tác động lên tệp trọng số `weights/RealESRGAN_x4plus.pth`.
- **Không gây tải phần cứng:** Không chạy các bài kiểm thử nặng hoặc ép xung GPU trong quá trình kiểm toán.

---

## 3. KIẾN TRÚC TOÀN HỆ THỐNG (FULL SYSTEM ARCHITECTURE P1–P5)

```
+----------------------------------------------------------------------------------------------------+
|                                    TRÌNH DUYỆT NGƯỜI DÙNG (FRONTEND)                               |
|   HTML5 + Vanilla CSS3 + Modern JavaScript (Dual-Tab: Image Enhancement & Video Enhancement)      |
|   - REST API Communication (Fetch JSON / FormData)                                                 |
|   - Realtime Server-Sent Events (SSE: /api/events, /api/video/events)                              |
+-------------------------------------------------+--------------------------------------------------+
                                                  | HTTP / SSE (Port 5678)
+-------------------------------------------------v--------------------------------------------------+
|                                    FLASK BACKEND CONTROLLER (app.py)                               |
|   - Image REST Handlers (Phase 1-4)                 - Video REST Handlers (Phase 5)                |
|   - SSE Multi-client Dispatcher                     - Video SSE Event Broadcaster                  |
|   - Path Security & Parameter Sanitization          - Workspace Lifecycle Management               |
+-----------------------+-------------------------------------------------+--------------------------+
                        |                                                 |
       +----------------v----------------+               +----------------v---------------+
       |    IMAGE JOB MANAGER            |               |    VIDEO JOB MANAGER           |
       |    (job_manager.py)             |               |    (video_job_manager.py)      |
       +----------------+----------------+               +----------------+---------------+
                        |                                                 |
                        +-----------------------+-------------------------+
                                                |
                               +----------------v---------------+
                               |     SQLITE WAL DATABASE        |
                               |          (jobs.db)             |
                               |   Tables: jobs, video_jobs     |
                               +--------------------------------+
                                                |
       +----------------------------------------+----------------------------------------+
       |                                                                                 |
+------v----------------------------------+                   +--------------------------v-------------------+
|      IMAGE PROCESSING PIPELINE          |                   |          VIDEO PROCESSING PIPELINE           |
|  - In-Memory Queue (job_manager.py)     |                   |  - VideoPipeline (video_pipeline.py)         |
|  - Worker Thread Loop                   |                   |  - FrameExtractor (frame_extractor.py)       |
|  - Tile Step-down & Retry               |                   |  - FrameProcessor (frame_processor.py)       |
|  - WebP/PNG/JPEG Encoding               |                   |  - FFmpegService (ffmpeg_service.py)         |
+----------------------+------------------+                   +--------------------------+-------------------+
                       |                                                                 |
                       +--------------------------------+--------------------------------+
                                                        |
                                       +----------------v---------------+
                                       |       AI WORKER SINGLETON      |
                                       |        (ai_worker.py)          |
                                       |  - Model: RealESRGAN_x4plus    |
                                       |  - Mutex Lock: worker._lock    |
                                       |  - Auto-Tile (512 -> 128)      |
                                       |  - torch.cuda.empty_cache()    |
                                       +----------------+---------------+
                                                        |
                                       +----------------v---------------+
                                       |      NVIDIA RTX 2050 (4GB)     |
                                       |         PyTorch 2.6.0          |
                                       +--------------------------------+
```

---

## 4. BIỂU ĐỒ PHỤ THUỘC MODULE (MODULE DEPENDENCY GRAPH)

Kiểm tra phân tầng kiến trúc xác nhận không tồn tại hiện tượng vòng lặp phụ thuộc (circular imports). Phụ thuộc giữa các module được định nghĩa rõ ràng theo phân cấp:

```
[app.py]
  ├── [ai_worker.py] <-------------------------+
  │     ├── [gpu_monitor.py]                   |
  │     └── [basicsr / realesrgan engine]      |
  ├── [job_manager.py]                         | (Chia sẻ AIWorker Singleton)
  │     ├── [ai_worker.py]                     |
  │     └── [job_logger.py]                    |
  ├── [video/video_pipeline.py]                |
  │     ├── [video/video_job_manager.py]       |
  │     ├── [video/ffmpeg_service.py]          |
  │     ├── [video/frame_extractor.py]         |
  │     └── [video/frame_processor.py] --------+
  ├── [report_generator.py]
  ├── [batch_exporter.py]
  └── [video/video_report.py]
```

---

## 5. KIỂM TOÁN TƯƠNG TÁC AI WORKER & GPU CONCURRENCY

### 5.1. Cơ chế Singleton và Khóa Bảo vệ GPU Mutex
- Mã nguồn tại `ai_worker.py` (L59–L85) cài đặt mẫu Singleton cho lớp `AIWorker` thông qua biến lớp `_instance` và khóa khởi tạo `_instance_lock`.
- Mọi thao tác suy luận (Inference) trên GPU trong cả hai pipeline ảnh và video đều phải chiếm khóa luồng `self.worker._lock`:
  - Pipeline Ảnh: `ai_worker.py` L380: `with self._lock:`
  - Pipeline Video: `video/frame_processor.py` L181: `with self.worker._lock:`
- **Kết luận:** Nguy cơ hai luồng cùng gửi tensor lên GPU đồng thời gây xung đột bộ nhớ phân mảnh hoặc crash driver NVIDIA đã được triệt tiêu 100%.

### 5.2. Tính toán Auto-Tile và Giới hạn VRAM
- Thuật toán Auto-Tile tại `ai_worker.py` (L230–L256) tự động phân bổ kích thước tile dựa trên độ phân giải đầu vào và dung lượng VRAM thực tế của RTX 2050 (4GB):
  - Kích thước $< 800\times 800$: Tile 512.
  - Kích thước $< 1600\times 1600$: Tile 384.
  - Kích thước $< 2400\times 2400$: Tile 256.
  - Kích thước lớn hơn: Tile 192 (hoặc step-down về 128 / 64 khi gặp OOM).
- Cơ chế bắt lỗi `torch.cuda.OutOfMemoryError` tại `ai_worker.py` (L434–L460) tự động giải phóng bộ nhớ `torch.cuda.empty_cache()` và hạ tile size xuống một bậc (512 $\to$ 384 $\to$ 256 $\to$ 192 $\to$ 128 $\to$ 64). Nếu tile 64 vẫn lỗi, hệ thống kích hoạt CPU fallback an toàn.

### 5.3. Phát hiện lỗi trạng thái Worker: AUDIT53-01 (MEDIUM)
- **Vị trí mã nguồn:** `video/frame_processor.py` dòng 125, dòng 165.
- **Chi tiết:** Khi xử lý khung hình video, `FrameProcessor.process_frames` thực thi lệnh:
  ```python
  self.worker.state = STATE_BUSY  # Dòng 125
  ```
  Tuy nhiên, khi vòng lặp duyệt khung hình kết thúc (hoàn thành bình thường, người dùng ấn Pause, Cancel hoặc xảy ra ngoại lệ), mã nguồn **hoàn toàn không có lệnh reset** `self.worker.state = STATE_READY`.
- **Hệ quả thực tế:** Sau khi một tác vụ video kết thúc, `AIWorker.state` vẫn bị kẹt ở giá trị `BUSY`. Dẫn tới:
  1. Endpoint `/api/gpu` báo trạng thái GPU là `BUSY` mặc dù GPU đang rảnh rỗi.
  2. Endpoint `/api/preview/generate` (Quick Preview cho ảnh) kiểm tra điều kiện tại `ai_worker.py` L517:
     ```python
     if self.state == STATE_BUSY:
         raise RuntimeError("AI Worker đang bận xử lý hàng đợi")
     ```
     sẽ liên tục từ chối yêu cầu tạo preview của người dùng với thông báo lỗi bận cho đến khi có một tác vụ ảnh mới được đưa vào xử lý và hoàn tất (vì worker ảnh có reset trạng thái).
- **Khuyến nghị khắc phục:** Đặt khối xử lý khung hình video trong `try...finally:` để đảm bảo `self.worker.state = STATE_READY` luôn được gọi khi thoát luồng.

---

## 6. KIỂM TOÁN MÁY TRẠNG THÁI CÔNG VIỆC (JOB STATE MACHINE AUDIT)

### 6.1. Máy trạng thái tác vụ ảnh (Image Jobs)
- Các trạng thái hợp lệ: `pending` $\to$ `processing` $\to$ `completed` / `failed` / `cancelled`.
- Hàng đợi FIFO được quản lý đồng thời qua SQLite và bộ nhớ `queue.Queue`.
- Cơ chế khởi động lại an toàn (Startup Recovery): Hàm `recover_crashed_jobs()` tại `job_manager.py` (L125–L148) tự động chuyển các job đang ở trạng thái `processing` khi ứng dụng bị tắt đột ngột về `failed` với thông báo lỗi phục hồi sau sự cố rõ ràng.

### 6.2. Máy trạng thái tác vụ video (Video Jobs)
- Các trạng thái hợp lệ: `pending` $\to$ `queued` $\to$ `extracting` $\to$ `processing` $\to$ `reconstructing` $\to$ `merging_audio` $\to$ `completed` / `paused` / `failed` / `cancelled`.
- Chuyển tiếp trạng thái được ghi nhận đầy đủ qua `video/video_job_manager.py` và phát sự kiện SSE tương ứng đến trình duyệt.
- Cơ chế phục hồi khi khởi động (Startup Recovery): `VideoJobManager.recover_interrupted_jobs()` tại `video/video_job_manager.py` (L180–L215) quét toàn bộ các trạng thái trung gian (`extracting`, `processing`, `reconstructing`, `merging_audio`) và đưa về `paused` (giữ lại các khung hình đã xử lý) hoặc `queued` (để trích xuất lại từ đầu).

### 6.3. Phát hiện lỗi xung đột khởi động Video Job: AUDIT53-02 (HIGH)
- **Vị trí mã nguồn:** `app.py` dòng 2240–2246, 2271–2288, 2301–2318; `video/video_pipeline.py` dòng 95, dòng 128.
- **Chi tiết:**
  - Endpoint `/api/video/jobs/<job_id>/start` chỉ kiểm tra điều kiện:
    ```python
    if job['status'] != STATUS_PENDING:
        return jsonify({'error': ...}), 400
    ```
    và sau đó lập tức tạo một `threading.Thread(target=self.video_pipeline.run_job, ...).start()`.
  - Tương tự, `/api/video/jobs/<job_id>/resume` chỉ kiểm tra `job['status'] != STATUS_PAUSED`.
  - Hệ thống **không hề kiểm tra** xem hiện tại có video job nào khác đang chạy hay không, và cũng không dùng cờ khóa luồng cấp controller để ngăn người dùng nhấp đúp (double-click) chuột vào nút Start/Resume.
  - Trong `video_pipeline.py`, thuộc tính `self._active_threads = {}` được khởi tạo tại L95 nhưng trong hàm `run_job` (L128) không hề cập nhật hoặc kiểm tra từ điển này.
- **Hệ quả thực tế:** Nếu người dùng gửi 2 yêu cầu Start/Resume liên tiếp trong khoảng thời gian ngắn hoặc cố tình mở 2 tab trình duyệt để Start 2 video khác nhau, 2 luồng background sẽ cùng chạy song song. Mặc dù GPU không bị crash nhờ `worker._lock`, nhưng 2 luồng sẽ tranh chấp CPU, I/O ổ đĩa, ghi đè biến điều khiển Auto-Tile và có nguy cơ gây tắc nghẽn hàng đợi FFmpeg.
- **Khuyến nghị khắc phục:**
  1. Bổ sung kiểm tra trạng thái nguyên tử trong database: `UPDATE video_jobs SET status = 'processing' WHERE id = ? AND status IN ('pending', 'paused')` và kiểm tra số bản ghi bị tác động.
  2. Bổ sung cờ kiểm tra `is_any_job_active()` trước khi cho phép kích hoạt luồng xử lý mới.

---

## 7. KIỂM TOÁN TƯƠNG TÁC VÀ GIAO DỊCH SQLITE (SQLITE CONCURRENCY AUDIT)

### 7.1. Cấu hình WAL Mode và Timeout
- Cả hai kết nối CSDL tại `job_manager.py` (L75–L95) và `video/video_job_manager.py` (L65–L85) đều thực thi:
  ```sql
  PRAGMA journal_mode = WAL;
  PRAGMA synchronous = NORMAL;
  PRAGMA foreign_keys = ON;
  ```
  kèm thiết lập `timeout = 20.0` giây khi mở kết nối.
- Chế độ WAL cho phép các luồng đọc (Readers) từ API không bao giờ bị chặn bởi luồng ghi (Writer) từ worker, giải quyết triệt để lỗi `sqlite3.OperationalError: database is locked` trong điều kiện tải bình thường.

### 7.2. Kiểm toán vòng đời kết nối: AUDIT53-04 (LOW)
- **Vị trí mã nguồn:** `job_manager.py` dòng 91; `video/video_job_manager.py` dòng 80.
- **Chi tiết:** Phương thức `get_connection()` tạo mới một `sqlite3.connect()`. Các phương thức truy vấn dùng cú pháp:
  ```python
  conn = self.get_connection()
  with conn:
      cursor = conn.cursor()
      ...
  ```
  Trong Python `sqlite3`, cú pháp `with conn:` chỉ quản lý transaction (tự động `COMMIT` khi thoát khối và `ROLLBACK` khi có ngoại lệ), nhưng **không đóng kết nối** (`conn.close()`). Việc giải phóng file descriptor dựa hoàn toàn vào cơ chế garbage collection (đếm tham chiếu) của CPython.
- **Hệ quả thực tế:** Dưới tải đa luồng cao hoặc nếu phát sinh tham chiếu vòng, các file handle kết nối có thể tồn tại lâu hơn cần thiết.
- **Khuyến nghị khắc phục:** Bọc việc mở kết nối bằng `contextlib.closing` hoặc khối `try...finally: conn.close()`.

---

## 8. KIỂM TOÁN PIPELINE XỬ LÝ ẢNH (IMAGE PIPELINE AUDIT)

### 8.1. Kiểm tra đầu vào và định dạng hỗ trợ
- Hệ thống hỗ trợ đầy đủ các định dạng: `PNG`, `JPG/JPEG`, `WEBP`, `BMP`.
- Kiểm tra tính hợp lệ của tệp ảnh đầu vào bằng thư viện `PIL.Image.verify()` kết hợp kích thước tối đa và độ phân giải tối đa cấu hình trong `config.json`.
- Chống tấn công Decompression Bomb qua thiết lập trần pixel `Image.MAX_IMAGE_PIXELS = 100_000_000`.

### 8.2. Bảo toàn Metadata và Xuất dữ liệu
- Pipeline sử dụng `piexif` và bộ giải mã của PIL để sao chép nguyên vẹn dữ liệu EXIF/ICC Profile từ ảnh gốc sang ảnh kết quả.
- Các định dạng xuất khẩu (JPEG chất lượng cao, PNG tối ưu nén, WebP lossy/lossless) hoạt động chính xác theo cấu hình người dùng yêu cầu.

---

## 9. KIỂM TOÁN PIPELINE XỬ LÝ VIDEO & DỊCH VỤ FFMPEG

### 9.1. Trích xuất khung hình và Bảo toàn Timestamp
- `video/frame_extractor.py` sử dụng tham số `-fps_mode passthrough` (hoặc `-vsync 0`) kết hợp định dạng ảnh chuỗi `%08d.png`. Điều này đảm bảo trích xuất chính xác 100% khung hình đối với cả luồng video VFR (Variable Frame Rate) và CFR (Constant Frame Rate) mà không làm lệch nhịp hiển thị.
- Thuộc tính `is_vfr` được phát hiện thông qua so sánh sai số giữa `r_frame_rate` và `avg_frame_rate` từ `ffprobe`.

### 9.2. Tái dựng Video và Đồng bộ Âm thanh / Hình ảnh
- Tái dựng video tại `video/ffmpeg_service.py` sử dụng bộ mã hóa `libx264` với tham số `-pix_fmt yuv420p`, `-movflags +faststart` giúp tệp tin MP4 có thể phát trực tiếp trên trình duyệt web ngay khi tải một phần.
- Luồng âm thanh được trích xuất bằng stream-copy nguyên bản (`-c:a copy`). Nếu luồng đích không tương thích hoặc lỗi giải mã, hệ thống tự động fallback sang mã hóa chuẩn `aac -b:a 192k`. Các video không có luồng audio được gắn cờ và bỏ qua giai đoạn ghép âm an toàn.

### 9.3. Xử lý khung hình lỗi (Corrupt Frame Resilience)
- Trong `video/frame_processor.py` (L187–L205), nếu một khung hình bị lỗi trong quá trình suy luận AI, hệ thống bắt ngoại lệ và tự động dùng thuật toán nội suy chất lượng cao `cv2.resize(img, (w*4, h*4), interpolation=cv2.INTER_LANCZOS4)` để tạo khung hình thay thế.
- **Kết luận:** Cơ chế này đảm bảo tổng số lượng khung hình của video đầu ra luôn bằng đúng số khung hình trích xuất, loại trừ hoàn toàn nguy cơ rớt khung hình (frame drop) hoặc lệch nhịp đồng bộ âm thanh (audio desync).

### 9.4. Phát hiện lỗi ghi đè trạng thái khi Hủy Video: AUDIT53-03 (MEDIUM)
- **Vị trí mã nguồn:** `video/video_pipeline.py` dòng 217, dòng 361–366; `video/ffmpeg_service.py` dòng 244.
- **Chi tiết:** Khi người dùng gửi yêu cầu hủy (`cancel_event.set()`) trong lúc FFmpeg đang chạy (trích xuất, dựng hình hoặc ghép audio), hàm `execute_ffmpeg` kích hoạt ngắt tiến trình con và ném ra ngoại lệ:
  ```python
  raise RuntimeError("FFmpeg process cancelled by user request.")
  ```
  Tại `video/video_pipeline.py`, khối `try...except Exception as e:` bao bọc bên ngoài tại dòng 361 bắt toàn bộ ngoại lệ và thực hiện:
  ```python
  self.manager.update_job_status(job_id, STATUS_FAILED, error_message=err_msg)
  self._emit_event("video_error", ...)
  ```
- **Hệ quả thực tế:** Khi người dùng chủ động nhấn nút Hủy trong các giai đoạn FFmpeg, tác vụ bị gán nhãn là `FAILED` với thông báo lỗi đỏ trên giao diện, thay vì chuyển sang trạng thái `CANCELLED` như mong đợi.
- **Khuyến nghị khắc phục:** Trong khối `except Exception as e:` của `video_pipeline.py`, kiểm tra nếu `cancel_event.is_set()` hoặc chuỗi thông báo chứa từ khóa `"cancelled"`, cập nhật trạng thái thành `STATUS_CANCELLED` thay vì `STATUS_FAILED`.

---

## 10. KIỂM TOÁN TÍCH HỢP GIAO DIỆN & BACKEND API (FRONTEND / BACKEND INTEGRATION AUDIT)

### 10.1. Đối chiếu Danh mục Endpoints & Trình xử lý Frontend
Toàn bộ 44 API routes trên Flask backend đã được đối chiếu chi tiết với mã nguồn JavaScript tại `templates/index.html`:

| Nhóm API | Số Endpoint | Kiểm tra gọi từ Frontend | Trạng thái tích hợp |
| :--- | :---: | :--- | :---: |
| **Giao diện & Cấu hình** | 3 | Route `/`, `/api/config`, `/api/gpu` được gọi khi tải trang và theo chu kỳ | **PASS** |
| **Quản lý Job Ảnh** | 12 | `/api/jobs/upload`, `/api/jobs/batch`, `/api/jobs/clear`, v.v. được liên kết đầy đủ | **PASS** |
| **Sự kiện Thời gian thực Ảnh** | 1 | EventSource kết nối tới `/api/events` lắng nghe sự kiện tiến độ | **PASS** |
| **Xem trước Nhanh (Preview)** | 2 | `/api/preview/generate` và route phục vụ ảnh preview | **PASS** |
| **Xuất tệp & Báo cáo Ảnh** | 4 | Tích hợp xuất tệp ZIP, báo cáo HTML, Markdown, CSV | **PASS** |
| **Quản lý Job Video** | 12 | `/api/video/jobs/create`, `/api/video/jobs/<id>/start`, `pause`, `resume`, `cancel`, v.v. | **PASS** |
| **Sự kiện Thời gian thực Video**| 1 | EventSource kết nối tới `/api/video/events` xử lý đầy đủ các event | **PASS** |
| **Xuất tệp & Báo cáo Video** | 4 | Tích hợp tải video MP4, báo cáo chi tiết video HTML/PDF | **PASS** |
| **Tĩnh & Tệp tin kết quả** | 5 | Phục vụ an toàn qua `send_from_directory` với kiểm tra đường dẫn an toàn | **PASS** |

### 10.2. Phát hiện lỗi trùng lặp tham số: AUDIT53-05 (INFO)
- **Vị trí mã nguồn:** `app.py` dòng 1853–1863, dòng 1893–1905.
- **Chi tiết:** Tại hai endpoint `/api/export/zip` và `/api/export/report`, logic trích xuất danh sách `job_ids` từ request hỗ trợ cả phương thức `GET` (qua `request.args`) và `POST` (qua `request.get_json()`) bị lặp lại từng dòng mã tương tự nhau.
- **Đánh giá:** Không gây lỗi vận hành hoặc nguy cơ bảo mật, chỉ là điểm cần cải thiện về tính thẩm mỹ của mã nguồn.

---

## 11. KIỂM TOÁN BẢO MẬT & LÀM SẠCH DỮ LIỆU ĐẦU VÀO (SECURITY AUDIT)

### 11.1. Chống tấn công Path Traversal
- Hàm `safe_path()` được triển khai chuẩn mực tại `app.py` (L105–L125), sử dụng `Path(path).resolve()` và kiểm tra tiền tố `path.startswith(base_dir)`.
- Mọi thao tác tải tệp ảnh gốc, tệp kết quả và video tải về đều đi qua hàm kiểm tra này, loại bỏ nguy cơ đọc tệp trái phép qua các chuỗi `../` hoặc `..\`.

### 11.2. Chống tấn công Command Injection trong FFmpeg
- Dịch vụ `video/ffmpeg_service.py` thực thi subprocess hoàn toàn thông qua mảng đối số (list arguments):
  ```python
  subprocess.Popen(cmd_list, shell=False, ...)
  ```
  Tuyệt đối không sử dụng `shell=True` và không nối chuỗi tham số shell, ngăn chặn 100% nguy cơ chèn mã độc thông qua tên tệp tin đầu vào.

### 11.3. Chống tấn công CSV Injection (Formula Injection)
- Module `report_generator.py` (L245–L260) thực hiện lọc dữ liệu trước khi ghi ra tệp CSV: các chuỗi ký tự bắt đầu bằng `=`, `+`, `-`, `@`, `\t`, `\r` đều được tự động chèn thêm dấu nháy đơn `'` ở đầu để vô hiệu hóa việc thực thi công thức tự động trong Microsoft Excel / LibreOffice.

---

## 12. KIỂM TOÁN RÒ RỈ TÀI NGUYÊN & VÒNG ĐỜI BỘ NHỚ (RESOURCE LEAK AUDIT)

### 12.1. Quản lý Bộ nhớ Đồ họa GPU (VRAM)
- Model `RealESRGAN_x4plus` được giữ cố định trên VRAM (~1.2GB) sau khi nạp, tuân thủ đúng yêu cầu kiến trúc Persistent Worker.
- Sau mỗi tác vụ nâng cấp ảnh hoặc khung hình video, hệ thống thực thi `torch.cuda.empty_cache()` để giải phóng toàn bộ tensor trung gian không sử dụng.
- Dữ liệu rà soát không phát hiện hiện tượng tích tụ tensor (tensor leak) trong các biến toàn cục.

### 12.2. Vòng đời Kết nối SSE (Server-Sent Events)
- Cơ chế phát sự kiện thời gian thực trong `app.py` sử dụng hàng đợi `queue.Queue` cho từng client kết nối.
- Khi kết nối mạng bị ngắt (client đóng trình duyệt), ngoại lệ `GeneratorExit` được bắt an toàn và client tương ứng lập tức được loại bỏ khỏi danh sách `_sse_listeners`, tránh rò rỉ luồng hoặc tích tụ bộ nhớ đệm sự kiện.

### 12.3. Dọn dẹp Tệp tin Tạm (Workspace Cleanup)
- Quy trình dọn dẹp không gian làm việc của video (`cleanup_workspace` trong `video_pipeline.py`) tự động xóa thư mục khung hình thô `extracted/` sau khi hoàn tất dựng video, giúp tiết kiệm dung lượng đĩa cứng.

---

## 13. KIỂM TOÁN PHỤ THUỘC & MÔI TRƯỜNG THỰC THI (DEPENDENCY AUDIT)

### 13.1. Phiên bản Phần mềm Cốt lõi
- **Python:** 3.10.21 (Chuẩn 64-bit Windows).
- **PyTorch:** 2.6.0+cu126 (Tương thích CUDA 12.6, tận dụng tối đa nhân Tensor Cores trên RTX 2050).
- **Torchvision:** 0.21.0+cu126.
- **FFmpeg:** 7.1-full_build (Hỗ trợ đầy đủ bộ giải mã H.264, AAC, MP3, Opus).

### 13.2. Tính di động của Đường dẫn (Path Portability)
- Toàn bộ các module sử dụng `BASE_DIR = Path(__file__).resolve().parent` để xây dựng đường dẫn tương đối.
- Tệp kịch bản `Start_ImageStudio.bat` sử dụng biến môi trường `%~dp0`, đảm bảo hệ thống có thể di chuyển sang bất kỳ ổ đĩa hoặc thư mục nào mà không cần cấu hình lại đường dẫn.

---

## 14. KẾT QUẢ PHÂN TÍCH TĨNH (STATIC ANALYSIS RESULTS)

- Không phát hiện bất kỳ cú pháp lỗi (`SyntaxError`) hoặc tên biến không được định nghĩa (`NameError`).
- Các khối lệnh gọi thư viện ngoài (`cv2`, `torch`, `PIL`, `piexif`) đều được bọc trong các khối `try...except` với xử lý ngoại lệ phòng ngừa.
- Không tồn tại cảnh báo nghiêm trọng về cấu trúc mã nguồn ngoài các điểm đã được lập chỉ mục trong bảng phát hiện.

---

## 15. TỔNG HỢP BẰNG CHỨNG KIỂM THỬ (TEST EVIDENCE SUMMARY)

Đối chiếu dữ liệu kiểm thử thực tế từ các báo cáo Phase 5.1 và Phase 5.2 xác nhận:
- **Phase 5 Video Tests:** 76/76 tests ĐẠT 100% (Bao gồm các nhóm: Metadata probe, Frame extraction, Frame processing, Reconstruction, Audio merge, Pause/Resume, Crash recovery).
- **Hồi quy hệ thống (Phase 1–4):** 115/115 tests ĐẠT 100%.
- **Tổng số test case đã kiểm chứng:** 191/191 tests (100% PASS).

---

## 16. BẢNG CHI TIẾT CÁC PHÁT HIỆN KIỂM TOÁN (FINDINGS REGISTER)

Dưới đây là bảng phân loại toàn bộ 5 phát hiện kiểm toán được trích xuất trực tiếp vào tệp `docs/phase-5.3-code-audit-findings.csv`:

| ID | Mức độ | Module | Tệp tin | Vị trí dòng | Tóm tắt mô tả |
| :---: | :---: | :--- | :--- | :---: | :--- |
| **AUDIT53-01** | **MEDIUM** | Video Frame Processor | `video/frame_processor.py` | 125, 165 | Worker state bị kẹt ở `STATE_BUSY` sau khi hoàn thành/hủy tác vụ video do thiếu lệnh reset về `STATE_READY`. |
| **AUDIT53-02** | **HIGH** | Video Controller & Pipeline | `app.py` / `video_pipeline.py` | 2240–2246; 95, 128 | Thiếu cơ chế kiểm tra chặn luồng video chạy đồng thời khi bấm Start/Resume nhiều lần hoặc từ nhiều tab. |
| **AUDIT53-03** | **MEDIUM** | Video Pipeline | `video/video_pipeline.py` | 217, 361–366 | Hủy video trong lúc FFmpeg đang chạy bị bắt bởi ngoại lệ chung và cập nhật thành `FAILED` thay vì `CANCELLED`. |
| **AUDIT53-04** | **LOW** | Database Connection | `job_manager.py` / `video_job_manager.py` | 91; 80 | Kết nối SQLite trong các hàm truy vấn phụ thuộc hoàn toàn vào GC của CPython để đóng file handle. |
| **AUDIT53-05** | **INFO** | Batch Export API | `app.py` | 1853–1863, 1893–1905 | Trùng lặp mã nguồn bóc tách tham số `job_ids` giữa phương thức GET và POST tại hai endpoint xuất báo cáo. |

---

## 17. MA TRẬN RỦI RO & PHÂN TÍCH TÁC ĐỘNG (RISK MATRIX)

```
        +-------------------------------------------------------+
        |                 MA TRẬN RỦI RO HỆ THỐNG               |
        +-------------------------------------------------------+
        | TÁC ĐỘNG \ XÁC SUẤT  |   THẤP   | TRUNG BÌNH |  CAO   |
        +----------------------+----------+------------+--------+
        | NGHIÊM TRỌNG (HIGH)  |          | AUDIT53-02 |        |
        | TRUNG BÌNH (MEDIUM)  |          | AUDIT53-01 |        |
        |                      |          | AUDIT53-03 |        |
        | THẤP (LOW / INFO)    | AUDIT53-05| AUDIT53-04 |       |
        +----------------------+----------+------------+--------+
```

### Đánh giá tác động rủi ro:
1. **Rủi ro vận hành GPU:** **KHÔNG CÓ (NONE)**. Khóa `worker._lock` đảm bảo GPU không bao giờ bị quá tải hoặc xung đột luồng tensor.
2. **Rủi ro toàn vẹn dữ liệu:** **CỰC THẤP (VERY LOW)**. WAL mode và atomic commits bảo vệ an toàn dữ liệu lịch sử và trạng thái hàng đợi.
3. **Rủi ro trải nghiệm người dùng (UX):** **TRUNG BÌNH (MEDIUM)**. Người dùng có thể gặp hiện tượng không tạo được Quick Preview sau khi chạy video nếu chưa sửa `AUDIT53-01`, hoặc thấy thông báo Failed khi ấn Cancel nếu chưa sửa `AUDIT53-03`.

---

## 18. KẾ HOẠCH KHẮC PHỤC KHUYẾN NGHỊ (RECOMMENDED FIX PRIORITY)

| Thứ tự | Mã phát hiện | Giải pháp kỹ thuật khuyến nghị | Mức độ phức tạp |
| :---: | :---: | :--- | :---: |
| **1** | **AUDIT53-01** | Bổ sung `try...finally: self.worker.state = STATE_READY` trong `video/frame_processor.py` để đảm bảo trạng thái luôn được hoàn trả. | Rất thấp (3 dòng code) |
| **2** | **AUDIT53-02** | Thêm kiểm tra `is_any_job_active()` trong `app.py` và cập nhật trạng thái nguyên tử trong `video_jobs` trước khi kích hoạt `Thread`. | Thấp (10 dòng code) |
| **3** | **AUDIT53-03** | Thêm điều kiện kiểm tra `cancel_event.is_set()` trong khối ngoại lệ tại `video/video_pipeline.py` L361 để gán `STATUS_CANCELLED`. | Thấp (5 dòng code) |
| **4** | **AUDIT53-04** | Bọc kết nối SQLite bằng `contextlib.closing` hoặc `try...finally: conn.close()`. | Thấp |
| **5** | **AUDIT53-05** | Tách hàm trợ giúp `_extract_job_ids(request)` trong `app.py`. | Tùy chọn (Refactor) |

---

## 19. ĐÁNH GIÁ ĐỘ ỔN ĐỊNH THỜI GIAN THỰC (OPERATIONAL STABILITY ASSESSMENT)

- **Khả năng chịu lỗi bộ nhớ (OOM Resilience):** Hệ thống có khả năng tự phục hồi xuất sắc nhờ cơ chế hạ cấp kích thước Tile tự động theo 6 cấp độ (512 $\to$ 384 $\to$ 256 $\to$ 192 $\to$ 128 $\to$ 64) kết hợp dọn cache GPU tức thì.
- **Khả năng duy trì liên tục (Continuity & Checkpointing):** Điểm kiểm tra (checkpoint) theo từng khung hình giúp video pipeline có thể tạm dừng và tiếp tục mượt mà mà không phải xử lý lại từ đầu.
- **Khả năng phục hồi sự cố (Crash Recovery):** Trạng thái bất thường khi khởi động đều được giải quyết tự động, bảo toàn trạng thái công việc và dữ liệu của người dùng.

---

## 20. KẾT LUẬN VÀ QUYẾT ĐỊNH KIỂM TOÁN CUỐI CÙNG (FINAL AUDIT DECISION)

### QUYẾT ĐỊNH CHÍNH THỨC: **CONDITIONAL PASS (ĐẠT CÓ ĐIỀU KIỆN)**

Hệ thống **AI Image Studio** đạt tiêu chuẩn ổn định kỹ thuật cao cho toàn bộ các chức năng từ Phase 1 đến Phase 5. Không tồn tại lỗi nghiêm trọng (Critical Flaws) hoặc nguy cơ gây hỏng hóc phần cứng GPU / hỏng dữ liệu.

**Điều kiện nghiệm thu hoàn toàn:**
- Áp dụng 3 bản vá kỹ thuật mức độ High/Medium (`AUDIT53-01`, `AUDIT53-02`, `AUDIT53-03`) trong đợt bảo trì tiếp theo.
- Tuân thủ nguyên tắc bàn giao: Báo cáo kiểm toán Phase 5.3 hoàn thành độc lập, toàn bộ mã nguồn được giữ nguyên trạng thái Read-Only, không tự ý can thiệp code trước khi có phê duyệt.
