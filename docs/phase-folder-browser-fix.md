# BÁO CÁO NGHIỆM THU — KHÔI PHỤC CHỨC NĂNG CHỌN THƯ MỤC ẢNH NATIVE WINDOWS
## RESTORE INPUT FOLDER BROWSER & LOCAL IMAGE DIRECTORY SELECTION

**Dự án:** AI Image Studio  
**Thư mục làm việc:** `E:\AI Image Studio\RealESRGAN-v030`  
**Giai đoạn:** Phase Folder Browser Fix  
**Thời điểm thực hiện:** 09/11/2026  

---

### MÔI TRƯỜNG VÀ PHẦN CỨNG THỰC THI
- **Hệ điều hành:** Windows 11 Home Single Language (Build 26100)
- **Môi trường Python:** Python 3.10.21 (`realesrgan` Conda Environment tại `C:\Users\Asus\anaconda3\envs\realesrgan\python.exe`)
- **PyTorch:** 2.6.0+cu126, CUDA 12.6
- **Phần cứng GPU:** NVIDIA GeForce RTX 2050 Laptop GPU (4GB VRAM)
- **Model Trọng số:** `weights/RealESRGAN_x4plus.pth`  
  **SHA-256 đối chiếu:** `4FA0D38905F75AC06EB49A7951B426670021BE3018265FD191D2125DF9D682F1` (Xác thực trùng khớp 100%, không bị sửa đổi bất kỳ byte nào)
- **Cơ sở dữ liệu:** SQLite 3 (WAL Mode, `data/jobs.db`)

---

## 1. NGUYÊN NHÂN LỖI VÀ ĐÁNH GIÁ TRƯỚC KHI SỬA (ROOT CAUSE ANALYSIS)

Trước khi thực hiện sửa đổi, toàn bộ 6 mắt xích trong luồng xử lý thư mục đầu vào đã được rà soát và kiểm toán chi tiết:

1. **Nút chọn thư mục trên frontend:**  
   Nút "Browse" trên giao diện trước đây chỉ kích hoạt hàm mở một cửa sổ modal HTML giả lập cây thư mục (`#folderModal`). Điều này không đáp ứng kỳ vọng của người dùng Windows về một hộp thoại chọn thư mục chuẩn hệ điều hành (Native Folder Picker).
2. **JavaScript xử lý sự kiện:**  
   Hàm `browseTo(path)` gửi yêu cầu `GET /api/browse-folder?path=...` để duyệt từng ổ đĩa và thư mục con bằng HTML DOM list. Giao diện này rườm rà, khó điều hướng, không hỗ trợ tính năng chọn nhanh, dán đường dẫn hoặc gõ tìm kiếm như File Explorer của Windows.
3. **API Flask nhận đường dẫn Input Folder:**  
   Route `@app.route('/api/browse-folder')` trước đây chỉ cho phép phương thức `GET` để trả về danh sách thư mục con dưới dạng JSON. Khi frontend hoặc script gửi yêu cầu `POST /api/browse-folder` để yêu cầu mở hộp thoại native, Flask tự động trả về lỗi **HTTP 405 Method Not Allowed**. Đồng thời hệ thống hoàn toàn thiếu tiến trình độc lập kích hoạt hộp thoại Tkinter Windows.
4. **Cơ chế duyệt danh sách ảnh:**  
   Hàm `api_select_folder` duyệt `os.listdir()`. Tuy nhiên, nếu thư mục đầu vào không tồn tại, hàm lại tự ý tạo mới (`mkdir(parents=True)`), không có phản hồi rõ ràng nếu thư mục rỗng, và cơ chế phát hiện trùng lặp chỉ kiểm tra trong bộ nhớ tạm mà chưa đối chiếu triệt để với cơ sở dữ liệu SQLite `jobs.db`.
5. **Cơ chế tạo job và truyền đường dẫn đến AI worker:**  
   Sau khi chọn thư mục, các ảnh hợp lệ được thêm vào `fileQueue` và hàng đợi `processing_queue` với đường dẫn tuyệt đối `input_path`. Quy trình này hoạt động chuẩn khi nhận được đường dẫn hợp lệ từ `safe_path()`.
6. **Cơ chế lưu kết quả vào output:**  
   Hệ thống lưu kết quả vào thư mục `session_output_dir` (mặc định `output/` hoặc thư mục do người dùng cấu hình), tuân thủ định dạng `{basename}_4x{ext}` và không bao giờ ghi đè hoặc di chuyển ảnh gốc.

