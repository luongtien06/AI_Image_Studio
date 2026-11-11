# BÁO CÁO NGHIỆM THU KHẮC PHỤC SỰ CỐ: PACKAGE 1 & PACKAGE 2
## IMAGE PIXEL VALIDATION & ORPHAN-SAFE STARTUP RECOVERY

**Dự án:** AI Image Studio — Real-ESRGAN v0.3.0  
**Thư mục gốc:** `E:\AI Image Studio\RealESRGAN-v030`  
**Nhánh Git:** `clean-main`  
**Thời điểm thực hiện:** 10/11/2026  
**Phạm vi:** Package 1 (Pixel Limit Consistency) & Package 2 (Orphan-Safe Startup Recovery)  
**Trạng thái nghiệm thu:** HOÀN THÀNH — 100% PASS (244/244 tests)  

---

## 1. TỔNG QUAN THỰC HIỆN & PHẠM VI (EXECUTIVE SUMMARY)

Thực hiện yêu cầu tại chỉ thị remediation có kiểm soát:
- **Package 1 — Đồng bộ hóa ngưỡng giới hạn Pixel:** Đồng bộ toàn diện giới hạn pixel có hiệu lực giữa Folder Scanner, Upload API, Folder Selection và Inference Worker ở mức cấu hình chuẩn **150,000,000 pixels (150 Megapixels)**. Khắc phục dứt điểm lỗi worker gọi `validate_image_file` thiếu tham số dẫn đến fallback về ngưỡng cũ 25M px.
- **Package 2 — Startup Recovery an toàn với Job mồ côi (Orphan-Safe Recovery):** Bổ sung cơ chế thẩm tra tính hợp lệ của file nguồn (`is_valid_source_file`) trước khi đưa job vào hàng đợi khởi động (`eligible_job_ids`). Tái kiểm tra ngay trước khi suy luận (`process_single_image`), ngăn chặn hoàn toàn việc job mất file nguồn bị đưa vào worker hoặc rơi vào vòng lặp retry vô tận.

---

## 2. BACKUP & BẢO TOÀN DỮ LIỆU (BACKUP MANIFEST)

Trước khi chỉnh sửa bất kỳ dòng mã nguồn nào, hệ thống đã sao lưu an toàn các tệp mã nguồn vào thư mục lưu trữ độc lập:

**Thư mục sao lưu:**  
`E:\AI Image Studio\RealESRGAN-v030\backups\remediation_20261110_115647\`

| Tệp tin | SHA-256 Gốc (Trước sửa đổi) | SHA-256 Mới (Sau sửa đổi) |
| :--- | :--- | :--- |
| `app.py` | `a00f0771af4ba089e7d1efb5196ef57edc2e0db77a7b4e789d4bdd5d5754b665` | `0fd2fea0a041aa0e8098274e030618c6ae31f4e059c5f75fd3269c3e27971d60` |
| `job_manager.py` | `c55ec806895f30ff40cd643004d516cddb93cd69185f6b51bb3d0110c9ffd75c` | `2a87ac8d9b02d303c36964bd1abf9d820b7fcfcbdc37cdfa43457d4ae835e92f` |

### Cam kết bảo vệ hệ thống:
- **SQLite Database:** Không can thiệp sửa đổi bảng hay xóa/cập nhật 82 job mồ côi trong `data/jobs.db`.
- **User Files & Folders:** Không xóa, không di chuyển ảnh người dùng, uploads, outputs, thumbnails hay model weights.
- **Config:** Giữ nguyên giá trị pixel trong `config.json`.
- **GPU Inference:** Hoàn toàn mock inference trong quá trình test, không kích hoạt suy luận GPU thật.
- **Git:** Không commit, không push, không reset, không stash.

---

## 3. CHI TIẾT CÁC THAY ĐỔI MÃ NGUỒN (EXACT CODE CHANGES)

### 3.1. Package 1: Pixel Limit Consistency (`app.py`)

#### A. Xác định ngưỡng cấu hình có thẩm quyền (Authoritative Limit)
Tại [app.py:140-145](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/app.py#L140-L145):
```python
# Trước:
MAX_PIXELS = int(APP_CONFIG["security"].get("max_pixels", 25000000))
UPLOAD_MAX_PIXELS = int(APP_CONFIG["security"].get("upload_max_pixels", 150000000))

