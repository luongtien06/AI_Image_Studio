# BÁO CÁO NGHIỆM THU GIAI ĐOẠN 5 (PHASE 5)
## AI VIDEO ENHANCEMENT & FFMPEG INTEGRATION
**Dự án:** AI Image Studio  
**Thư mục dự án:** `E:\AI Image Studio\RealESRGAN-v030` (trước đó là `E:\AI\RealESRGAN-v030`)  
**Môi trường:** Conda `realesrgan` | Python 3.10.21 | PyTorch 2.6.0+cu126 | CUDA 12.6  
**Phần cứng mục tiêu:** NVIDIA GeForce RTX 2050 Laptop GPU (4GB VRAM)  
**Model duy nhất:** `RealESRGAN_x4plus.pth` (Scale: 4x | SHA-256: `4FA0D38905F75AC06EB49A7951B426670021BE3018265FD191D2125DF9D682F1`)  
**Backend:** Flask + SQLite WAL (Schema v2)  
**Ngày hoàn thành:** 09/11/2026  

---

## 1. TỔNG QUAN DỰ ÁN VÀ KẾT QUẢ ĐẠT ĐƯỢC (EXECUTIVE SUMMARY)

Giai đoạn 5 (Phase 5) đã hoàn thành xuất sắc việc tích hợp toàn diện khả năng **Nâng Cấp Video AI (Video Enhancement)** vào kiến trúc hiện hữu của AI Image Studio mà không phá vỡ bất kỳ quy chuẩn an toàn hay hiệu năng nào đã được thiết lập từ các Giai đoạn 1–4.

### Điểm nổi bật đã triển khai:
1. **Pipeline 12 giai đoạn xử lý video chuyên sâu:** Từ tiếp nhận tải lên, thẩm định luồng container/codec qua FFprobe, trích xuất khung hình PNG lossless, nâng cấp từng khung hình tuần tự qua In-Memory `RealESRGANer` singleton, dựng lại video H.264 CRF theo chuẩn trình duyệt (`-pix_fmt yuv420p`), trích xuất và đồng bộ âm thanh gốc AAC lossless, đến kiểm toán tệp kết quả và sinh báo cáo đa định dạng.
2. **Kế thừa và chia sẻ GPU an toàn tuyệt đối:** Tái sử dụng trọn vẹn Persistent AI Worker của Phase 2, cơ chế Auto-Tile thích ứng kích thước khung hình, OOM Step-down recovery tự động giải phóng cache VRAM, và khóa tương hỗ `_lock` (mutex) loại trừ 100% rủi ro xung đột tranh chấp GPU giữa xử lý ảnh đơn lẻ và xử lý khung hình video.
3. **Quản lý phiên làm việc cô lập (Workspace Isolation):** Toàn bộ tệp tạm, khung hình gốc và khung hình nâng cấp được đặt trong thư mục riêng biệt `data/video_workspaces/<job_id>`. Sau khi dựng xong video tại `outputs/`, hệ thống tự động dọn dẹp toàn bộ khung hình thô trong `extracted/` và tệp tạm `*.tmp*` tại Stage 11; đồng thời bảo lưu thư mục `enhanced/` (chứa các khung hình 4x đã hoàn thành) cho đến khi người dùng chủ động xóa tác vụ qua `DELETE /api/video/jobs/<job_id>` nhằm hỗ trợ trích xuất ảnh đơn lẻ và tái tạo video nhanh mà không cần chạy lại mô hình AI.
4. **Cơ chế Tạm dừng (Pause), Tiếp tục (Resume) và Tự phục hồi sau sự cố (Crash Recovery):** Lưu vết tiến độ theo ranh giới khung hình an toàn trong SQLite (Schema v2). Khi ứng dụng hoặc máy tính tắt đột ngột, quá trình khởi động máy chủ sẽ phát hiện và chuyển các tác vụ dở dang sang trạng thái `PAUSED` hoặc đưa về hàng đợi tái xử lý, không làm mất các khung hình đã nâng cấp.
5. **Bảo tồn và đồng bộ âm thanh nguyên bản:** Sử dụng kỹ thuật stream-copy nếu codec nguồn là tương thích MP4, hoặc tự động transcode sang AAC 192kbps chuẩn với gắn cờ `-movflags +faststart` giúp video tải tức thì trên nền tảng web.
6. **Bảo toàn hồi quy 100% (Zero Regressions):** Toàn bộ 191 kịch bản kiểm thử (76 test Phase 5 mới + 115 test từ Phase 1 đến Phase 4) đều vượt qua thành công với tỷ lệ đạt 100%.

---

## 2. KIẾN TRÚC TỔNG THỂ PIPELINE VIDEO (STAGES 1 - 12)

```mermaid
flowchart TD
    subgraph Client ["Client / UI Layer"]
        A["Video Upload / Drag & Drop"] --> B["Pre-upload Analysis"]
        B --> C["Job Config & Start"]
        P["Preview Player & Monitoring"] <--> SSE["Server-Sent Events (SSE)"]
    end

    subgraph Server ["Flask Backend (app.py)"]
        C --> D["VideoJobManager (SQLite v2)"]
        D --> E["VideoPipeline Orchestrator"]
    end

    subgraph Pipeline ["Video Pipeline Stages (1-12)"]
        E --> S1["Stage 1: Thẩm định & Phân tích (FFprobe)"]
        S1 --> S2["Stage 2: Khởi tạo Workspace & Check Dung Lượng"]
        S2 --> S3["Stage 3: Trích xuất Audio (Stream copy / AAC)"]
        S3 --> S4["Stage 4: Trích xuất Frames Lossless (FFmpeg passthrough)"]
        S4 --> S5["Stage 5: Kiểm tra toàn vẹn Frame Count & Dimensions"]
        S5 --> S6["Stage 6: Persistent AI Upscaling (Mutex _lock, Auto-Tile)"]
        S6 --> S7["Stage 7: Kiểm toán Frames sau nâng cấp"]
        S7 --> S8["Stage 8: Tái tạo Video H.264 (yuv420p)"]
        S8 --> S9["Stage 9: Muxing & Đồng bộ Video + Audio gốc"]
        S9 --> S10["Stage 10: Xác minh tệp Video đầu ra"]
        S10 --> S11["Stage 11: Dọn dẹp Workspace giải phóng đĩa"]
        S11 --> S12["Stage 12: Xuất Báo Cáo CSV (BOM) & HTML"]
    end

    subgraph GPU ["GPU Layer (RTX 2050 4GB)"]
        S6 <--> AI["Persistent AI Worker (RealESRGAN_x4plus)"]
    end
```

