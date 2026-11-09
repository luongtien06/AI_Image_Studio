# -*- coding: utf-8 -*-
"""
Report Generator Module for AI Image Studio (Phase 4)
Generates exportable processing reports in CSV and standalone HTML formats.
Includes summary metrics, complete per-job details, HTML injection protection,
and Excel-compatible UTF-8 with BOM encoding for Vietnamese text.
"""

import csv
import html
import io
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
    """Escape CSV formula injection triggers (=, +, -, @)."""
    if val is None:
        return ""
    s = str(val).strip()
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{s}"
    return s


def generate_csv_report(
    jobs: List[Dict[str, Any]],
    stats: Optional[Dict[str, Any]] = None,
    title: str = "AI Image Studio — Processing Report"
) -> bytes:
    """
    Generate CSV report encoded as UTF-8 with BOM (utf-8-sig)
    for seamless compatibility with Microsoft Excel on Windows.
    """
    stats = stats or {}
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    # Header block
    writer.writerow([title])
    writer.writerow(["Generated At", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    writer.writerow([])

    # Summary Statistics Block
    writer.writerow(["--- TỔNG HỢP KẾT QUẢ / SUMMARY METRICS ---"])
    writer.writerow(["Chỉ số / Metric", "Giá trị / Value"])
    writer.writerow(["Tổng số ảnh / Total Jobs", stats.get("total_jobs", len(jobs))])
    writer.writerow(["Hoàn thành / Completed", stats.get("completed_jobs", 0)])
    writer.writerow(["Thất bại / Failed", stats.get("failed_jobs", 0)])
    writer.writerow(["Đã hủy / Cancelled", stats.get("cancelled_jobs", 0)])
    writer.writerow(["Đang chờ / Pending", stats.get("pending_jobs", 0)])
    writer.writerow(["Tỷ lệ thành công / Success Rate", f"{stats.get('success_rate', 0.0)}%"])
    writer.writerow(["Tổng thời gian xử lý / Total Time (s)", f"{stats.get('total_duration', 0.0):.2f}s"])
    writer.writerow(["Thời gian trung bình / Avg Time (s)", f"{stats.get('avg_duration', 0.0):.2f}s"])
    writer.writerow(["Tổng dung lượng gốc / Total Input", _human_size(stats.get("total_input_size", 0))])
    writer.writerow(["Tổng dung lượng kết quả / Total Output", _human_size(stats.get("total_output_size", 0))])
    writer.writerow([])

    # Table Header
    headers = [
        "STT / No.",
        "Mã Job / Job ID",
        "Mã Batch / Batch ID",
        "File Gốc / Input File",
        "Độ Phân Giải Gốc / Input Dim",
        "Định Dạng Gốc / Input Format",
        "Dung Lượng Gốc / Input Size",
        "File Kết Quả / Output File",
        "Độ Phân Giải Mới / Output Dim",
        "Định Dạng Mới / Output Format",
        "Dung Lượng Mới / Output Size",
        "Mô Hình / AI Model",
        "Kích Thước Tile / Tile Size",
        "Thời Gian Xử Lý / Duration (s)",
        "Số Lần Thử Lại / Retries",
        "Trạng Thái / Status",
        "Thời Điểm Tạo / Created At",
        "Thời Điểm Xong / Completed At",
        "Ghi Chú Lỗi / Error Details"
    ]
    writer.writerow(headers)

    # Rows
    for idx, j in enumerate(jobs, start=1):
        inp_filename = j.get("filename")
        if not inp_filename and j.get("input_path"):
            inp_filename = str(j["input_path"]).replace('\\', '/').split('/')[-1]
        inp_filename = inp_filename or "N/A"
        inp_ext = Path(inp_filename).suffix.upper().lstrip(".") or "IMAGE"
        
        out_filename = ""
        out_ext = ""
        out_path_str = j.get("output_path")
        if out_path_str:
            out_filename = Path(out_path_str).name
            out_ext = Path(out_filename).suffix.upper().lstrip(".")

        row = [
            idx,
            _sanitize_csv_cell(j.get("id")),
            _sanitize_csv_cell(j.get("batch_id") or "N/A"),
            _sanitize_csv_cell(inp_filename),
            _sanitize_csv_cell(j.get("dimensions") or "N/A"),
            _sanitize_csv_cell(inp_ext),
            _sanitize_csv_cell(_human_size(j.get("file_size", 0))),
            _sanitize_csv_cell(out_filename or "N/A"),
            _sanitize_csv_cell(j.get("output_dimensions") or "N/A"),
            _sanitize_csv_cell(out_ext or "N/A"),
            _sanitize_csv_cell(_human_size(j.get("output_size", 0)) if j.get("output_size") else "N/A"),
            "RealESRGAN_x4plus",
            _sanitize_csv_cell(j.get("tile_used") or "Auto"),
            _sanitize_csv_cell(f"{j.get('elapsed_seconds', 0.0):.2f}"),
            _sanitize_csv_cell(j.get("retry_count", 0)),
            _sanitize_csv_cell(j.get("status")),
            _sanitize_csv_cell(j.get("created_at") or ""),
            _sanitize_csv_cell(j.get("completed_at") or ""),
            _sanitize_csv_cell(j.get("error_message") or "")
        ]
        writer.writerow(row)

    csv_text = output.getvalue()
    return csv_text.encode("utf-8-sig")


def generate_html_report(
    jobs: List[Dict[str, Any]],
    stats: Optional[Dict[str, Any]] = None,
    title: str = "AI Image Studio — Báo Cáo Xử Lý Ảnh"
) -> str:
    """
    Generate standalone offline HTML report with modern layout,
    summary cards, and strictly HTML-escaped data cells to prevent XSS.
    """
    stats = stats or {}
    total = stats.get("total_jobs", len(jobs))
    completed = stats.get("completed_jobs", 0)
    failed = stats.get("failed_jobs", 0)
    cancelled = stats.get("cancelled_jobs", 0)
    pending = stats.get("pending_jobs", 0)
    success_rate = stats.get("success_rate", 0.0)
    total_time = stats.get("total_duration", 0.0)
    avg_time = stats.get("avg_duration", 0.0)
    total_in = _human_size(stats.get("total_input_size", 0))
    total_out = _human_size(stats.get("total_output_size", 0))
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Render table rows safely
    rows_html = []
    for idx, j in enumerate(jobs, start=1):
        jid = html.escape(str(j.get("id", "")))
        status = html.escape(str(j.get("status", "")).upper())
        status_class = "badge-pending"
        if status == "COMPLETED":
            status_class = "badge-success"
        elif status == "FAILED":
            status_class = "badge-error"
        elif status == "CANCELLED":
            status_class = "badge-muted"
        elif status in ("PROCESSING", "VALIDATING"):
            status_class = "badge-info"
        elif status == "RETRY_WAIT":
            status_class = "badge-warning"

        fname = j.get("filename")
        if not fname and j.get("input_path"):
            fname = str(j["input_path"]).replace('\\', '/').split('/')[-1]
        inp_name = html.escape(str(fname or "—"))

        inp_dim = html.escape(str(j.get("dimensions") or "—"))
        inp_size = html.escape(_human_size(j.get("file_size", 0)))

        out_name = "—"
        out_dim = html.escape(str(j.get("output_dimensions") or "—"))
        out_size = "—"
        if j.get("output_path"):
            raw_out = str(j["output_path"]).replace('\\', '/').split('/')[-1]
            out_name = html.escape(raw_out)
            out_size = html.escape(_human_size(j.get("output_size", 0)))

        tile = html.escape(str(j.get("tile_used") or "Auto"))
        duration = f"{j.get('elapsed_seconds', 0.0):.2f}s"
        retries = html.escape(str(j.get("retry_count", 0)))
        err = html.escape(str(j.get("error_message") or ""))

        rows_html.append(f"""
        <tr>
          <td>{idx}</td>
          <td title="{jid}"><code>{jid[:8]}...</code></td>
          <td><strong>{inp_name}</strong></td>
          <td>{inp_dim}</td>
          <td>{inp_size}</td>
          <td>{out_name}</td>
          <td><span class="dim-scale">{out_dim}</span></td>
          <td>{out_size}</td>
          <td>{tile}</td>
          <td>{duration}</td>
          <td>{retries}</td>
          <td><span class="badge {status_class}">{status}</span></td>
          <td class="err-text">{err}</td>
        </tr>
        """)

    table_body = "\n".join(rows_html) if rows_html else "<tr><td colspan='13' class='empty-cell'>Không có dữ liệu công việc nào.</td></tr>"

    html_content = f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(title)}</title>
<style>
  :root {{
    --bg-primary: #0d1117;
    --bg-secondary: #161b22;
    --bg-card: #21262d;
    --border: #30363d;
    --text-primary: #c9d1d9;
    --text-muted: #8b949e;
    --accent: #58a6ff;
    --success: #3fb950;
    --error: #f85149;
    --warning: #d29922;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
    padding: 2rem;
    line-height: 1.5;
  }}
  .container {{ max-width: 1400px; margin: 0 auto; }}
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid var(--border);
    padding-bottom: 1.2rem;
    margin-bottom: 1.8rem;
    flex-wrap: wrap;
    gap: 1rem;
  }}
  .title-group h1 {{ font-size: 1.6rem; color: #fff; margin-bottom: 0.3rem; }}
  .title-group p {{ font-size: 0.85rem; color: var(--text-muted); }}
  .grid-metrics {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1rem;
    margin-bottom: 2rem;
  }}
  .metric-card {{
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem;
  }}
  .metric-title {{ font-size: 0.78rem; text-transform: uppercase; color: var(--text-muted); font-weight: 600; margin-bottom: 0.4rem; }}
  .metric-val {{ font-size: 1.6rem; font-weight: 700; color: #fff; }}
  .metric-card.success .metric-val {{ color: var(--success); }}
  .metric-card.error .metric-val {{ color: var(--error); }}
  .metric-card.accent .metric-val {{ color: var(--accent); }}
  .table-container {{
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow-x: auto;
  }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; text-align: left; }}
  th {{ background: var(--bg-card); color: var(--text-muted); font-weight: 600; padding: 0.75rem 0.9rem; border-bottom: 1px solid var(--border); white-space: nowrap; }}
  td {{ padding: 0.7rem 0.9rem; border-bottom: 1px solid var(--border); }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover {{ background: rgba(88, 166, 255, 0.05); }}
  code {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 0.8rem; background: var(--bg-card); padding: 2px 5px; border-radius: 4px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; }}
  .badge-success {{ background: rgba(63, 185, 80, 0.18); color: var(--success); }}
  .badge-error {{ background: rgba(248, 81, 73, 0.18); color: var(--error); }}
  .badge-warning {{ background: rgba(210, 153, 34, 0.18); color: var(--warning); }}
  .badge-info {{ background: rgba(88, 166, 255, 0.18); color: var(--accent); }}
  .badge-muted {{ background: var(--bg-card); color: var(--text-muted); }}
  .dim-scale {{ color: var(--accent); font-weight: 600; }}
  .err-text {{ color: var(--error); font-size: 0.8rem; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .empty-cell {{ text-align: center; color: var(--text-muted); padding: 2rem; }}
  .footer {{ margin-top: 2rem; text-align: center; font-size: 0.8rem; color: var(--text-muted); }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="title-group">
      <h1>⚡ AI Image Studio — Báo Cáo Xử Lý Ảnh</h1>
      <p>Mô hình: RealESRGAN_x4plus (4×) | Tạo lúc: {gen_time}</p>
    </div>
  </div>

  <div class="grid-metrics">
    <div class="metric-card">
      <div class="metric-title">Tổng số ảnh</div>
      <div class="metric-val">{total}</div>
    </div>
    <div class="metric-card success">
      <div class="metric-title">Hoàn thành</div>
      <div class="metric-val">{completed}</div>
    </div>
    <div class="metric-card error">
      <div class="metric-title">Thất bại</div>
      <div class="metric-val">{failed}</div>
    </div>
    <div class="metric-card">
      <div class="metric-title">Đã hủy</div>
      <div class="metric-val">{cancelled}</div>
    </div>
    <div class="metric-card accent">
      <div class="metric-title">Tỷ lệ thành công</div>
      <div class="metric-val">{success_rate}%</div>
    </div>
    <div class="metric-card">
      <div class="metric-title">Tổng thời gian</div>
      <div class="metric-val">{total_time:.1f}s</div>
    </div>
    <div class="metric-card">
      <div class="metric-title">Thời gian TB / ảnh</div>
      <div class="metric-val">{avg_time:.1f}s</div>
    </div>
    <div class="metric-card">
      <div class="metric-title">Tổng dung lượng ra</div>
      <div class="metric-val">{total_out}</div>
    </div>
  </div>

  <div class="table-container">
    <table>
      <thead>
        <tr>
          <th>STT</th>
          <th>Mã Job</th>
          <th>File Gốc</th>
          <th>Độ Phân Giải</th>
          <th>Dung Lượng</th>
          <th>File Kết Quả</th>
          <th>Độ Phân Giải (4×)</th>
          <th>Dung Lượng Ra</th>
          <th>Tile</th>
          <th>Thời Gian</th>
          <th>Thử Lại</th>
          <th>Trạng Thái</th>
          <th>Chi Tiết Lỗi</th>
        </tr>
      </thead>
      <tbody>
        {table_body}
      </tbody>
    </table>
  </div>

  <div class="footer">
    <p>AI Image Studio — Local Real-ESRGAN Upscaler. Offline standalone verification report.</p>
  </div>
</div>
</body>
</html>"""
    return html_content