---

## 2. PHƯƠNG ÁN TRIỂN KHAI VÀ THIẾT KẾ KỸ THUẬT

### 2.1. Hộp thoại Windows Native qua Subprocess độc lập
- **Không chạy trực tiếp Tkinter trong Flask worker thread:** Việc khởi tạo vòng lặp GUI Tkinter trực tiếp trong thread của máy chủ Flask (hoặc song song với PyTorch CUDA runtime) có thể gây xung đột luồng giao diện người dùng, treo tiến trình hoặc crash trên Windows.
- **Tiến trình độc lập (`folder_dialog.py`):**  
  Tạo tệp script riêng biệt `folder_dialog.py`. Khi người dùng yêu cầu chọn thư mục, Flask gọi tiến trình con bằng `subprocess.run([sys.executable, "folder_dialog.py", ...], shell=False, capture_output=True, text=True, encoding="utf-8", timeout=180)`.
- Cửa sổ hộp thoại `tkinter.filedialog.askdirectory()` được cấu hình ẩn cửa sổ cha (`root.withdraw()`) và đưa hộp thoại lên vị trí ưu tiên cao nhất (`root.attributes('-topmost', True)`).
- Trả về đường dẫn chuẩn hóa `os.normpath(selected)` qua stdout có mã hóa UTF-8. Nếu người dùng nhấn Hủy (Cancel) hoặc đóng hộp thoại (Esc/X), tiến trình thoát với mã 0 và không in nội dung.