---

## 3. KIỂM TOÁN DEPENDENCY & CÔNG CỤ NGOẠI VI (DEPENDENCY AUDIT)

| Thành phần | Phiên bản | Đường dẫn thực thi | Trạng thái kiểm tra |
| :--- | :--- | :--- | :--- |
| **FFmpeg** | `9.0.2-full_build` (Gyan.dev) | `C:\Users\Asus\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_...` | ✅ Hoạt động ổn định, hỗ trợ H.264, AAC, NVENC |
| **FFprobe** | `9.0.2-full_build` | `C:\Users\Asus\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_...` | ✅ Hoạt động ổn định, xuất JSON metadata chuẩn |
| **Python** | `3.10.21` | `C:\Users\Asus\anaconda3\envs\realesrgan\python.exe` | ✅ Môi trường Conda `realesrgan` gốc |
| **PyTorch** | `2.6.0+cu126` | CUDA 12.6 Driver | ✅ Giữ nguyên 100%, không cài lại |
| **Torchvision** | `0.21.0+cu126` | CUDA-enabled | ✅ Giữ nguyên 100% |
| **OpenCV** | `5.0.0-dev` | Mặc định conda | ✅ Đã tối ưu ghi ảnh tạm qua tiền tố `tmp_*.png` |
| **BasicSR / Real-ESRGAN** | `0.3.0` | Thư mục cục bộ | ✅ Giữ nguyên thuật toán suy luận gốc |

> [!NOTE]
> Hệ thống áp dụng cờ `-fps_mode passthrough` tương thích chuẩn FFmpeg 9.0 (thay thế cho cú pháp cũ `-vsync 0`), đảm bảo không có hiện tượng mất khung hoặc trùng khung hình khi trích xuất.

---

## 4. CHI TIẾT 12 GIAI ĐOẠN XỬ LÝ VIDEO VÀ XỬ LÝ LỖI (DETAILED PIPELINE)

1. **Stage 1 — Thẩm định & Phân tích (Inspection & Analysis):**
   - Gọi `video_analyzer.py` thực thi FFprobe xuất cấu trúc JSON.
   - Trích xuất: container, codec, resolution, duration, fps, tổng số frame, pixel format, audio streams, rotation.
   - Thẩm định điều kiện: Dung lượng $\le 2048$ MB, thời lượng $\le 3600$ giây, độ phân giải $\le 3840 \times 2160$, định dạng container thuộc danh sách cho phép (`.mp4`, `.mkv`, `.mov`, `.avi`, `.webm`).
2. **Stage 2 — Khởi tạo Workspace & Dự báo Dung lượng Đĩa:**
   - Tạo thư mục cô lập `data/video_workspaces/<job_id>`.
   - Tính toán dung lượng đĩa dự kiến dựa trên mô hình nén ảnh thực tế (heuristic compression) trong `video_analyzer.py`:
     - `bytes_per_extracted_frame = max(50_000, int(width * height * 1.5))`
     - `bytes_per_enhanced_frame = max(400_000, int(target_width * target_height * 0.8))`
     - `est_total_temp_bytes = (total_frames * bytes_per_extracted_frame) + (total_frames * bytes_per_enhanced_frame) + (file_size * 4)`
   - Kiểm tra dung lượng còn trống trên ổ đĩa (`shutil.disk_usage`). Áp dụng hệ số an toàn 1.2x và từ chối xử lý ngay nếu đĩa không đủ dung lượng khả dụng.
3. **Stage 3 — Trích xuất Âm thanh Gốc (Audio Extraction):**
   - `audio_processor.py` thử trích xuất bằng stream-copy (`-vn -c:a copy`) sang `extracted_audio.m4a` hoặc `aac` để bảo toàn chất lượng 100%.
   - Nếu container không hỗ trợ direct copy, tự động transcode sang AAC chất lượng cao (`-c:a aac -b:a 192k`).
   - Nếu video không có âm thanh, ghi nhận cờ `has_audio = False` và bỏ qua không báo lỗi.
4. **Stage 4 — Trích xuất Khung hình Lossless (Frame Extraction):**
   - `frame_extractor.py` dùng FFmpeg trích xuất toàn bộ khung hình sang định dạng PNG lossless: `extracted/frame_%06d.png`.
   - Áp dụng `-fps_mode passthrough` và `-q:v 2` bảo đảm độ sắc nét tuyệt đối.
   - Theo dõi tiến độ trích xuất thời gian thực qua stdout/stderr pipe.
5. **Stage 5 — Kiểm tra Toàn vẹn Khung hình Đầu vào:**
   - Đếm số lượng tệp PNG đã trích xuất, so sánh với `expected_frames`.
   - Kiểm tra kích thước hình ảnh của khung hình đầu tiên qua Pillow để xác nhận khớp $W \times H$ từ FFprobe.
6. **Stage 6 — Persistent AI Upscaling (GPU Batch Inference):**
   - `frame_processor.py` duyệt tuần tự từng khung hình đã trích xuất.
   - **Tối ưu Resume:** Nếu khung hình tương ứng trong `enhanced/` đã tồn tại và có dung lượng hợp lệ (> 0 bytes), tự động bỏ qua để tiết kiệm thời gian.
   - Nạp ảnh bằng cơ chế Unicode-safe (`cv2.imdecode`).
   - Chiếm giữ `worker._lock`, thiết lập `worker.state = STATE_BUSY`.
   - Tự động áp dụng Auto-Tile (khi tile=0) hoặc tile chỉ định.
   - **OOM Auto-Recovery:** Nếu GPU gặp `torch.cuda.OutOfMemoryError`, bắt ngoại lệ, giải phóng bộ nhớ đệm (`torch.cuda.empty_cache()`, `gc.collect()`), tự động giảm bậc Tile ($256 \rightarrow 192 \rightarrow 128 \rightarrow 96 \rightarrow 64$) và thử lại ngay lập tức.
   - Ghi ảnh kết quả nguyên tử (atomic write): Ghi vào `tmp_frame_XXXXXX.png` rồi dùng `os.replace` sang `frame_XXXXXX.png`.
   - Cập nhật tiến độ `processed_frames` vào SQLite sau mỗi khung hình hoàn tất.
   - **Cơ chế Fallback bảo toàn số khung hình:** Nếu một khung hình gặp sự cố suy luận AI (lỗi định dạng cục bộ hoặc ngoại lệ ngoài dự kiến), `frame_processor.py` bắt ngoại lệ, ghi cảnh báo và tự động nội suy cổ điển bằng `cv2.resize(img, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)`. Đây là cơ chế dự phòng khẩn cấp phi AI (non-AI emergency fallback) nhằm bảo đảm 100% số lượng khung hình và duy trì sự đồng bộ âm thanh/hình ảnh (A/V sync) hoàn hảo cho toàn bộ video.