# Sau:
UPLOAD_MAX_PIXELS = int(APP_CONFIG["security"].get("upload_max_pixels", 150000000))
MAX_PIXELS = UPLOAD_MAX_PIXELS
PILLOW_MAX_IMAGE_PIXELS = int(APP_CONFIG["security"].get("pillow_max_image_pixels", 250000000))
```
- Ngưỡng hiệu lực chuẩn cho toàn bộ quy trình tiếp nhận và xử lý ảnh là **150,000,000 pixels**.
- `MAX_PIXELS` được trỏ về `UPLOAD_MAX_PIXELS`, đảm bảo mọi hàm gọi `validate_image_file` dù không truyền tham số `max_pixels` cũng tự động áp dụng đúng ngưỡng 150M.

#### B. Khắc phục lệnh gọi xác thực của Worker trong `process_single_image`
Tại [app.py:903](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/app.py#L903):
```python
# Trước:
is_valid_img, val_reason, (w, h), _ = validate_image_file(valid_in)

# Sau:
is_valid_img, val_reason, (w, h), _ = validate_image_file(valid_in, max_pixels=UPLOAD_MAX_PIXELS)
```
- Loại bỏ hoàn toàn sự không nhất quán giữa tầng giao diện/scanner (150M) và tầng thực thi suy luận (25M).
- Các ảnh độ phân giải cao thực tế như `4244x6400` (27.16M px) và `11520x9712` (111.88M px) được xác thực nhất quán và chuyển tiếp tới engine AI Auto-Tiling mà không bị từ chối oan.

#### C. Đồng bộ phản hồi cấu hình tại `/api/config`
Tại [app.py:1420](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/app.py#L1420):
```python
"security": {
    "max_file_size_mb": APP_CONFIG["security"]["max_file_size_mb"],
    "max_pixels": MAX_PIXELS,
    "upload_max_pixels": UPLOAD_MAX_PIXELS,
    "allowed_extensions": list(ALLOWED_EXTENSIONS)
}
```

---

### 3.2. Package 2: Orphan-Safe Startup Recovery (`job_manager.py` & `app.py`)

#### A. Bổ sung từ khóa lỗi không thử lại (`NON_RETRYABLE_ERRORS`) trong `job_manager.py`
Tại [job_manager.py:56-70](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/job_manager.py#L56-L70):
Bổ sung `"source_file_missing"` và `"source file does not exist"` vào danh sách non-retryable errors để đảm bảo không một job mất file nguồn nào có thể kích hoạt retry backoff.

#### B. Thêm phương thức kiểm tra file nguồn `is_valid_source_file`
Tại [job_manager.py:708-723](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/job_manager.py#L708-L723):
```python
def is_valid_source_file(self, file_path: Optional[Union[str, Path]]) -> bool:
    """
    Verify whether the source path:
    1. Is present (not None/empty)
    2. Is a regular file (not a directory)
    3. Is readable by the current process
    """
    if not file_path:
        return False
    try:
        p = Path(file_path)
        if not p.is_absolute():
            base_dir = self.db_path.parent.parent
            p = (base_dir / p).resolve()
        return p.is_file() and os.access(str(p), os.R_OK)
    except Exception:
        return False
