# BÁO CÁO ĐIỀU TRA SỰ CỐ KỸ THUẬT: IMAGE SCAN & ORPHANED JOBS
**Dự án:** AI Image Studio — Real-ESRGAN v0.3.0  
**Thư mục gốc:** `E:\AI Image Studio\RealESRGAN-v030`  
**Nhánh Git:** `clean-main`  
**Thời điểm điều tra:** 10/11/2026  
**Chế độ thực hiện:** STRICTLY READ-ONLY AUDIT & ROOT CAUSE ANALYSIS  

---

## 1. TỔNG QUAN SỰ CỐ & DẤU HIỆU LÂM SÀNG (EXECUTIVE SUMMARY)

Vào sáng ngày 10/11/2026, sau khi khởi động lại ứng dụng `RealESRGAN-v030` từ launcher `Start_ImageStudio.bat`, hệ thống ghi nhận một loạt hiện tượng bất thường:

1. **Startup Recovery tự động nạp 84 jobs vào hàng đợi (Queue Restoration):** Khi Flask khởi động xong các bước kiểm tra, Phase 3 Crash Recovery phát hiện 84 jobs đủ điều kiện (eligible jobs) và tự động đẩy toàn bộ vào `processing_queue`. AI Worker ngay lập tức chạy qua 84 jobs này và chuyển trạng thái toàn bộ sang `FAILED`.
2. **Nhiều job tham chiếu file không tồn tại (82/106 jobs):** Trong cơ sở dữ liệu `data/jobs.db`, có tới 82 jobs tham chiếu đến các đường dẫn file không còn tồn tại trên ổ đĩa.
3. **Folder Scanner từ chối ảnh có độ phân giải lớn hơn 25 triệu pixel:** Các ảnh thực tế như `4244x6400` (27.16M pixels) và `11520x9712` (111.88M pixels) bị từ chối với thông báo `Image exceeds maximum pixel limit: ... (max allowed: 25,000,000)`.
4. **Folder Scanner báo "Loaded 0 valid images":** Khi người dùng mở ứng dụng hoặc trỏ về thư mục mặc định `uploads/`, hệ thống ghi nhận thông điệp `Loaded 0 valid images from E:\AI Image Studio\RealESRGAN-v030\uploads`.
5. **AI Worker và GPU CUDA vẫn hoàn toàn bình thường:** Model `RealESRGAN_x4plus.pth` tải thành công vào VRAM, GPU NVIDIA GeForce RTX 2050 (4GB) báo trạng thái `READY` ở chế độ FP16 (Half Precision).

Báo cáo này làm rõ toàn bộ chuỗi nguyên nhân gốc rễ (Root Cause Analysis - RCA) dựa trên bằng chứng dữ liệu, log thực tế và đối chiếu mã nguồn từng dòng lệnh.

---

## 2. GATE 0 — TUÂN THỦ NGUYÊN TẮC BẢO VỆ DỮ LIỆU & READ-ONLY

Toàn bộ quá trình điều tra được thực hiện dưới sự kiểm soát an toàn nghiêm ngặt:
- **Zero Source Modifications:** Không chỉnh sửa bất kỳ dòng mã nguồn nào (`app.py`, `job_manager.py`, `ai_worker.py`, frontend HTML/JS).
- **Zero Config Modifications:** Giữ nguyên `config.json`.
- **Zero Database Writes:** Toàn bộ truy vấn SQLite tới `data/jobs.db` được mở ở chế độ `file:...mode=ro` (Read-Only URI). Không thực hiện INSERT, UPDATE, DELETE hay chạy Migration.
- **Zero Destructive Actions:** Không xóa, không đổi tên, không di chuyển file người dùng hay file cache.
- **Zero Inference Execution:** Không khởi động worker thực thi suy luận AI trên GPU để tránh xung đột tài nguyên.

---

## 3. GATE 1 — KIỂM TOÁN DỮ LIỆU SQLITE JOBS (`data/jobs.db`)