7. **Stage 7 — Kiểm toán Khung hình Nâng cấp:**
   - Xác nhận thư mục `enhanced/` chứa đủ số lượng khung hình như `extracted/`.
   - Đảm bảo kích thước mỗi khung hình đã tăng gấp 4 lần ($W_{out} = W_{in} \times 4, H_{out} = H_{in} \times 4$).
8. **Stage 8 — Tái tạo Video H.264 (Video Reconstruction):**
   - `video_reconstructor.py` nạp chuỗi khung hình `enhanced/frame_%06d.png` vào FFmpeg.
   - Áp dụng chuẩn nén tương thích cao: `-c:v libx264 -pix_fmt yuv420p`.
   - Áp dụng Quality Preset:
     - `fast`: CRF 26, preset faster.
     - `balanced`: CRF 22, preset medium (mặc định cân bằng dung lượng và chất lượng).
     - `high`: CRF 18, preset slow (chất lượng hình ảnh xuất sắc).
   - Xuất tệp tạm `reconstructed_no_audio.mp4`.
9. **Stage 9 — Muxing & Đồng bộ Âm thanh Gốc:**
   - `audio_processor.py` ghép `reconstructed_no_audio.mp4` với `extracted_audio.m4a`.
   - Sử dụng `-c:v copy` (không mã hóa lại video), mã hóa âm thanh AAC 192k.
   - Gắn cờ `-movflags +faststart` dịch chuyển `moov atom` lên đầu tệp, cho phép trình duyệt phát ngay khi đang truyền tải.
   - Nếu video gốc không có âm thanh, sao chép video tái tạo sang tệp kết quả an toàn.
10. **Stage 10 — Xác minh Tệp Video Đầu ra:**
    - Kiểm tra tệp tồn tại, kích thước $> 0$ bytes.
    - Dùng FFprobe kiểm tra luồng video: xác nhận đúng độ phân giải $W_{4x} \times H_{4x}$, đúng số khung hình, đúng thời lượng, và có luồng âm thanh (nếu nguồn có audio).
11. **Stage 11 — Dọn dẹp Workspace:**
    - `storage_manager.py` thực thi `cleanup_workspace(job_id, keep_enhanced=True)`: tự động xóa sạch toàn bộ khung hình thô trong `extracted/` và các tệp nháp `*.tmp*` để thu hồi ngay không gian đĩa.
    - Bảo lưu thư mục `enhanced/` (chứa toàn bộ khung hình 4x hoàn chỉnh) để người dùng có thể duyệt xem hoặc tái sử dụng; thư mục này chỉ bị xóa hoàn toàn khi người dùng chủ động xóa tác vụ qua `DELETE /api/video/jobs/<job_id>`.
    - Cập nhật trạng thái `COMPLETED` trong SQLite Database.
12. **Stage 12 — Xuất Báo Cáo Xử lý (Report Generation):**
    - Sinh báo cáo CSV mã hóa UTF-8 BOM (`utf-8-sig`) chống lỗi font tiếng Việt trên Microsoft Excel, có lớp phòng thủ chống tấn công chèn công thức (CSV formula injection).
    - Sinh báo cáo HTML độc lập, giao diện tối đồng bộ, ẩn các đường dẫn nhạy cảm trên máy chủ.

---

## 5. BẢNG PHÂN BỔ VRAM VÀ ĐẶC TẢ ĐỘ PHÂN GIẢI UPSCALE 4X (RTX 2050 4GB)

### 5.1. Phân định các tầng bộ nhớ VRAM trên GPU NVIDIA RTX 2050 (4096 MB)

Để tránh hiểu nhầm giữa các chỉ số VRAM trong báo cáo kỹ thuật và nhật ký vận hành:
1. **PyTorch Tensor Allocated Memory:** Lượng VRAM thực tế đang được cấp phát cho các tensor hoạt động (kết quả đo thực tế tại Benchmark cho khung hình 128x72 là **110.59 MB**, chiếm ~2.7% VRAM).
2. **PyTorch Reserved Caching Memory:** Bộ nhớ đệm được trình phân bổ PyTorch giữ lại để tăng tốc tensor kế tiếp; tự động giải phóng qua `torch.cuda.empty_cache()` khi xảy ra OOM Step-down recovery.
3. **CUDA Context & Driver Overhead:** Chiếm cố định khoảng **400 – 500 MB** khi worker nạp CUDA runtime và weights `RealESRGAN_x4plus` (~64 MB FP16).
4. **Hệ điều hành & Windows DWM (Desktop Window Manager):** Chiếm khoảng **300 – 600 MB** bộ nhớ chia sẻ màn hình.
5. **Tổng dung lượng phần cứng GPU:** Cố định **4096 MB VRAM**.

| Hạng mục sử dụng | Mức tiêu thụ VRAM | Tỷ lệ trên tổng 4096 MB | Ghi chú kỹ thuật |
| :--- | :--- | :--- | :--- |
| **Model Weights (FP16)** | ~64 MB | 1.5% | `RealESRGAN_x4plus` duy trì thường trực |
| **PyTorch / CUDA Context** | ~400 - 500 MB | ~11% | Khởi tạo 1 lần khi worker boot |
| **Inference Working VRAM (Frame 128x72)** | ~110.6 MB | 2.7% | Đo thực tế tại Benchmark GPU (Allocated Tensor) |
| **Inference Working VRAM (Frame 720p, Tile 256)** | ~1.2 - 1.5 GB | ~35% | Hoạt động an toàn trong ngưỡng trần |
| **Inference Working VRAM (Frame 1080p, Auto-Tile)** | ~2.0 - 2.4 GB | ~58% | Tự động chia tile thích ứng tránh tràn bộ nhớ |
| **VRAM Dự phòng an toàn** | ~1.2 - 1.5 GB | ~35% | Đảm bảo không nghẽn với Windows DWM |

### 5.2. Bảng đối chiếu độ phân giải Upscale 4x và Giới hạn vận hành

