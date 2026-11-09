# -*- coding: utf-8 -*-
"""
Video Report Generator Module for AI Image Studio (Phase 5)
Generates exportable processing reports in CSV (UTF-8 BOM) and standalone HTML
formats with summary metrics, per-job details, formula injection defense,
and privacy sanitization (omits private server paths).
"""

import csv
import html
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _human_size(bytes_val: int) -> str:
    if not bytes_val or bytes_val <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    b = float(bytes_val)
    idx = 0
    while b >= 1024.0 and idx < len(units) - 1:
        b /= 1024.0
        idx += 1
    return f"{b:.1f} {units[idx]}"


def _sanitize_csv_cell(val: Any) -> str:
    """Escape CSV formula injection triggers (=, +, -, @, \\t, \\r)."""
    if val is None:
        return ""
    s = str(val).strip()
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{s}"
    return s


def generate_video_csv_report(
    jobs: List[Dict[str, Any]],
    stats: Optional[Dict[str, Any]] = None,
    title: str = "AI Image Studio — Video Processing Report"
) -> bytes:
    """
    Generate CSV report for video jobs encoded as UTF-8 with BOM (utf-8-sig)
    for seamless compatibility with Microsoft Excel on Windows.
    """
    stats = stats or {}
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    # Header block
    writer.writerow([title])
    writer.writerow(["Thời gian xuất báo cáo / Generated At", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    writer.writerow([])

    # Summary Statistics Block
    writer.writerow(["--- TỔNG HỢP KẾT QUẢ VIDEO / VIDEO SUMMARY METRICS ---"])
    writer.writerow(["Chỉ số / Metric", "Giá trị / Value"])
    writer.writerow(["Tổng số video / Total Videos", stats.get("total_jobs", len(jobs))])
    writer.writerow(["Video hoàn thành / Completed", stats.get("completed_jobs", 0)])
    writer.writerow(["Video thất bại / Failed", stats.get("failed_jobs", 0)])
    writer.writerow(["Tổng số khung hình / Total Frames", stats.get("total_frames_sum", 0)])
    writer.writerow(["Số khung hình đã xử lý / Processed Frames", stats.get("processed_frames_sum", 0)])
    writer.writerow(["Tổng thời gian xử lý / Total Elapsed", f"{stats.get('total_seconds', 0.0):.1f}s"])
    writer.writerow(["Thời gian trung bình / Average Duration", f"{stats.get('avg_duration', 0.0):.1f}s"])
    writer.writerow(["Tổng dung lượng ban đầu / Total Input Size", _human_size(stats.get("total_input_bytes", 0))])
    writer.writerow(["Tổng dung lượng kết quả / Total Output Size", _human_size(stats.get("total_output_bytes", 0))])
    writer.writerow([])

    # Detailed Job Rows Header
    writer.writerow(["--- CHI TIẾT TỪNG VIDEO / DETAILED VIDEO JOBS ---"])
    headers = [
        "Mã tác vụ / Job ID",
        "Tên file / Filename",
        "Trạng thái / Status",
        "Giai đoạn / Stage",
        "Độ phân giải gốc / Source Res",
        "Độ phân giải 4x / Target Res",
        "FPS gốc / Source FPS",
        "Thời lượng / Duration (s)",
        "Tổng frames / Total Frames",
        "Frames hoàn thành / Processed",
        "Dung lượng gốc / Input Size",
        "Dung lượng kết quả / Output Size",
        "Thời gian xử lý / Elapsed (s)",
        "Tile đã dùng / Tile Used",
        "Codec Video",
        "Có Audio / Has Audio",
        "Thời gian tạo / Created At",
        "Thời gian hoàn tất / Completed At",
        "Thông báo lỗi / Error Message"
    ]
    writer.writerow(headers)

    for j in jobs:
        filename = j.get("filename") or (Path(j.get("source_path", "")).name if j.get("source_path") else "")
        src_res = f"{j.get('source_width', 0)}x{j.get('source_height', 0)}"
        tgt_res = f"{j.get('target_width', 0)}x{j.get('target_height', 0)}"

        # Video metadata parse
        v_meta = {}
        if j.get("video_metadata"):
            try:
                v_meta = json.loads(j["video_metadata"]) if isinstance(j["video_metadata"], str) else j["video_metadata"]
            except Exception:
                pass
        a_meta = {}
        if j.get("audio_metadata"):
            try:
                a_meta = json.loads(j["audio_metadata"]) if isinstance(j["audio_metadata"], str) else j["audio_metadata"]
            except Exception:
                pass

        v_codec = v_meta.get("codec", "h264")
        has_audio = "Có / Yes" if a_meta.get("has_audio") else "Không / No"

        row = [
            _sanitize_csv_cell(j.get("id")),
            _sanitize_csv_cell(filename),
            _sanitize_csv_cell(j.get("status")),
            _sanitize_csv_cell(j.get("current_stage")),
            _sanitize_csv_cell(src_res),
            _sanitize_csv_cell(tgt_res),
            _sanitize_csv_cell(j.get("source_fps", 0)),
            _sanitize_csv_cell(f"{j.get('source_duration', 0.0):.2f}"),
            _sanitize_csv_cell(j.get("total_frames", 0)),
            _sanitize_csv_cell(j.get("processed_frames", 0)),
            _sanitize_csv_cell(_human_size(j.get("file_size", 0))),
            _sanitize_csv_cell(_human_size(j.get("output_size", 0))),
            _sanitize_csv_cell(f"{j.get('elapsed_seconds', 0.0):.2f}"),
            _sanitize_csv_cell(j.get("tile_used") or "N/A"),
            _sanitize_csv_cell(v_codec),
            _sanitize_csv_cell(has_audio),
            _sanitize_csv_cell(j.get("created_at")),
            _sanitize_csv_cell(j.get("completed_at") or ""),
            _sanitize_csv_cell(j.get("error_message") or "")
        ]
        writer.writerow(row)

    return output.getvalue().encode("utf-8-sig")


def generate_video_html_report(
    jobs: List[Dict[str, Any]],
    stats: Optional[Dict[str, Any]] = None,
    title: str = "AI Image Studio — Báo Cáo Xử Lý Nâng Cấp Video"
) -> str:
    """
    Generate standalone offline dark-themed HTML report for video jobs.
    Fully XSS protected and omits private local server filesystem paths.
    """
    stats = stats or {}
    total_jobs = stats.get("total_jobs", len(jobs))
    completed_jobs = stats.get("completed_jobs", 0)
    failed_jobs = stats.get("failed_jobs", 0)
    total_frames = stats.get("total_frames_sum", 0)
    processed_frames = stats.get("processed_frames_sum", 0)
    total_seconds = stats.get("total_seconds", 0.0)
    avg_duration = stats.get("avg_duration", 0.0)
    total_in = _human_size(stats.get("total_input_bytes", 0))
    total_out = _human_size(stats.get("total_output_bytes", 0))

    success_rate = round((completed_jobs / total_jobs * 100), 1) if total_jobs > 0 else 0.0

    job_rows_html = []
    for j in jobs:
        jid = html.escape(str(j.get("id", "")))
        filename = html.escape(str(j.get("filename") or Path(j.get("source_path", "")).name))
        status = str(j.get("status", "")).upper()
        stage = html.escape(str(j.get("current_stage", "")))

        src_res = f"{j.get('source_width', 0)}x{j.get('source_height', 0)}"
        tgt_res = f"{j.get('target_width', 0)}x{j.get('target_height', 0)}"

        fps = f"{j.get('source_fps', 0):.2f}"
        dur = f"{j.get('source_duration', 0.0):.1f}s"
        frames = f"{j.get('processed_frames', 0)} / {j.get('total_frames', 0)}"
        size_in = _human_size(j.get("file_size", 0))
        size_out = _human_size(j.get("output_size", 0))
        elapsed = f"{j.get('elapsed_seconds', 0.0):.1f}s"
        tile = html.escape(str(j.get("tile_used") or "Auto"))
        err = html.escape(str(j.get("error_message") or "-"))

        badge_class = "badge-success" if status == "COMPLETED" else (
            "badge-danger" if status == "FAILED" else (
                "badge-warning" if status == "PAUSED" else "badge-info"
            )
        )

        job_rows_html.append(f"""
        <tr>
            <td><code>{jid[:8]}...</code></td>
            <td class="font-bold">{filename}</td>
            <td><span class="badge {badge_class}">{html.escape(status)}</span></td>
            <td><span class="stage-tag">{stage}</span></td>
            <td>{src_res} &rarr; <strong>{tgt_res}</strong></td>
            <td>{fps} fps</td>
            <td>{dur}</td>
            <td>{frames}</td>
            <td>{size_in} &rarr; {size_out}</td>
            <td>{elapsed}</td>
            <td>{tile}</td>
            <td class="error-cell">{err}</td>
        </tr>
        """)

    table_rows = "\n".join(job_rows_html)

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <title>{html.escape(title)}</title>
    <style>
        :root {{
            --bg: #0f172a;
            --surface: #1e293b;
            --border: #334155;
            --text: #f8fafc;
            --muted: #94a3b8;
            --primary: #3b82f6;
            --success: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            padding: 32px 24px;
            line-height: 1.5;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        header {{ margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }}
        h1 {{ font-size: 24px; font-weight: 700; color: #fff; }}
        .meta-time {{ color: var(--muted); font-size: 14px; margin-top: 4px; }}
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 32px;
        }}
        .kpi-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
        }}
        .kpi-title {{ font-size: 13px; color: var(--muted); text-transform: uppercase; font-weight: 600; }}
        .kpi-value {{ font-size: 26px; font-weight: 700; color: #fff; margin-top: 4px; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            overflow: hidden;
            font-size: 13px;
        }}
        th, td {{ padding: 12px 14px; text-align: left; border-bottom: 1px solid var(--border); }}
        th {{ background: #111827; color: var(--muted); font-weight: 600; text-transform: uppercase; font-size: 12px; }}
        tr:hover {{ background: rgba(255, 255, 255, 0.02); }}
        .font-bold {{ font-weight: 600; }}
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
        }}
        .badge-success {{ background: rgba(16, 185, 129, 0.2); color: var(--success); }}
        .badge-danger {{ background: rgba(239, 68, 68, 0.2); color: var(--danger); }}
        .badge-warning {{ background: rgba(245, 158, 11, 0.2); color: var(--warning); }}
        .badge-info {{ background: rgba(59, 130, 246, 0.2); color: var(--primary); }}
        .stage-tag {{
            background: #374151;
            color: #d1d5db;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 11px;
        }}
        .error-cell {{ color: var(--danger); max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        @media print {{
            body {{ background: #fff; color: #000; }}
            .kpi-card, table {{ border-color: #ccc; background: #fff; color: #000; }}
            th {{ background: #f0f0f0; color: #000; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>{html.escape(title)}</h1>
            <div class="meta-time">Thời gian kết xuất: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | AI Model: RealESRGAN_x4plus (RTX 2050 4GB)</div>
        </header>

        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-title">Tổng số video</div>
                <div class="kpi-value">{total_jobs}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Tỷ lệ thành công</div>
                <div class="kpi-value" style="color: var(--success);">{success_rate}%</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Khung hình đã xử lý</div>
                <div class="kpi-value">{processed_frames} / {total_frames}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Thời gian trung bình</div>
                <div class="kpi-value">{avg_duration:.1f}s</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Dung lượng ban đầu / Kết quả</div>
                <div class="kpi-value" style="font-size: 20px;">{total_in} &rarr; {total_out}</div>
            </div>
        </div>

        <table>
            <thead>
                <tr>
                    <th>Mã</th>
                    <th>Tên File</th>
                    <th>Trạng thái</th>
                    <th>Giai đoạn</th>
                    <th>Độ phân giải</th>
                    <th>FPS</th>
                    <th>Thời lượng</th>
                    <th>Khung hình</th>
                    <th>Dung lượng</th>
                    <th>Thời gian</th>
                    <th>Tile</th>
                    <th>Ghi chú</th>
                </tr>
            </thead>
            <tbody>
                {table_rows}
            </tbody>
        </table>
    </div>
</body>
</html>
"""