### 3.1. Phân bổ trạng thái tổng thể
Tổng số bản ghi trong bảng `jobs`: **106 jobs**
- `STATUS_FAILED`: **102 jobs** (chiếm 96.2%)
- `STATUS_COMPLETED`: **4 jobs** (chiếm 3.8%)
- `STATUS_QUEUED`: **0 jobs** (tại thời điểm kết thúc lượt xử lý của worker)
- `STATUS_PROCESSING`: **0 jobs**
- `STATUS_RETRY_WAIT`: **0 jobs**
- `STATUS_CANCELLED`: **0 jobs**

### 3.2. Kiểm tra sự tồn tại của file nguồn trên ổ cứng (`input_path`)
Kiểm tra thực tế từng đường dẫn `input_path` của 106 jobs:
- **File nguồn TỒN TẠI trên đĩa:** **24 jobs** (22.6%)
- **File nguồn KHÔNG TỒN TẠI trên đĩa (Missing):** **82 jobs** (77.4%)

### 3.3. Phân loại 82 jobs có file nguồn bị mất
| Mẫu đường dẫn thư mục gốc | Số lượng | Trạng thái | Thông báo lỗi ghi nhận |
| :--- | :---: | :---: | :--- |
| `E:\AI Image Studio\RealESRGAN-v030\uploads\` | 51 | 47 FAILED, 4 COMPLETED | `Invalid image: File does not exist or is a directory` (47 jobs), `None` (4 jobs COMPLETED cũ) |
| `C:\Users\Asus\AppData\Local\Temp\...` | 12 | 12 FAILED | `Invalid image: File does not exist or is a directory` (10 jobs), `Image file does not exist: ...` (2 jobs) |
| Các thư mục test khác (`test_other`) | 19 | 19 FAILED | `Invalid image: File does not exist or is a directory` (18 jobs), `Corrupted or unrecognized image format` (1 job) |
| **Tổng cộng** | **82** | **78 FAILED, 4 COMPLETED** | |

### 3.4. Phân tích 24 jobs có file nguồn tồn tại nhưng FAILED
Cả 24 jobs này đều là các file ảnh thực tế nằm trong các thư mục gốc của người dùng bên ngoài dự án:
1. **Thư mục `E:\WEB\3Dimage\!!!\originals\i1\` (8 jobs, tạo lúc 21:51:27 - 21:51:31 ngày 09/11/2026):**
   - Kích thước ảnh: `11520 x 9712` (tổng cộng **111,882,240 pixels** ~ 111.9 Megapixels).
   - Lỗi: `Invalid image: Image exceeds maximum pixel limit: 11520x9712 (111,882,240 pixels, max allowed: 25,000,000)`.
2. **Thư mục `E:\WEB\3Dimage\!!!\originals\i30\` (16 jobs, tạo lúc 22:39:25 - 22:39:30 ngày 09/11/2026):**
   - Kích thước ảnh: `4244 x 6400` (tổng cộng **27,161,600 pixels** ~ 27.16 Megapixels).
   - Lỗi: `Invalid image: Image exceeds maximum pixel limit: 4244x6400 (27,161,600 pixels, max allowed: 25,000,000)`.

---

## 4. GATE 2 — TƯƠNG QUAN HỆ THỐNG FILE & CÁC LẦN CLEANUP TRƯỚC (CORRELATION AUDIT)

### 4.1. Thực trạng các thư mục runtime
- `uploads/`: **0 files** (hoàn toàn rỗng).
- `output/`: **0 files**.
- `thumbnails/`: **49 files** (kích thước nhỏ từ 2 KB đến 8 KB).
- `logs/`: **4 files** (`app.log` 722 KB, `history.json` 166 KB, `history.bak` 165 KB, `startup.log` 399 B).

### 4.2. Nguyên nhân phát sinh 82 "Orphaned Jobs" trong SQLite
1. **Hoạt động dọn dẹp thư mục `uploads/` trong đợt Cleanup trước (đêm 09/11 - rạng sáng 10/11):**
   - Trong quá trình dọn dẹp hệ thống trước đó, toàn bộ ảnh tạm trong thư mục `uploads/` (bao gồm các file test synthetic tạo trong Phase 1 đến 5.4) đã được xóa hoặc di chuyển vào kho lưu trữ sao lưu `_backup/RealESRGAN-v030_before_cleanup/`.
   - Tuy nhiên, script dọn dẹp chỉ can thiệp vào hệ thống tập tin (File System) mà **không thực hiện đồng bộ hoặc dọn dẹp các dòng tương ứng trong bảng `jobs` của SQLite (`data/jobs.db`)**.
   - Do đó, 51 bản ghi job trong SQLite vẫn giữ nguyên đường dẫn `uploads/<file_name>`, trở thành các liên kết mồ côi (dangling pointers).
2. **Các bài kiểm thử tự động (Test Suites) ghi trực tiếp vào `data/jobs.db`:**
   - Một số test suite (`test_folder_browser_selection.py`, `test_phase3_reliability.py`, v.v.) khi khởi chạy đã nạp các job trỏ vào thư mục tạm `C:\Users\Asus\AppData\Local\Temp\...`.
   - Khi bài test kết thúc (Teardown fixture), thư mục tạm của hệ điều hành bị xóa, nhưng các job row được insert vào database thực vẫn tồn tại.

---

## 5. GATE 3 — ĐIỀU TRA CHUYÊN SÂU CƠ CHẾ STARTUP RECOVERY (ROOT CAUSE ANALYSIS)

Dựa vào việc đối chiếu trực tiếp nhật ký `logs/app.log` lúc `11:40:22 - 11:40:27` và mã nguồn trong `job_manager.py`, `app.py`:

```log
2026-11-10 11:40:22 [INFO] Running startup checks...
2026-11-10 11:40:22 [INFO] Model OK: E:\AI Image Studio\RealESRGAN-v030\weights\RealESRGAN_x4plus.pth
2026-11-10 11:40:22 [INFO] CUDA: True, GPU: NVIDIA GeForce RTX 2050
2026-11-10 11:40:23 [INFO] RealESRGANer successfully loaded in memory. CUDA: True, Half: True, Device: cuda
2026-11-10 11:40:23 [INFO] Persistent AI Worker initialized: status=READY
2026-11-10 11:40:23 [INFO] Executing Phase 3 Startup Job Recovery...
2026-11-10 11:40:23 [INFO] Recovery: Job c4581151-853c-4937-8024-edb3eb88ba88 interrupted mid-process. Re-queueing for retry.
2026-11-10 11:40:23 [INFO] Startup Recovery completed: 1 jobs processed, 84 queued jobs eligible.
2026-11-10 11:40:23 [INFO] Phase 3 Crash Recovery completed: {'recovered_count': 1, 'details': [{'id': 'c4581151-853c-4937-8024-edb3eb88ba88', 'action': 'requeued'}], 'eligible_job_ids': [... 84 IDs ...]}
2026-11-10 11:40:24 [INFO] Startup Queue Restoration: Enqueueing 84 recovered pending jobs.
```

### 5.1. Cơ chế phát hiện 84 jobs trong `recover_on_startup()` (`job_manager.py`)
Tại [job_manager.py](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/job_manager.py#L706-L840):
1. **Dòng 720–780:** Hệ thống quét các job bị gián đoạn ở trạng thái `PROCESSING` hoặc `VALIDATING`. Phát hiện 1 job (`c4581151-853c-4937-8024-edb3eb88ba88`), không có output hợp lệ trên đĩa, số lần thử lại `retry_count < max_retries`, nên phục hồi chuyển về `STATUS_QUEUED`.
2. **Dòng 827–832:**
   ```python
   # 4. Collect all eligible QUEUED jobs in deterministic order
   cur_queued = conn.execute(
       "SELECT id FROM jobs WHERE status = ? ORDER BY created_at ASC",
       (STATUS_QUEUED,)
   )
   eligible_job_ids = [r["id"] for r in cur_queued.fetchall()]
   ```
   **Lỗ hổng thiết kế tại đây:** Truy vấn này lấy **toàn bộ các job có trạng thái `STATUS_QUEUED`** bất kể chúng được tạo từ bao giờ và **hoàn toàn không kiểm tra xem file nguồn `input_path` có tồn tại trên đĩa hay không**.
   Lúc này trong SQLite có sẵn 83 jobs ở trạng thái `STATUS_QUEUED` tồn đọng từ các phiên trước, cộng thêm 1 job vừa được requeue, tạo thành danh sách đúng **84 eligible job IDs**.

### 5.2. Cơ chế tự động nạp queue trong `run_startup_checks()` (`app.py`)
Tại [app.py](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/app.py#L795-L812):
```python
# Phase 3 Queue Restoration: Restore eligible QUEUED jobs into processing_queue exactly once
global _startup_queue_restored
if not _startup_queue_restored:
    eligible_ids = rec_report.get("eligible_job_ids", [])
    with jobs_lock:
        pending_ids = [jid for jid in eligible_ids if jid in jobs and jobs[jid]['status'] == 'pending']
    if pending_ids:
        logger.info(f"Startup Queue Restoration: Enqueueing {len(pending_ids)} recovered pending jobs.")
        processing_queue.put({
            'job_ids': pending_ids,
            ...
        })
    _startup_queue_restored = True