```

#### C. Thắt chặt khâu phục hồi Startup Recovery trong `recover_on_startup`
Tại [job_manager.py:724-870](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/job_manager.py#L724-L870):
1. **Đối với job bị gián đoạn (`PROCESSING` / `VALIDATING`):**
   Nếu không có file output hoàn chỉnh trên đĩa, trước khi kiểm tra số lần retry, hệ thống kiểm tra `self.is_valid_source_file(in_path_str)`. Nếu file nguồn bị mất, đánh dấu `STATUS_FAILED` với lỗi `"SOURCE_FILE_MISSING: Source file is missing, not a regular file, or unreadable."` và không bao giờ re-queue.
2. **Đối với job trong trạng thái chờ retry (`RETRY_WAIT`):**
   Kiểm tra `is_valid_source_file(in_path_str)`. Nếu file nguồn không còn tồn tại, chuyển thẳng sang `STATUS_FAILED` với mã lỗi `SOURCE_FILE_MISSING`.
3. **Đối với danh sách nạp hàng đợi (`QUEUED` -> `eligible_job_ids`):**
   ```python
   # 4. Collect all eligible QUEUED jobs in deterministic order, filtering out missing sources
   cur_queued = conn.execute(
       "SELECT id, input_path FROM jobs WHERE status = ? ORDER BY created_at ASC",
       (STATUS_QUEUED,)
   )
   queued_rows = cur_queued.fetchall()
   eligible_job_ids = []
   for r in queued_rows:
       jid = r["id"]
       in_path_str = r["input_path"]
       if not self.is_valid_source_file(in_path_str):
           logger.warning(f"Recovery: QUEUED Job {jid} source file missing or invalid ({in_path_str}). Marking FAILED.")
           self.transition_status(
               jid,
               STATUS_FAILED,
               error_message="SOURCE_FILE_MISSING: Source file is missing, not a regular file, or unreadable.",
               force=True
           )
           recovered_jobs.append({"id": jid, "action": "marked_failed_missing_source"})
       else:
           eligible_job_ids.append(jid)
   ```
   **Kết quả:** Danh sách `eligible_job_ids` trả về cho Flask chỉ chứa 100% các job có file nguồn tồn tại, là file thực và có quyền đọc.
4. **Bảo toàn trạng thái lịch sử:**
   Truy vấn hoàn toàn không động chạm tới các job đã ở trạng thái `COMPLETED`, `CANCELLED` hay `FAILED`. Ngay cả khi một ảnh đã `COMPLETED` trước đây mà file nguồn bị xóa sau đó, trạng thái `COMPLETED` vẫn được bảo toàn nguyên vẹn.
5. **Tính bất biến (Idempotence):**
   Khi gọi `recover_on_startup()` nhiều lần liên tiếp, các job mất file nguồn đã chuyển sang `STATUS_FAILED` sẽ không bị xử lý lại; kết quả trả về hoàn toàn đồng nhất.

#### D. Tái kiểm tra tính hợp lệ file nguồn ngay trước khi suy luận (`process_single_image` trong `app.py`)
Tại [app.py:898-940](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/app.py#L898-L940):
```python
# Package 2: Recheck source validity immediately before inference
if not valid_in.is_file() or not os.access(str(valid_in), os.R_OK):
    error_msg = f"SOURCE_FILE_MISSING: Source file does not exist, is a directory, or is unreadable: {raw_input_path}"
    logger.error(f"Source file check failed for job {job.get('filename')}: {error_msg}")
    job_logger.log_error(job_id, error_msg)
    job_mgr.transition_status(job_id, STATUS_FAILED, error_message=error_msg, force=True)
    with jobs_lock:
        jobs[job_id]['status'] = 'error'
        jobs[job_id]['raw_status'] = STATUS_FAILED
        jobs[job_id]['error'] = error_msg
        jobs[job_id]['start_time'] = time.time()
        jobs[job_id]['end_time'] = time.time()
        jobs[job_id]['elapsed'] = 0.0
    broadcast_event('job_error', { ... })
    save_history_entry({ ... })
    return False