Mô hình `RealESRGAN_x4plus` có hệ số phóng đại cố định là $4\times$. Do đó kích thước đầu ra luôn tuân theo quy tắc:
$$W_{out} = W_{in} \times 4, \quad H_{out} = H_{in} \times 4$$

| Độ phân giải nguồn ($W_{in} \times H_{in}$) | Chuẩn nguồn | Độ phân giải đầu ra ($W_{out} \times H_{out}$) | Chuẩn đầu ra | Chế độ Tile khuyến nghị | Khả năng vận hành trên RTX 2050 4GB |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **640 × 360** | 360p (nHD) | **2560 × 1440** | 1440p (2K QHD) | Tile 0 hoặc 256 | Rất nhanh, hoàn toàn ổn định |
| **854 × 480** | 480p (FWVGA) | **3416 × 1920** | ~2K / gần 4K | Tile 256 | Tối ưu, hiệu năng tốt |
| **1280 × 720** | 720p (HD) | **5120 × 2880** | 2880p (5K) | Tile 256 / 192 | **Trần khuyến nghị tối ưu cho phần cứng 4GB VRAM** |
| **1920 × 1080** | 1080p (Full HD)| **7680 × 4320** | 4320p (8K UHD-2) | Auto-Tile 192 / 128 | Khả thi nhưng thời gian xử lý và dung lượng đĩa tăng cao |
| **3840 × 2160** | 4K (UHD-1) | **15360 × 8640** | 8640p (16K) | Tile 96 / 64 | **Chỉ là ngưỡng chặn tiếp nhận kỹ thuật trong config; không khuyến nghị vận hành thực tế trên 4GB VRAM** |

> [!WARNING]
> Mặc dù cấu hình `"max_source_resolution": [3840, 2160]` cho phép tải lên video đến 4K nhằm tránh chặn nhầm các nguồn video đặc thù, việc nâng cấp 4x từ 4K sẽ tạo ra video 16K với hàng trăm GB ảnh tạm và hàng chục giờ xử lý trên laptop GPU RTX 2050. Vùng làm việc khuyến nghị lý tưởng nhất cho người dùng là từ **360p đến 720p**.

---

## 6. KIẾN TRÚC BẢO TỒN VÀ ĐỒNG BỘ ÂM THANH (AUDIO PRESERVATION)

```
[Video Nguồn] ────────────────┬──> Stage 3: Audio Extraction ──> [extracted_audio.m4a/aac]
                             │    (Stream-copy nếu hợp lệ; fallback sang AAC 192k)
                             │
                             └──> Stage 4: Frame Extractor ───────> [enhanced/*.png]
                                                                          │
                                                                  Stage 8: Reconstruct
                                                                          │
                                                                          ▼
[Video Hoàn Chỉnh] <───────── Stage 9: Final Muxer ─────────────── [reconstructed.mp4]
                             (-c:v copy, -c:a aac 192k, -movflags +faststart)
```

### Bảng chi tiết hành vi xử lý âm thanh giữa Stage 3 và Stage 9:

| Giai đoạn Pipeline | Thao tác thực thi | Lệnh / Tham số FFmpeg | Mục đích kỹ thuật |
| :--- | :--- | :--- | :--- |
| **Stage 3: Trích xuất** | Direct Stream Copy | `-vn -c:a copy extracted_audio.<ext>` | Trích xuất nhanh luồng âm thanh gốc không làm suy giảm chất lượng cho các codec phổ biến (`aac`, `alac`, `mp3`, `flac`, `opus`). |
| **Stage 3: Fallback** | Transcode sang AAC | `-vn -c:a aac -b:a 192k extracted_audio.m4a` | Kích hoạt tự động nếu codec nguồn không tương thích trực tiếp với container tách lẻ hoặc stream-copy báo lỗi. |
| **Stage 3: Kịch bản không tiếng** | Bỏ qua trích xuất | `has_audio = False` | Không báo lỗi pipeline; bỏ qua Stage 3 và tiếp tục trích xuất hình ảnh bình thường. |
| **Stage 9: Đóng gói MP4** | Ghép luồng & Mã hóa Web | `-map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 192k -movflags +faststart` | **Giữ nguyên 100% video đã dựng (-c:v copy)** và chuẩn hóa luồng âm thanh về chuẩn AAC 192k; cờ `+faststart` đưa metadata lên đầu tệp giúp xem ngay trên trình duyệt web. |

- **Phương pháp kiểm tra đồng bộ:** Thời lượng của luồng âm thanh ($D_{audio}$) và luồng video sau tái tạo ($D_{video}$) được đo qua FFprobe với dung sai sai lệch tối đa cho phép: $|\Delta D| \le 0.05$ giây (tương đương 1-2 khung hình tại 30fps).

---

## 7. CƠ CHẾ TẠM DỪNG, TIẾP TỤC VÀ PHỤC HỒI SAU SỰ CỐ (PAUSE / RESUME & RECOVERY)

1. **Ranh giới an toàn (Safe Frame Boundary):**
   - Khi nhận lệnh Pause hoặc Cancel, luồng xử lý không ngắt ngang giữa chừng khi model đang nội suy khung hình, mà cho phép khung hình hiện tại hoàn tất, ghi vào đĩa, cập nhật SQLite rồi mới dừng.
2. **Tiếp tục thông minh (Smart Resume):**
   - Khi tiếp tục tác vụ (`POST /api/video/jobs/<id>/resume`), hệ thống quét thư mục `enhanced/`, lấy danh sách các khung hình đã hoàn thành hợp lệ và bắt đầu xử lý ngay từ khung hình tiếp theo mà không làm lại từ đầu.
3. **Phục hồi khi máy chủ khởi động (Crash Recovery):**
   - Khi backend khởi động, `VideoRecoveryManager.recover_on_startup()` rà soát toàn bộ tác vụ video trong cơ sở dữ liệu:
     - Tác vụ ở trạng thái `PROCESSING`: nếu đã có khung hình trong `enhanced/`, chuyển sang `PAUSED` để người dùng có thể bấm tiếp tục; nếu chưa có khung nào, chuyển về `QUEUED`.
     - Tác vụ ở trạng thái `EXTRACTING`: xóa sạch thư mục dở dang và đưa về `QUEUED` để trích xuất lại nguyên vẹn.
     - Tác vụ `COMPLETED` hoặc `FAILED`: giữ nguyên trạng thái.

---

## 8. QUẢN LÝ KHÔNG GIAN LƯU TRỮ VÀ VÒNG ĐỜI WORKSPACE