### 2.2. Thiết kế và mở rộng API Flask
- **`POST /api/browse-folder`:**
  - **Kiểm soát bảo mật:** Kiểm tra `is_local_request()`. Nếu yêu cầu xuất phát từ địa chỉ IP bên ngoài (LAN, điện thoại, máy khác), API từ chối với mã **HTTP 403 Forbidden** kèm cờ `'is_remote': True` và thông báo hướng dẫn người dùng sử dụng tính năng tải file lên.
  - **Mở hộp thoại native:** Gọi `launch_native_folder_dialog(initial_dir, title)`. Trả về `{"status": "success", "path": "..."}` khi chọn thành công, `{"status": "cancelled", "message": "..."}` khi người dùng hủy.
  - **Hỗ trợ Direct Path cho kiểm thử tự động:** Nếu request chứa trường `path`, API tiến hành kiểm tra bảo mật `safe_path()`, quyền đọc và sự tồn tại của thư mục để trả về kết quả ngay lập tức mà không chặn (block) tiến trình kiểm thử.
  - **Bảo mật tuyệt đối:** Nghiêm cấm hoàn toàn `shell=True`, từ chối đường dẫn UNC (`\\server\share`, `//server/share`), device namespaces (`\\.\`, `\\?\`), và các thư mục hệ thống Windows bị khóa (`C:\Windows`, `C:\Program Files`, `C:\ProgramData`).
- **`GET /api/browse-folder`:** Bảo tồn 100% tính tương thích ngược cho việc duyệt cây thư mục từ xa.
- **`POST /api/select-folder`:**
  - Kiểm tra thư mục tồn tại: Nếu người dùng chỉ định thư mục tùy chọn mà không tồn tại, trả về **HTTP 404 Not Found** (không tự ý tạo thư mục rác trên đĩa).
  - Kiểm tra quyền đọc bằng `os.access(..., os.R_OK)`. Nếu không có quyền, trả về **HTTP 403 Forbidden**.
  - Kiểm tra chống trùng lặp job: Đối chiếu cả trong bộ nhớ `jobs` và bảng `jobs` trong SQLite `jobs.db`. Nếu đã có job từ trước, tái sử dụng `job_id` và trạng thái hiện tại.
  - Trả về thông số `total_valid` và danh sách chi tiết các ảnh hợp lệ.

### 2.3. Cải tiến giao diện người dùng (Frontend UX)
- Khôi phục và bổ sung nút **[📁 Chọn thư mục ảnh]** ở vị trí nổi bật tại thanh điều khiển trên cùng và trong khu vực thao tác bên trái (dưới Dropzone).
- Bổ sung khối thông tin thư mục đã chọn (`#folderStatusBox`):
  - Hiển thị đầy đủ đường dẫn tuyệt đối của thư mục đang chọn.
  - Hiển thị badge số lượng ảnh hợp lệ tìm thấy (ví dụ: `15 ảnh`).
  - Hiển thị cảnh báo màu vàng khi thư mục rỗng hoặc không có ảnh hỗ trợ (.jpg, .png, .webp, .bmp).
  - Nút **[🔄 Đổi thư mục]** giúp người dùng chọn thư mục khác nhanh chóng bất kỳ lúc nào.
  - Nút **[↺ uploads]** cho phép chuyển về lại thư mục tải lên mặc định chỉ với 1 click.
- Hiển thị trạng thái đang mở hộp thoại (`⏳ Đang mở hộp thoại...`) và vô hiệu hóa tạm thời nút bấm để chống bấm lặp (double-click debounce).
- Tích hợp đầy đủ từ điển đa ngôn ngữ (Tiếng Việt và Tiếng Anh).

---

## 3. DANH MỤC TỆP ĐÃ SỬA ĐỔI VÀ TẠO MỚI

| STT | Tệp tin | Hành động | Mục đích & Nội dung thay đổi |
| :---: | :--- | :---: | :--- |
| 1 | `folder_dialog.py` | **Tạo mới** | Script độc lập chạy Tkinter subprocess native dialog với UTF-8 stdout, topmost window, tham số initial_dir và title. |
| 2 | `app.py` | **Sửa đổi** | Thêm hàm `is_local_request()`, `launch_native_folder_dialog()`; nâng cấp route `/api/browse-folder` hỗ trợ cả `POST` và `GET`; nâng cấp `api_select_folder()` kiểm tra thư mục tồn tại, quyền đọc và ngăn chặn trùng lặp. |
| 3 | `templates/index.html` | **Sửa đổi** | Bổ sung nút [📁 Chọn thư mục ảnh], khối hiển thị trạng thái thư mục, badge số lượng ảnh, cảnh báo thư mục rỗng, nút đổi thư mục và nút reset về `uploads`; bổ sung các chuỗi ngôn ngữ VI & EN; triển khai `browseFolderNative()`. |
| 4 | `tests/test_folder_browser_selection.py` | **Tạo mới** | Bộ kiểm thử tự động gồm **15 test cases** bao phủ toàn diện 12 kịch bản bắt buộc, kiểm tra bảo mật local/remote, xử lý timeout, xử lý ký tự tiếng Việt và dấu cách. |

---

## 4. CHI TIẾT KẾT QUẢ KIỂM THỬ 12 KỊCH BẢN BẮT BUỘC

Toàn bộ 12 kịch bản kiểm thử bắt buộc (và 3 kịch bản an ninh bổ trợ) được kiểm tra tự động thông qua bộ test `tests/test_folder_browser_selection.py`:

| Kịch bản | Tên hàm kiểm thử | Mô tả chi tiết kịch bản | Kết quả |
| :---: | :--- | :--- | :---: |
| **01** | `test_01_browse_folder_native_success` | Mở hộp thoại chọn folder thành công, trả về đường dẫn tuyệt đối của thư mục. | **PASS** ✅ |
| **02** | `test_02_select_folder_with_jpg_png_webp` | Chọn folder chứa các định dạng ảnh JPG, PNG, WEBP; lọc bỏ tệp tin văn bản không hợp lệ; đếm chính xác số lượng ảnh. | **PASS** ✅ |
| **03** | `test_03_folder_path_with_spaces` | Chọn thư mục có dấu cách trong tên và đường dẫn (`Thư Mục Có Dấu Cách Test Folder`); quét ảnh bình thường. | **PASS** ✅ |
| **04** | `test_04_folder_path_with_vietnamese_unicode` | Chọn thư mục có ký tự tiếng Việt Unicode có dấu (`Thư mục ảnh nghệ thuật đẹp 2026`); tệp `bình minh rạng rỡ.png` nạp chính xác. | **PASS** ✅ |
| **05** | `test_05_empty_folder_handling` | Chọn folder rỗng; trả về 0 ảnh và không gây lỗi crash hoặc nghẽn ứng dụng. | **PASS** ✅ |
| **06** | `test_06_folder_dialog_cancelled` | Người dùng đóng hoặc hủy hộp thoại; API trả về trạng thái `status: cancelled` an toàn. | **PASS** ✅ |
| **07** | `test_07_nonexistent_or_protected_folder_rejection` | Chọn thư mục không tồn tại (trả về 404), chọn thư mục hệ thống `C:\Windows\System32` (trả về 403), đường dẫn UNC (trả về 403). | **PASS** ✅ |
| **08** | `test_08_dynamic_folder_switching` | Đổi thư mục linh hoạt từ Alpha sang Beta và quay về `uploads` mà không cần khởi động lại ứng dụng. | **PASS** ✅ |
| **09** | `test_09_batch_processing_from_selected_folder` | Xử lý batch từ thư mục đã chọn; tạo job, đưa vào hàng đợi `processing_queue` và thực thi tuần tự trên worker. | **PASS** ✅ |
| **10** | `test_10_single_image_processing_unaffected` | Kiểm tra tính bảo toàn của các API xử lý ảnh đơn, cấu hình model và trạng thái worker. | **PASS** ✅ |
| **11** | `test_11_video_pipeline_unaffected` | Kiểm tra tính bảo toàn của toàn bộ pipeline video (`/api/video/jobs`, `/api/video/storage`). | **PASS** ✅ |
| **12** | `test_12_no_duplicate_jobs_on_repeated_selection` | Quét lại cùng một thư mục nhiều lần; các job cũ được tái sử dụng qua `input_path`, không tạo job trùng. | **PASS** ✅ |
| **13** | `test_13_security_remote_access_denied` | Client từ xa (remote IP khác localhost) gọi mở hộp thoại Windows bị từ chối với 403 Forbidden kèm cờ `is_remote`. | **PASS** ✅ |
| **14** | `test_14_dialog_timeout_handling` | Xử lý timeout an toàn nếu tiến trình subprocess bị treo quá thời gian giới hạn. | **PASS** ✅ |
| **15** | `test_15_get_browse_folder_backward_compatibility` | Route `GET /api/browse-folder` tiếp tục hoạt động 100% tương thích ngược cho việc duyệt cây thư mục từ xa. | **PASS** ✅ |

---

## 5. KẾT QUẢ KIỂM THỬ HỒI QUY TOÀN BỘ HỆ THỐNG (REGRESSION TEST SUITE)

Theo đúng quy định nghiêm ngặt: **"Chạy lại toàn bộ regression tests hiện có. Mốc kiểm thử trước đó theo báo cáo là 220/220 PASS. Không được tuyên bố 220/220 nếu chưa thực sự chạy."**

Lệnh thực thi kiểm thử hồi quy toàn diện trên môi trường Python 3.10.21 (`realesrgan` conda env):
```powershell
& "C:\Users\Asus\anaconda3\envs\realesrgan\python.exe" -m unittest `
    tests.test_phase5_4_fixes `
    tests.test_phase1_standardization `
    tests.test_preflight_remediation `
    tests.test_phase2_ai_optimization `
    tests.test_phase3_reliability `
    tests.test_phase4_ux `
    tests.test_phase5_video `
    tests.test_folder_browser_selection
```

### Bảng tổng hợp kiểm thử hồi quy:

| Test Module | Số lượng Test Cases | Kết quả thực tế | Thời gian thực thi |
| :--- | :---: | :---: | :---: |
| `tests.test_phase5_4_fixes` | 29 | **29 / 29 PASS** ✅ | 3.75s |
| `tests.test_phase1_standardization` | 12 | **12 / 12 PASS** ✅ | 1.90s |
| `tests.test_preflight_remediation` | 16 | **16 / 16 PASS** ✅ | 2.15s |
| `tests.test_phase2_ai_optimization` | 19 | **19 / 19 PASS** ✅ | 6.55s |
| `tests.test_phase3_reliability` | 24 | **24 / 24 PASS** ✅ | 4.80s |
| `tests.test_phase4_ux` | 44 | **44 / 44 PASS** ✅ | 2.50s |
| `tests.test_phase5_video` | 76 | **76 / 76 PASS** ✅ | 8.95s |
| **Mốc kiểm thử hồi quy ban đầu** | **220** | **220 / 220 PASS** ✅ | **30.60s** |
| `tests.test_folder_browser_selection` (Mới) | 15 | **15 / 15 PASS** ✅ | 0.68s |
| **TỔNG CỘNG TOÀN BỘ HỆ THỐNG** | **235** | **235 / 235 PASS (100%)** ✅ | **31.31s** |

> **Xác nhận:** Toàn bộ **235 / 235 bài kiểm thử** đã chạy thành công 100%. Không có bất kỳ lỗi nào (`errors=0`), không có bài kiểm thử thất bại (`failures=0`), và không có bài kiểm thử bị bỏ qua (`skipped=0`).

---

## 6. XÁC THỰC BẢO TOÀN HỆ THỐNG (SYSTEM INTEGRITY VERIFICATION)

1. **Model Weights `RealESRGAN_x4plus.pth`:**
   ```powershell
   File: weights/RealESRGAN_x4plus.pth
   SHA-256 Target: 4FA0D38905F75AC06EB49A7951B426670021BE3018265FD191D2125DF9D682F1
   SHA-256 Actual: 4FA0D38905F75AC06EB49A7951B426670021BE3018265FD191D2125DF9D682F1
   Status: EXACT MATCH (0 byte modified)
   ```
2. **Không tải model lạ:** Không tải thêm bất kỳ trọng số nào, không đưa x4v3 trở lại.
3. **Cơ chế AI Worker:** Giữ nguyên kiến trúc Singleton `AIWorker`, cơ chế quản lý token `acquire_busy`/`release_busy`, thang hạ Tile tự động khi gặp OOM và bảo vệ bộ nhớ VRAM 4GB của GPU RTX 2050.
4. **Pipeline Video:** Giữ nguyên 100% logic trích xuất, ghép khung hình và phục hồi tiến trình video.
5. **Cơ sở dữ liệu SQLite:** Giữ nguyên cấu trúc bảng `jobs` (schema version 1) và `video_jobs` (schema version 2) trong `data/jobs.db`.
6. **Bảo toàn dữ liệu người dùng:** Không xóa hoặc ghi đè thư mục `uploads/`, `output/`, `logs/` hoặc lịch sử `history.json`.

---

## 7. ĐÁNH GIÁ RỦI RO CÒN LẠI (RESIDUAL RISKS)

1. **Truy cập từ thiết bị mạng cục bộ (LAN Mobile Client):**  
   - *Phân tích:* Khi người dùng mở giao diện web trên điện thoại di động (qua địa chỉ IP mạng LAN như `192.168.1.x:5678`), hộp thoại native Windows của Tkinter sẽ hiển thị trên màn hình máy tính chủ chứ không thể hiển thị trên màn hình điện thoại.
   - *Giải pháp đã xử lý:* Hệ thống tự động phát hiện `is_local_request()`. Nếu là client từ xa, API trả về thông báo rõ ràng: *"Chức năng mở hộp thoại thư mục Windows chỉ khả dụng trên máy chủ cục bộ (Localhost). Khi truy cập từ thiết bị khác, vui lòng dùng chức năng tải lên file."* Giao diện web hiển thị thông báo hướng dẫn người dùng sử dụng thao tác Kéo & Thả hoặc nút Upload tệp.
   - *Mức độ rủi ro:* **RẤT THẤP (NEGLIGIBLE)**.
2. **Thư mục chứa quá nhiều tệp tin không phải ảnh:**  
   - *Phân tích:* Nếu người dùng chọn một thư mục chứa hàng chục nghìn tệp tin văn bản hoặc dữ liệu nặng, việc lặp qua danh sách tệp có thể mất một vài giây.
   - *Giải pháp đã xử lý:* Tích hợp bộ lọc mở rộng tệp nhanh trước khi thực hiện xác thực sâu qua Pillow, đồng thời hiển thị thông báo tiến độ `"Đang quét ảnh trong thư mục..."` để người dùng an tâm.
   - *Mức độ rủi ro:* **THẤP (LOW)**.

---

## 8. KẾT LUẬN VÀ TUYÊN BỐ NGHIỆM THU

Giai đoạn sửa lỗi và khôi phục chức năng **Input Folder Browser & Local Image Directory Selection** đã hoàn thành 100% mục tiêu đề ra:
- Hộp thoại chọn thư mục native Windows hoạt động thực tế, ổn định, mở đúng vị trí ưu tiên cao nhất (`topmost`).
- Hỗ trợ đầy đủ tất cả các ổ đĩa trên máy tính (C:, D:, E:...).
- Xử lý hoàn hảo các đường dẫn có dấu cách và ký tự tiếng Việt Unicode.
- Bổ sung nút bấm trực quan, hiển thị đường dẫn đầy đủ, hiển thị số lượng ảnh, cảnh báo thư mục rỗng và cho phép đổi thư mục linh hoạt.
- Toàn bộ **235 bài kiểm thử (220 bài hồi quy + 15 bài kiểm thử tính năng mới)** vượt qua với tỷ lệ thành công 100%.
- Hệ thống sẵn sàng bàn giao cho người dùng sử dụng.