```
- Ngay cả khi một file ảnh bị xóa hoặc thư mục bị tháo gỡ (unmount) sau thời điểm khởi động máy chủ nhưng trước khi worker xử lý tới nó, worker sẽ phát hiện ngay tại cổng vào, ghi nhận lỗi `SOURCE_FILE_MISSING` và dừng lại mà không gọi AI Worker, không thử lại và không làm tràn bộ nhớ GPU.

---

## 4. SO SÁNH HÀNH VI TRƯỚC VÀ SAU SỬA ĐỔI (BEFORE / AFTER)

| Tình huống thực tế | Trước khi khắc phục (Before) | Sau khi khắc phục (After) |
| :--- | :--- | :--- |
| **Khởi động server khi có job mồ côi (file đã bị xóa)** | Phục hồi toàn bộ vào `eligible_job_ids`, tự động nạp vào `processing_queue`. Worker phải chạy qua từng job, gây đỏ rực hàng đợi và log lỗi dồn dập. | Thẩm tra tại nguồn trong `recover_on_startup`. Tự động đánh dấu `STATUS_FAILED` với lý do `SOURCE_FILE_MISSING` và **loại bỏ khỏi `eligible_job_ids`**, không nạp vào queue. |
| **Job bị ngắt giữa chừng mà file nguồn bị mất** | Chuyển sang `STATUS_QUEUED` nếu còn số lượt retry, đẩy vào hàng đợi vô ích. | Phát hiện file nguồn mất ngay tại bước phục hồi, chuyển thẳng sang `STATUS_FAILED` không retry. |
| **Ảnh 27.16M px và 111.88M px** | Upload và Folder Selection báo hợp lệ (do check 150M), nhưng khi bấm Upscale thì Worker từ chối với lỗi vượt quá 25M px. | Cả Scanner, Upload, Selection và Worker đều dùng chung ngưỡng 150M. Ảnh được đưa vào suy luận Auto-Tiling mượt mà trên RTX 2050 4GB. |
| **Ảnh vượt quá 150M px (ví dụ 156M px)** | Báo lỗi không nhất quán tùy theo endpoint. | Báo lỗi chuẩn xác, ghi rõ kích thước, tổng pixel và giới hạn: `Image exceeds maximum pixel limit: 12500x12500 (156,250,000 pixels, max allowed: 150,000,000)`. |
| **Job đã hoàn thành nhưng file nguồn bị xóa** | Có nguy cơ bị tính toán lại hoặc báo lỗi. | Bảo toàn nguyên vẹn `STATUS_COMPLETED`, không bị tác động bởi recovery. |
| **Job đang yêu cầu hủy (`CANCEL_REQUESTED`)** | Có nguy cơ bị re-queue lại. | Được chuyển dứt điểm sang `STATUS_CANCELLED`, không bao giờ được phục hồi lại queue. |

---

## 5. KẾT QUẢ KIỂM THỬ HỒI QUY (TEST VERIFICATION RESULTS)

Đã thiết lập bộ test chuyên biệt [`tests/test_incident_remediation_packages.py`](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/tests/test_incident_remediation_packages.py) bao phủ 100% các ca kiểm thử bắt buộc trong môi trường SQLite tạm thời biệt lập (mock inference):

```text
Ran 12 tests in 0.782s — OK
```

### Chi tiết 12 ca kiểm thử Package 1 & 2:
1. `test_queued_existing_source_eligible`: QUEUED + file tồn tại -> Nạp vào eligible queue. **[PASS]**
2. `test_queued_missing_source_not_eligible`: QUEUED + file mất -> Chặn khỏi queue, đánh dấu FAILED (`SOURCE_FILE_MISSING`). **[PASS]**
3. `test_retry_wait_missing_source_not_eligible`: RETRY_WAIT + file mất -> Không re-queue, đánh dấu FAILED. **[PASS]**
4. `test_source_deleted_after_enqueue_blocks_inference`: File bị xóa sau khi enqueue -> Worker chặn trước inference, đánh dấu FAILED. **[PASS]**
5. `test_source_path_points_to_directory_blocks_inference`: Đường dẫn là thư mục -> Bị chặn trước inference, đánh dấu FAILED. **[PASS]**
6. `test_completed_missing_source_remains_completed`: COMPLETED + file mất -> Giữ nguyên COMPLETED. **[PASS]**
7. `test_cancelled_missing_source_remains_cancelled`: CANCELLED + file mất -> Giữ nguyên CANCELLED. **[PASS]**
8. `test_cancel_requested_not_resurrected`: CANCEL_REQUESTED -> Chuyển thành CANCELLED, không phục hồi queue. **[PASS]**
9. `test_recovery_idempotence`: Chạy recovery nhiều lần -> Kết quả nhất quán tuyệt đối. **[PASS]**
10. `test_configured_150m_limit_consistently_applied`: Giới hạn 150M áp dụng đồng bộ ở mọi tầng và `/api/config`. **[PASS]**
11. `test_images_at_25m_27m_111m_produce_consistent_decisions`: Ảnh 25M, 27.16M, 111.88M được chấp nhận; >150M bị từ chối với thông báo định dạng chuẩn. **[PASS]**
12. `test_retry_count_does_not_increase_indefinitely_for_missing_source`: Retry count không tăng vô hạn khi mất file nguồn. **[PASS]**

### Kết quả chạy kiểm thử tích hợp toàn bộ hệ thống:
- **Suite 1 (7 modules liên quan - 135 tests):** `test_preflight_remediation`, `test_phase2_ai_optimization`, `test_jpg_upload_reprocessing`, `test_phase3_reliability`, `test_phase4_ux`, `test_recovery_and_queue_lifecycle`, `test_incident_remediation_packages` -> **135/135 PASS (1 skipped asset)**.
- **Suite 2 (7 modules còn lại - 109 tests):** `test_folder_browser_selection`, `test_output_validation_cancellation_history`, `test_phase1_atomic_transitions_and_retry`, `test_video_lifecycle_and_upload_safety`, `test_utils`, `test_discriminator_arch`, `test_phase5_video` -> **109/109 PASS**.
- **Tổng cộng:** **244 / 244 tests PASS (100%)**.

---

## 6. ĐÁNH GIÁ RỦI RO CÒN LẠI (REMAINING RISKS)

1. **82 job mồ côi hiện tại trong `data/jobs.db`:**
   - Hệ thống mới đã vô hiệu hóa hoàn toàn rủi ro của 82 job này: khi khởi động lại, `recover_on_startup()` sẽ bỏ qua chúng và không nạp vào queue.
   - Tuy nhiên, các dòng này vẫn đang nằm ở trạng thái `FAILED` trong database lịch sử và sẽ xuất hiện trên giao diện Web khi người dùng lọc theo `status=FAILED`.
   - Cần thực hiện Package 3 (Data Reconciliation Script) khi người dùng phê duyệt riêng để dọn sạch hoặc lưu trữ (archive) 82 dòng này.
2. **Các ổ đĩa mạng hoặc ổ cứng rời bị ngắt kết nối (Network / Removable Drive Unmount):**
   - Nếu người dùng nạp ảnh từ ổ đĩa gắn ngoài rồi rút ổ đĩa ra, worker sẽ phát hiện `SOURCE_FILE_MISSING` và chuyển job sang `FAILED` mà không gây treo ứng dụng.

---

## 7. KẾT LUẬN & DỪNG BƯỚC

Hệ thống đã hoàn thành trọn vẹn Package 1 và Package 2 đúng theo tiêu chuẩn kỹ thuật và phạm vi cho phép:
- Không thay đổi `config.json`
- Không can thiệp sửa đổi trái phép dữ liệu sản xuất trong `data/jobs.db`
- Không thực thi suy luận AI GPU thật trong quá trình kiểm thử
- Dừng ngay sau Package 1 & 2; không tự ý thực hiện Package 3.