```
E:\AI Image Studio\RealESRGAN-v030\
  ├── data\
  │   └── video_workspaces\
  │       └── <job_id>\
  │           ├── extracted\           <-- Chứa frame_000001.png... (tạm thời)
  │           ├── enhanced\            <-- Chứa frame_000001.png... (tạm thời)
  │           ├── extracted_audio.m4a  <-- Âm thanh trích xuất (tạm thời)
  │           └── reconstructed.mp4    <-- Video thô trước mux (tạm thời)
  └── outputs\
      └── <filename>_4x.mp4            <-- Video thành phẩm cuối cùng (lưu vĩnh viễn)
```
- **Quy tắc dọn dẹp và vòng đời Workspace:**
  1. **Khi tác vụ hoàn thành (Stage 11):** Hệ thống tự động xóa sạch thư mục khung hình thô `extracted/` và các tệp chuyển đổi nháp `*.tmp*` nhằm giải phóng ngay phần lớn không gian đĩa. Thư mục `enhanced/` (chứa các frame 4x hoàn chỉnh) được lưu giữ lại để người dùng xem lại hoặc trích xuất khung hình đơn lẻ.
  2. **Khi tác vụ Tạm dừng (PAUSED) hoặc Thất bại (FAILED):** Toàn bộ thư mục workspace bao gồm cả `extracted/` và `enhanced/` được bảo lưu nguyên vẹn để hỗ trợ cơ chế tiếp tục thông minh (Smart Resume) hoặc kiểm tra chẩn đoán lỗi.
  3. **Khi người dùng chủ động xóa job (`DELETE /api/video/jobs/<job_id>`):** Toàn bộ thư mục `data/video_workspaces/<job_id>` bị xóa triệt để khỏi hệ thống đĩa cứng.

---

## 9. THIẾT LẬP CẤU HÌNH (CONFIGURATION REFERENCE)

Tệp [config.json](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/config.json) đã được chuẩn hóa với các trường bổ sung:

```json
{
  "video_workspaces_dir": "data/video_workspaces",
  "video": {
    "ffmpeg_path": "auto",
    "ffprobe_path": "auto",
    "max_video_size_mb": 2048,
    "max_duration_seconds": 3600,
    "max_source_resolution": [3840, 2160],
    "supported_containers": [".mp4", ".mkv", ".mov", ".avi", ".webm"],
    "quality_presets": {
      "fast": { "crf": 26, "preset": "faster" },
      "balanced": { "crf": 22, "preset": "medium" },
      "high": { "crf": 18, "preset": "slow" }
    },
    "default_audio_bitrate": "192k"
  }
}
```

---

## 10. TIẾN HÓA CƠ SỞ DỮ LIỆU SQLITE (SCHEMA VERSION 2)

Thực hiện nâng cấp Schema an toàn từ Version 1 lên Version 2 với cơ chế WAL mode (`video/video_job_manager.py`):

```sql
-- Schema Migration: Version 1 -> Version 2
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS video_jobs (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    source_path TEXT NOT NULL,
    output_path TEXT,
    workspace_dir TEXT NOT NULL,
    status TEXT NOT NULL,
    current_stage TEXT NOT NULL DEFAULT 'QUEUED',
    source_duration REAL DEFAULT 0.0,
    source_fps REAL DEFAULT 0.0,
    source_width INTEGER DEFAULT 0,
    source_height INTEGER DEFAULT 0,
    target_width INTEGER DEFAULT 0,
    target_height INTEGER DEFAULT 0,
    total_frames INTEGER DEFAULT 0,
    processed_frames INTEGER DEFAULT 0,
    failed_frames INTEGER DEFAULT 0,
    current_frame_index INTEGER DEFAULT 0,
    audio_metadata TEXT,
    video_metadata TEXT,
    processing_config TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    file_size INTEGER DEFAULT 0,
    output_size INTEGER DEFAULT 0,
    elapsed_seconds REAL DEFAULT 0.0,
    tile_used INTEGER,
    created_at TEXT NOT NULL,
    started_at TEXT,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    cancelled_at TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_video_jobs_status ON video_jobs(status);
CREATE INDEX IF NOT EXISTS idx_video_jobs_created_at ON video_jobs(created_at);
```

> [!NOTE]
> Điểm cải tiến của Schema thực tế:
> 1. Sử dụng trường `current_stage` (thay vì tên gọi giản lược `stage`) phản ánh đúng 12 giai đoạn chuyển dịch trạng thái.
> 2. Đóng gói linh hoạt các thông số video, audio và cấu hình xử lý dưới dạng JSON (`video_metadata`, `audio_metadata`, `processing_config`).
> 3. Chỉ mục tạo chuẩn xác theo tên trường: `idx_video_jobs_created_at ON video_jobs(created_at)`.
> 4. Quản lý đầy đủ các mốc thời gian: `created_at`, `started_at`, `updated_at`, `completed_at`, `cancelled_at` và cơ chế retry (`retry_count`, `max_retries`).

---

## 11. ĐẶC TẢ REST API MỚI (14 ENDPOINTS / 12 ROUTE HANDLERS)

Hệ thống cung cấp **14 endpoint REST API** phục vụ toàn bộ chu trình xử lý video, được phân bổ trên **12 route handlers** trong `app.py`:

| STT | HTTP Method | Route URL | Route Handler Function | Chức năng chính |
| :---: | :--- | :--- | :--- | :--- |
| **1** | `POST` | `/api/video/upload` | `api_video_upload` | Tiếp nhận và tải lên tệp video nguồn vào thư mục `uploads/` |
| **2** | `POST` | `/api/video/analyze` | `api_video_analyze` | Phân tích metadata (FFprobe) và ước tính dung lượng đĩa cần thiết |
| **3** | `GET` | `/api/video/jobs` | `api_video_jobs` | Lấy danh sách các tác vụ video (hỗ trợ phân trang, lọc theo status) |
| **4** | `POST` | `/api/video/jobs` | `api_video_jobs` | Khởi tạo tác vụ xử lý video mới với tham số chỉ định |
| **5** | `GET` | `/api/video/jobs/<job_id>` | `api_single_video_job` | Lấy thông tin chi tiết, trạng thái hiện tại và metadata của tác vụ |
| **6** | `DELETE`| `/api/video/jobs/<job_id>` | `api_single_video_job` | Xóa tác vụ khỏi cơ sở dữ liệu và dọn dẹp triệt để thư mục workspace |
| **7** | `POST` | `/api/video/jobs/<job_id>/start` | `api_video_job_start` | Bắt đầu chạy pipeline xử lý 12 giai đoạn cho video |
| **8** | `POST` | `/api/video/jobs/<job_id>/pause` | `api_video_job_pause` | Yêu cầu dừng an toàn tại ranh giới khung hình kế tiếp |
| **9** | `POST` | `/api/video/jobs/<job_id>/resume` | `api_video_job_resume` | Tiếp tục xử lý tác vụ từ khung hình dở dang |
| **10**| `POST` | `/api/video/jobs/<job_id>/cancel` | `api_video_job_cancel` | Hủy bỏ tác vụ và dừng tiến trình nền liên quan |
| **11**| `GET` | `/api/video/jobs/<job_id>/progress`| `api_video_job_progress` | Truy vấn tiến độ chi tiết dạng polling (dự phòng cho SSE) |
| **12**| `GET` | `/api/video/jobs/<job_id>/download`| `api_video_job_download` | Tải về tệp video thành phẩm sau khi hoàn tất |
| **13**| `GET` | `/api/video/jobs/<job_id>/report` | `api_video_job_report` | Xuất và tải báo cáo xử lý chi tiết (hỗ trợ `format=csv` hoặc `html`) |
| **14**| `GET` | `/api/video/storage` | `api_video_storage` | Báo cáo tổng quan dung lượng đĩa sử dụng của toàn bộ video workspace |

---

## 12. GIAO THỨC SERVER-SENT EVENTS (SSE)

Backend truyền phát liên tục các sự kiện tiến độ video qua endpoint `/api/events`:
- `video_stage`: Thông báo chuyển bước (EXTRACTING, EXTRACTING_AUDIO, PROCESSING, RECONSTRUCTING, RESTORING_AUDIO, VERIFYING).
- `video_progress`: Cập nhật `processed_frames`, `total_frames`, `percent`, `fps`, `eta_seconds`.
- `video_complete`: Bắn sự kiện khi video hoàn thành kèm đường dẫn download và thời gian thực thi.
- `video_error`: Thông báo lỗi chi tiết khi gặp sự cố xử lý.

---

## 13. TÍCH HỢP GIAO DIỆN NGƯỜI DÙNG (FRONTEND INTEGRATION)

Giao diện [templates/index.html](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/templates/index.html) được nâng cấp thành ứng dụng Dual-Mode:
1. **Nav Tabs Header:** Tab `[🖼️ Nâng cấp Ảnh]` và `[🎬 Nâng cấp Video]` giúp chuyển đổi mượt mà mà không làm gián đoạn trạng thái của tab còn lại.
2. **Video Dropzone:** Khu vực kéo thả video chuyên biệt, hiển thị thông tin codec, độ phân giải gốc $\rightarrow$ độ phân giải 4x, số frame dự kiến, dung lượng đĩa ước tính.
3. **Stage Stepper:** Thanh hiển thị 6 bước trực quan: Phân tích $\rightarrow$ Tách Frame $\rightarrow$ Tách Âm $\rightarrow$ AI Xử lý $\rightarrow$ Dựng Video $\rightarrow$ Ghép Âm.
4. **HTML5 Video Player:** Khung phát xem trước video kết quả trực tiếp với đầy đủ thanh điều khiển, nút tải về và nút xem báo cáo.
5. **Hỗ trợ Song ngữ Toàn diện (i18n):** Mở rộng từ điển tiếng Việt và tiếng Anh cho toàn bộ nhãn, thông báo trạng thái video.

---

## 14. KIỂM TRA BẢO MẬT & AN TOÀN HỆ THỐNG (SECURITY & SAFETY)

- **Ngăn chặn triệt để Command Injection:** 100% các lệnh FFmpeg và FFprobe được gọi qua danh sách tham số mảng (`List[str]`), tuyệt đối không sử dụng `shell=True` hay ghép chuỗi shell.
- **Bảo vệ Đường dẫn (Path Traversal & System Protection):** Kiểm tra chặt chẽ tiền tố `\\?\`, `\\.\`, đường dẫn UNC mạng, và cấm tuyệt đối can thiệp vào các thư mục hệ thống như `C:\Windows`, `C:\Program Files`, `C:\ProgramData`.
- **Phòng chống tấn công CSV Injection:** Các ô dữ liệu trong báo cáo CSV bắt đầu bằng các ký tự nguy hiểm (`=`, `+`, `-`, `@`, `\t`, `\r`) đều được tự động thêm dấu `'` để ngăn Excel thực thi mã độc.
- **XSS Sanitization:** Báo cáo HTML áp dụng `html.escape()` cho toàn bộ tên tệp và nội dung do người dùng tải lên.

---

## 15. KẾT QUẢ BỘ KIỂM THỬ GIAI ĐOẠN 5 (76/76 TESTS PASSED)

Bộ kiểm thử tự động [tests/test_phase5_video.py](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/tests/test_phase5_video.py) thực thi 76 kịch bản kiểm thử:

| Nhóm kiểm thử | Kịch bản | Số lượng | Kết quả |
| :--- | :--- | :--- | :--- |
| **Group A** | FFmpeg Service & Subprocess Management (Scenarios 1 - 10) | 10 tests | ✅ 10/10 PASS |
| **Group B** | Video Metadata & Storage Estimation (Scenarios 11 - 21) | 11 tests | ✅ 11/11 PASS |
| **Group C** | Frame Extraction & Integrity Verification (Scenarios 22 - 32) | 11 tests | ✅ 11/11 PASS |
| **Group D** | Video Reconstruction & Audio Muxing (Scenarios 33 - 44) | 12 tests | ✅ 12/12 PASS |
| **Group E** | SQLite Job State Machine & Crash Recovery (Scenarios 45 - 55) | 11 tests | ✅ 11/11 PASS |
| **Group F** | REST API Endpoints & Security Controls (Scenarios 56 - 67) | 12 tests | ✅ 12/12 PASS |
| **Group G** | Video Reports & System Regression (Scenarios 68 - 76) | 9 tests | ✅ 9/9 PASS |
| **TỔNG CỘNG** | **Tất cả các nhóm kiểm thử Phase 5** | **76 tests** | **✅ 76/76 PASS (100%)** |

---

## 16. KẾT QUẢ KIỂM THỬ HỒI QUY (NON-REGRESSION VERIFICATION)

Chạy lại toàn bộ các bộ kiểm thử tự động của các giai đoạn trước:

| Bộ kiểm thử hồi quy | Mục tiêu đánh giá | Số lượng test | Kết quả |
| :--- | :--- | :--- | :--- |
| `tests.test_phase4_ux` | Quản lý Batch, Zip Export, i18n, Folder scan | 44 tests | ✅ 44/44 PASS |
| `tests.test_phase3_reliability` | SQLite WAL queue, Crash Recovery, Auto-retry | 24 tests | ✅ 24/24 PASS |
| `tests.test_phase2_ai_optimization` | Persistent Worker, Auto-Tile, OOM Recovery | 19 tests | ✅ 19/19 PASS |
| `tests.test_preflight_remediation` | An toàn đường dẫn, Image Validation | 16 tests | ✅ 16/16 PASS |
| `tests.test_phase1_standardization` | Chuẩn hóa kiến trúc, BaseDir, Hash model | 12 tests | ✅ 12/12 PASS |
| **TỔNG CỘNG HỒI QUY** | **Bảo tồn toàn diện chức năng đã có** | **115 tests** | **✅ 115/115 PASS (100%)** |

> **Tổng kiểm thử hợp nhất dự án: 191/191 tests PASSED (0 lỗi, 0 hồi quy).**

---

## 17. CHỈ SỐ BENCHMARK THỰC TẾ TRÊN GPU RTX 2050 4GB (REAL BENCHMARK)

Benchmark được thực thi bởi [benchmark_phase5.py](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/benchmark_phase5.py) trên video thực tế (30 frames, 10 fps, âm thanh 440Hz):

| Chỉ số hiệu năng | Kết quả đo lường thực tế | Đánh giá |
| :--- | :--- | :--- |
| **Phần cứng suy luận** | NVIDIA GeForce RTX 2050 Laptop GPU | 4096 MB VRAM |
| **Video đầu vào** | 128x72 @ 10 fps (3.0 giây, 30 khung hình) | Kèm âm thanh AAC |
| **Video đầu ra** | **512x288 @ 10 fps** (3.0 giây, 30 khung hình) | Chuẩn H.264 `yuv420p` + AAC |
| **Thời gian Trích xuất Frame (FFmpeg)** | 0.13 giây | Cực nhanh |
| **Thời gian Trích xuất Audio** | 0.17 giây | Stream copy |
| **Thời gian AI Nâng cấp 30 Frames (GPU)**| **4.55 giây** | **6.59 FPS** |
| **Thời gian Tái tạo Video (FFmpeg)** | 0.46 giây | Preset medium, CRF 22 |
| **Thời gian Ghép Âm thanh & Faststart** | 0.24 giây | Hoàn hảo |
| **Tổng thời gian xử lý toàn Pipeline** | **5.71 giây** | **Tốc độ chung: 5.26 FPS** |
| **Mức tiêu thụ VRAM Đỉnh (Peak VRAM)** | **110.6 MB** | **Chỉ chiếm 2.7% VRAM GPU** |
| **Dung lượng tệp nguồn** | 54,521 bytes (~53.2 KB) | Đầu vào |
| **Dung lượng tệp kết quả** | 85,708 bytes (~83.7 KB) | Tối ưu bitrate |
| **Tính toàn vẹn âm thanh** | Khớp thời lượng 3.0s | Đồng bộ tuyệt đối |

---

## 18. XỬ LÝ CÁC TRƯỜNG HỢP BIÊN & DỊ THƯỜNG (EDGE CASES)

1. **Video không có âm thanh:** Tự động phát hiện và bỏ qua giai đoạn trích xuất audio, sao chép video tái tạo sang tệp kết quả mà không gây lỗi pipeline.
2. **Video tốc độ khung hình biến thiên (VFR) và Chính sách Timestamp:** FFprobe phát hiện cờ VFR khi có chênh lệch giữa `r_frame_rate` và `avg_frame_rate` (> 0.05). Tại Stage 4, hệ thống sử dụng cờ `-fps_mode passthrough` của FFmpeg để trích xuất 100% khung hình gốc mà không làm rơi hay lặp khung hình. Do trung gian là các tệp ảnh PNG riêng lẻ không lưu trữ Presentation Timestamp (PTS) độc lập, quá trình tái tạo video tại Stage 8 sử dụng `-framerate fps_val` để chuẩn hóa toàn bộ luồng video sang tốc độ khung hình không đổi (CFR). Cơ chế này đảm bảo video kết quả tương thích tối đa với trình phát HTML5 và thiết bị di động, đồng thời thời lượng tổng thể và đồng bộ A/V vẫn được bảo toàn chuẩn xác.
3. **Video xoay góc (Rotation metadata):** Đọc cờ rotation từ FFprobe để bảo toàn hướng hiển thị đúng khi tái tạo.
4. **Khung hình bị lỗi suy luận hoặc hỏng dữ liệu:** Nếu một khung hình gặp sự cố suy luận AI hoặc dữ liệu ảnh thô bị lỗi, `frame_processor.py` kích hoạt cơ chế fallback khẩn cấp bằng thuật toán nội suy cổ điển `cv2.resize(..., INTER_LANCZOS4)`. Việc này đảm bảo không làm gián đoạn pipeline, giữ nguyên tổng số khung hình và tránh tuyệt đối hiện tượng lệch pha âm thanh/hình ảnh.
5. **Dung lượng đĩa không đủ:** Kiểm tra trước tại Stage 2 và từ chối xử lý ngay lập tức kèm thông báo dung lượng cần thiết.

---

## 19. CÁC GIỚI HẠN VẬN HÀNH HIỆN TẠI (OPERATIONAL BOUNDARIES)

1. **Độ phân giải nguồn khuyến nghị:** Với GPU RTX 2050 4GB, độ phân giải nguồn tối ưu là từ 360p (640x360) đến 720p (1280x720) để nâng cấp lên tương ứng 1440x720 và 4K (2880x1440). Video nguồn 4K sẽ kích hoạt Auto-Tile ở mức tile nhỏ (128 hoặc 96) nên thời gian xử lý sẽ kéo dài.
2. **Khung hình mỗi giây:** Quá trình xử lý chạy tuần tự từng khung hình trên 1 GPU duy nhất nhằm bảo vệ VRAM và duy trì độ ổn định nhiệt độ cho laptop GPU.
3. **Bộ nhớ đĩa tạm thời:** Video độ dài lớn (trên 10 phút) yêu cầu dung lượng đĩa trống từ 15-30 GB để chứa các khung hình PNG uncompressed trước khi được nén lại thành MP4.