```
Do mục tiêu ban đầu của tính năng Crash Recovery là không làm mất tác vụ khi ứng dụng bị tắt đột ngột, toàn bộ 84 jobs này được tự động đưa vào hàng đợi `processing_queue` để worker xử lý.

### 5.3. Tại sao Worker xử lý thất bại mà không bị lặp vô tận (Infinite Retry Loop)?
Worker `processing_worker` lấy batch gồm 84 job IDs và gọi `process_single_image(job)` cho từng job:
Tại [app.py](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/app.py#L903-L908):
```python
is_valid_img, val_reason, (w, h), _ = validate_image_file(valid_in)
if not is_valid_img:
    error_msg = f"Invalid image: {val_reason}"
    logger.error(f"Image verification failed for {valid_in.name}: {val_reason}")
    job_logger.log_error(job_id, error_msg)
    job_mgr.schedule_retry(job_id, error_msg)  # Non-retryable -> FAILED
```
- **Đối với 60 jobs mất file nguồn:** `validate_image_file()` trả về `is_valid=False`, lý do: `"File does not exist or is a directory"`.
- **Đối với 24 jobs độ phân giải lớn:** `validate_image_file()` trả về `is_valid=False`, lý do: `"Image exceeds maximum pixel limit: ... (max allowed: 25,000,000)"`.
- Hàm `job_mgr.schedule_retry()` kiểm tra xem lỗi này có phải là lỗi tạm thời (transient) hay không (như CUDA OOM). Do đây là lỗi xác thực dữ liệu đầu vào (Invalid image), nó được xếp vào loại **Non-retryable Error**, hệ thống lập tức đánh dấu job là `STATUS_FAILED` mà không thử lại, giải phóng hàng đợi an toàn trong vòng 5 giây (từ `11:40:23` đến `11:40:27`).

---

## 6. GATE 4 — PHÂN TÍCH KIẾN TRÚC GIỚI HẠN PIXEL & NĂNG LỰC VRAM GPU

### 6.1. Xung đột giữa các cấu hình giới hạn Pixel
Trong tệp cấu hình [config.json](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/config.json):
```json
"security": {
  "max_pixels": 25000000,
  "upload_max_pixels": 150000000,
  "pillow_max_image_pixels": 250000000
}
```
Trong [app.py](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/app.py):
- **Dòng 143:** `MAX_PIXELS = int(APP_CONFIG["security"].get("max_pixels", 25000000))` (25 Megapixels).
- **Dòng 144:** `UPLOAD_MAX_PIXELS = int(APP_CONFIG["security"].get("upload_max_pixels", 150000000))` (150 Megapixels).
- **Dòng 555:** `def validate_image_file(file_path, max_pixels=None):`
  - **Dòng 612:** `limit_pixels = max_pixels if max_pixels is not None else MAX_PIXELS`
- **Dòng 1458 (API Upload):** `validate_image_file(dest_path, max_pixels=UPLOAD_MAX_PIXELS)` -> Sử dụng ngưỡng **150M pixels** (cho phép tải lên ảnh độ phân giải cao).
- **Dòng 1580 (API Select Folder):** `validate_image_file(file_path, max_pixels=UPLOAD_MAX_PIXELS)` -> Sử dụng ngưỡng **150M pixels** (cho phép nạp ảnh từ thư mục vào giao diện).
- **DÒNG 903 (Processing Worker trong `process_single_image`):**
  ```python
  is_valid_img, val_reason, (w, h), _ = validate_image_file(valid_in)
  ```
  **ĐIỂM GẪY KIẾN TRÚC (ARCHITECTURAL DISCREPANCY):** Dòng 903 gọi `validate_image_file` nhưng **KHÔNG truyền tham số `max_pixels`**, dẫn đến việc hàm tự động lấy giá trị mặc định là `MAX_PIXELS = 25,000,000`!

**Hậu quả:** 
Khi người dùng tải ảnh hoặc chọn thư mục chứa ảnh lớn (ví dụ `27.16M px` hoặc `111.88M px`), giao diện Web và các endpoint tiếp nhận (`/api/upload`, `/api/select-folder`) đều thông báo chấp nhận hợp lệ (vì < 150M px). Nhưng ngay khi người dùng nhấn bắt đầu xử lý, worker kiểm tra lại ở bước suy luận thì ngay lập tức từ chối và đánh dấu `FAILED` vì vượt quá 25M px!

### 6.2. Đánh giá năng lực VRAM của GPU NVIDIA GeForce RTX 2050 (4GB)
- **Cấu hình phần cứng:** NVIDIA GeForce RTX 2050 Laptop GPU (4GB GDDR6 VRAM, PyTorch 2.6.0+cu126, CUDA 12.6/12.7).
- **Dung lượng mô hình trong VRAM:** Trọng số `RealESRGAN_x4plus.pth` ở chế độ FP16 chỉ chiếm khoảng **~64 MB VRAM**.
- **Cơ chế Auto-Tiling của RealESRGANer:**
  - Mô hình Real-ESRGAN không xử lý toàn bộ ma trận ảnh cùng một lúc trên VRAM. Toàn bộ ảnh kích thước lớn được lưu trên bộ nhớ RAM chính của máy tính.
  - Ảnh được cắt nhỏ thành từng ô vuông (tiles) có kích thước `tile_size` (mặc định là `128x128`, có đệm viền `tile_pad=10`).
  - Mỗi tile `128x128` chỉ chiếm vài Megabyte VRAM trong quá trình forward pass.
  - Khi hoàn thành từng tile, kết quả được ghép nối lại trên RAM chính.
  - Nếu gặp áp lực bộ nhớ, cơ chế OOM Fallback tự động hạ tile xuống `64` hoặc `32`.
- **Phân tích đối với 2 mẫu ảnh thực tế:**
  1. `4244 x 6400` (**27.16M pixels**): Dung lượng mảng RGB trên RAM chính ~81 MB. Xử lý chia thành các tile 128x128 hoàn toàn chạy mượt mà trên 4GB VRAM.
  2. `11520 x 9712` (**111.88M pixels**): Dung lượng mảng RGB trên RAM chính ~335 MB. Xử lý bằng auto-tiling trên RTX 2050 4GB hoàn toàn khả thi và nằm trong ngưỡng an toàn của hệ số `UPLOAD_MAX_PIXELS: 150,000,000`.
- **Kết luận:** Giới hạn 25M pixels tại `MAX_PIXELS` là một hằng số cũ còn sót lại từ các phiên bản sơ khai, hoàn toàn không phản ánh đúng năng lực xử lý thực tế của engine Real-ESRGAN Auto-Tiling trên RTX 2050 4GB.

---

## 7. GATE 5 — QUY TRÌNH FOLDER SCANNER & GIAO DIỆN NGƯỜI DÙNG (FRONTEND FLOW)

### 7.1. Tại sao Folder Scanner thông báo "Loaded 0 valid images"?
1. **Trường hợp thư mục rỗng (`uploads/`):**
   - Khi người dùng tải lại trang hoặc bấm `Reset Folder (uploads)`, hàm `loadImagesFromFolder('', true)` gọi `POST /api/select-folder` với `path: ""`.
   - Tại dòng 1555 của `app.py`, đường dẫn mặc định là `UPLOAD_DIR`.
   - Vì thư mục `uploads/` đã được dọn sạch trước đó (0 files), vòng lặp duyệt file không thu được kết quả nào (`results = []`).
   - Dòng 1716 log ra: `Loaded 0 valid images from E:\AI Image Studio\RealESRGAN-v030\uploads`.
   - Giao diện người dùng nhận được `{files: [], total_valid: 0, count: 0}`, hiển thị cảnh báo `folderEmptyAlert` ("Selected folder is empty or has no supported images") và thông báo cảnh báo toast `folder_empty_warning`.
2. **Trường hợp thư mục chứa ảnh nhưng toàn bộ ảnh vượt quá 25M pixel (nếu scanner dùng MAX_PIXELS):**
   - Mặc dù dòng 1580 của `api_select_folder` hiện tại đã dùng `UPLOAD_MAX_PIXELS`, nhưng nếu trước đó người dùng thực hiện quét bằng một phương thức validate mặc định hoặc ảnh bị lỗi, mọi file bị bỏ qua bởi `if not is_valid: continue` mà không được thêm vào mảng `results`.
   - Khi đó `len(results) == 0`, hệ thống cũng hiển thị "Loaded 0 valid images".

### 7.2. Tác động trải nghiệm người dùng (UX Impact)
- Người dùng thấy danh sách hàng đợi bị lấp đầy bởi 84 tác vụ màu đỏ (FAILED) ngay khi vừa mở ứng dụng.
- Người dùng không thể xử lý ảnh độ phân giải cao thông qua nút Bắt đầu xử lý dù ảnh đã hiển thị hợp lệ trên danh sách.
- Gây hiểu nhầm rằng hệ thống hoặc GPU bị lỗi, trong khi thực tế GPU và AI Worker đang ở trạng thái hoàn hảo.

---

## 8. PHÂN LOẠI MỨC ĐỘ NGHIÊM TRỌNG (SEVERITY CLASSIFICATION)

| Hạng mục | Mức độ | Diễn giải chi tiết |
| :--- | :---: | :--- |
| **Xung đột giới hạn Pixel (Discrepancy in `process_single_image`)** | **HIGH** | `validate_image_file` trong worker thiếu tham số `max_pixels`, kích hoạt mức chặn mặc định 25M px. Khiến mọi ảnh từ 25M đến 150M px bị từ chối suy luận dù đã vượt qua khâu tải lên và nạp thư mục. |
| **Recovery nạp Job không kiểm tra sự tồn tại của file nguồn** | **HIGH** | `recover_on_startup()` truy vấn mọi `STATUS_QUEUED` mà không kiểm tra `input_path.is_file()`, dẫn đến việc tự động nạp hàng chục job mồ côi (orphaned jobs) vào queue khi khởi động, làm bẩn lịch sử và gây hoang mang cho người dùng. |
| **Tồn đọng bản ghi rác trong SQLite sau khi dọn dẹp file** | **MEDIUM** | Các thao tác dọn dẹp filesystem không đi kèm đồng bộ hóa database, để lại 82 liên kết mồ côi trong `jobs.db`. |
| **Thông báo Folder Scanner "Loaded 0 valid images" khi thư mục rỗng** | **LOW** | Đây là hành vi phản hồi đúng của ứng dụng khi thư mục `uploads/` không có file, nhưng cần cải thiện thông điệp hướng dẫn người dùng kéo thả file thay vì hiển thị cảnh báo tiêu cực. |
| **Trạng thái AI Worker & GPU CUDA** | **HEALTHY** | Không có lỗi. Khởi tạo Persistent RealESRGANer thành công, CUDA FP16 hoạt động bình thường trên RTX 2050. |

---

## 9. ĐỀ XUẤT PHƯƠNG ÁN KHẮC PHỤC (REMEDIATION PLAN)

> [!IMPORTANT]
> Đây là báo cáo điều tra READ-ONLY. Tuyệt đối KHÔNG tự ý sửa đổi mã nguồn hoặc database trong phiên điều tra này. Dưới đây là kế hoạch kỹ thuật chi tiết để người dùng phê duyệt trước khi chuyển sang giai đoạn thực thi:

### Gói 1: Đồng bộ hóa ngưỡng xác thực Pixel trên toàn bộ hệ thống
1. **Cập nhật `process_single_image` trong `app.py`:**
   Tại dòng 903:
   ```python
   # Hiện tại:
   is_valid_img, val_reason, (w, h), _ = validate_image_file(valid_in)
   
   # Khắc phục đề xuất:
   is_valid_img, val_reason, (w, h), _ = validate_image_file(valid_in, max_pixels=UPLOAD_MAX_PIXELS)
   ```
2. **Cập nhật giá trị mặc định của `validate_image_file`:**
   Đảm bảo nếu `max_pixels is None`, hàm sẽ sử dụng `UPLOAD_MAX_PIXELS` (150,000,000) làm mặc định thay vì 25,000,000, hoặc cập nhật `"max_pixels": 150000000` đồng bộ trong `config.json`.

### Gói 2: Tinh chỉnh cơ chế Startup Recovery (`job_manager.py`)
1. **Kiểm tra sự tồn tại của file nguồn trước khi xếp vào `eligible_job_ids`:**
   Tại dòng 828 của `job_manager.py`:
   - Trước khi trả về danh sách `eligible_job_ids`, kiểm tra từng job: nếu `input_path` không tồn tại trên ổ cứng hoặc kích thước bằng 0, tự động chuyển job đó sang `STATUS_FAILED` với lý do rõ ràng: `"Source file no longer exists on disk"` và **KHÔNG** đưa vào danh sách nạp queue.
2. **Tùy chọn cấu hình tự động dọn dẹp Orphaned Jobs:**
   Thêm một tác vụ hoặc flag khi bảo trì để dọn các job mồ côi (trỏ đến `Temp` hoặc `uploads` đã bị xóa) ra khỏi hàng đợi.

### Gói 3: Script bảo trì & đồng bộ dữ liệu SQLite an toàn
1. Viết script bảo trì (Data Reconciliation Script) chạy 1 lần có tạo backup:
   - Đánh dấu trạng thái `CANCELLED` hoặc lưu trữ (archive) 82 job mồ côi hiện tại trong `data/jobs.db`.
   - Giữ nguyên toàn bộ schema và không làm ảnh hưởng đến các job hợp lệ.

---

## 10. KẾ HOẠCH BÀI TEST HỒI QUY (REGRESSION TEST SUITE)

Sau khi được phê duyệt triển khai sửa lỗi, cần chạy các test case sau để nghiệm thu:
1. **Test High-Res Image Pipeline:** Thử nghiệm đưa ảnh kích thước `4244x6400` (27.16M px) và `11520x9712` (111.88M px) qua toàn bộ vòng đời: Tải lên / Quét thư mục -> Nạp hàng đợi -> Xác thực trước xử lý -> Suy luận AI với Auto-Tiling -> Xuất file thành công.
2. **Test Startup Recovery với Missing File:** Tạo 1 job giả lập trỏ vào file không tồn tại, khởi động lại server, xác nhận hệ thống đánh dấu job `FAILED` ngay tại bước recovery mà không tự động đẩy vào `processing_queue`.
3. **Test Empty Folder Scanning:** Quét thư mục rỗng và kiểm tra thông báo phản hồi thân thiện trên UI.
4. **Test Toàn diện:** Chạy toàn bộ 249 unit/integration tests hiện có để đảm bảo tỷ lệ pass đạt 100%.

---
*Báo cáo được lập tự động bởi Hệ thống Phân tích Kỹ thuật Antigravity — Tuân thủ tuyệt đối quy tắc Read-Only Audit.*