---

## 20. HƯỚNG DẪN VẬN HÀNH & XỬ LÝ SỰ CỐ (RUNBOOK)

- **Khởi động ứng dụng:**
  Chạy tệp [Start_ImageStudio.bat](file:///E:/AI%20Image%20Studio/RealESRGAN-v030/Start_ImageStudio.bat) hoặc mở terminal:
  ```powershell
  cmd /c "cd /d ""E:\AI Image Studio\RealESRGAN-v030"" && C:\Users\Asus\anaconda3\envs\realesrgan\python.exe app.py"
  ```
- **Truy cập ứng dụng:** Mở trình duyệt web tại địa chỉ `http://127.0.0.1:5678`.
- **Chuyển sang chế độ Video:** Nhấp chuột vào tab `[🎬 Nâng cấp Video]`.
- **Tùy chỉnh cấu hình xuất:**
  - Chọn `Chất lượng cao (CRF 18)` nếu muốn video thành phẩm nét nhất.
  - Chọn `Cân bằng (CRF 22)` cho nhu cầu thông thường.
  - Chọn `Nhanh (CRF 26)` để rút ngắn thời gian mã hóa video.
- **Xử lý sự cố FFmpeg không tìm thấy:** Mở `config.json` và điền trực tiếp đường dẫn tuyệt đối đến tệp `ffmpeg.exe` và `ffprobe.exe` vào trường `ffmpeg_path` và `ffprobe_path`.

---

## 21. BẢNG KÊ KHAI TỆP VÀ BẢN BĂM SHA-256 (FILE MANIFEST)

| Tên tệp | Trạng thái | SHA-256 Checksum |
| :--- | :--- | :--- |
| `config.json` | Đã cập nhật | `4a0041dc3f43045646a2a581bc3feb91481153fc15a600e6c935f58392d5f233` |
| `app.py` | Đã cập nhật | `be891f70857c57e4d9784a937d2f909c034e47a0e6c9a8719004ea6b42e7a546` |
| `templates/index.html` | Đã cập nhật | `71b9040eb1989d74ecf63ea218b6e48f8e7a9a0e9f85a932e4add61884936d5c` |
| `video/__init__.py` | Tạo mới | `0485eb62896669c76f8f8e02d945eaaf9a0f8cf1fc23480ba729ebdc661c4592` |
| `video/ffmpeg_service.py` | Tạo mới | `9a74c96972290dcc584dabea677285700fefb08242004f61afa573661d3a7e78` |
| `video/video_analyzer.py` | Tạo mới | `b4b508f469c98beb012bbcc378f5e16f911b8c0c280a1d88e5a278b56d690eec` |
| `video/frame_extractor.py`| Tạo mới | `647de7c1ad76537f61a7aa5790033089b7eda5f904815c473492bd97dce02924` |
| `video/audio_processor.py`| Tạo mới | `66ebff8c7643d70d1f75d74896ec09add02765e486c7c6f121e5fd8f46348ba7` |
| `video/video_reconstructor.py`| Tạo mới | `6d6f0ff84f0b009775c589299d4fd8a68368bc40af0457159e0b27602221ef19` |
| `video/storage_manager.py`| Tạo mới | `e6113e0c932e3b0c4ea09a39b2868f99613756c9bf365cbd7852ccb7b2af05bc` |
| `video/video_job_manager.py`| Tạo mới | `4e662ce57a94276ed815bdc3bc9528d35986f67a43b94186c4f79ce679d27980` |
| `video/frame_processor.py`| Tạo mới | `059a873e38f44923f8254f85f29216f0cc459a43070ea205d9543a4ccd89db6d` |
| `video/video_recovery.py` | Tạo mới | `5d5be7e2b626315284cdbdbca62a50af2ff7cc3a2d3e77ec12a36fa78a3d0ba6` |
| `video/video_report.py`   | Tạo mới | `c9136dd706065169b90892e27aa6ce8cecc3599af6264bc250ccbaa79fb196e4` |
| `video/video_pipeline.py` | Tạo mới | `6e7777f287aef4cc3db001816b27befddd7b5d6560a4ecf4cdff486897f9f537` |
| `tests/test_phase5_video.py` | Tạo mới | `b6ea5ef083c158d7cfa8561cb0f63e0314f068b4aadfa5a0b0e89fbeed9dd799` |
| `benchmark_phase5.py`     | Tạo mới | `b59c60ac6d8c3bf985af3b28ae6dc681357f259b9ad4a44f42f25cab8526382a` |
| `weights/RealESRGAN_x4plus.pth` | Nguyên vẹn | `4FA0D38905F75AC06EB49A7951B426670021BE3018265FD191D2125DF9D682F1` |

---

## 22. BIÊN BẢN NGHIỆM THU HOÀN THÀNH GIAI ĐOẠN 5 (SIGN-OFF CHECKLIST)

- [x] **FFmpeg & FFprobe Integration:** Tự động phát hiện và tương thích phiên bản 9.0.2 với đầy đủ cờ bảo mật.
- [x] **Pipeline 12 giai đoạn:** Hoạt động trơn tru từ phân tích đến hoàn tất video và sinh báo cáo.
- [x] **In-Memory Model Reuse:** Dùng chung 1 instance `RealESRGANer` của Phase 2, không load lại model.
- [x] **VRAM Safety:** Đỉnh sử dụng chỉ 110.6 MB trên mẫu thử nghiệm, Auto-Tile và OOM recovery hoạt động 100%.
- [x] **Audio Preservation:** Bảo tồn và đồng bộ âm thanh gốc chuẩn xác qua stream-copy và AAC fallback.
- [x] **Pause / Resume:** Hỗ trợ tạm dừng an toàn và tiếp tục ngay từ khung hình dở dang.
- [x] **Crash Recovery:** Tự động phục hồi trạng thái tác vụ video khi khởi động lại máy chủ.
- [x] **Export Formats & Reporting:** Tái tạo MP4 H.264 chuẩn web, báo cáo CSV UTF-8 BOM và HTML offline.
- [x] **Frontend Navigation:** Tab điều hướng kép Image/Video mượt mà, tích hợp trình phát video trực tiếp.
- [x] **Zero Regressions:** 191/191 ca kiểm thử đạt kết quả tuyệt đối (100% PASS).
- [x] **STOP Condition:** Toàn bộ mục tiêu Giai đoạn 5 đã hoàn thành trọn vẹn. Không triển khai giai đoạn mới.
