# -*- coding: utf-8 -*-
"""
Phase 5 Real GPU Benchmark Script for AI Image Studio
Tests end-to-end video enhancement on NVIDIA GeForce RTX 2050 4GB.
"""

import json
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark_phase5")

# Ensure base dir on sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import torch
from video.ffmpeg_service import get_ffmpeg_service
from video.video_analyzer import VideoAnalyzer
from video.storage_manager import VideoStorageManager
from video.video_job_manager import VideoJobManager
from video.video_pipeline import VideoPipeline
from video.video_report import generate_video_csv_report, generate_video_html_report
from ai_worker import get_ai_worker

def run_benchmark():
    logger.info("=" * 60)
    logger.info("STARTING PHASE 5 REAL GPU BENCHMARK (RTX 2050 4GB)")
    logger.info("=" * 60)

    device_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    total_vram_mb = torch.cuda.get_device_properties(0).total_memory / (1024 * 1024) if torch.cuda.is_available() else 0
    logger.info(f"Target GPU: {device_name} ({total_vram_mb:.0f} MB VRAM)")

    # 1. Initialize services
    with open(BASE_DIR / "config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    svc = get_ffmpeg_service(cfg)
    analyzer = VideoAnalyzer(svc)
    storage_mgr = VideoStorageManager(BASE_DIR / "data" / "video_workspaces")
    job_mgr = VideoJobManager()
    pipeline = VideoPipeline(base_dir=BASE_DIR, config=cfg)

    # 2. Generate a 3-second 128x72 synthetic test video with 10 fps (30 frames) and 440Hz audio
    bench_dir = BASE_DIR / "data" / "benchmarks"
    bench_dir.mkdir(parents=True, exist_ok=True)
    input_video = bench_dir / "benchmark_source_128x72_10fps.mp4"

    logger.info("Generating synthetic source video with audio...")
    cmd = [
        "-f", "lavfi", "-i", "testsrc=size=128x72:rate=10:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-y", str(input_video)
    ]
    ret, out, err = svc.execute_ffmpeg(cmd)
    if ret != 0:
        logger.error(f"Failed to generate test video: {err}")
        return False

    input_size_bytes = input_video.stat().st_size
    logger.info(f"Source video created: {input_video} ({input_size_bytes} bytes)")

    # Reset GPU memory stats
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats(0)

    # 3. Create Video Job
    import uuid
    t_start = time.time()
    job_id = str(uuid.uuid4())
    ws = storage_mgr.get_workspace(job_id)
    analysis = analyzer.analyze(input_video)
    job = job_mgr.create_video_job(
        job_id=job_id,
        filename=input_video.name,
        source_path=str(input_video),
        workspace_dir=str(ws),
        video_metadata=analysis.get("video"),
        audio_metadata=analysis.get("audio"),
        processing_config={"tile_size": 0, "quality_preset": "balanced", "custom_fps": None, "preserve_audio": True},
        file_size=analysis.get("file_size", input_size_bytes)
    )
    logger.info(f"Created video job: {job_id}")

    # Track stage timings
    stage_timings = {}
    last_stage_time = time.time()
    current_stage = "QUEUED"

    def on_progress(event_type, payload):
        nonlocal last_stage_time, current_stage
        if event_type == "video_stage":
            stage_name = payload.get("stage", "")
            if stage_name != current_stage:
                now = time.time()
                stage_timings[current_stage] = now - last_stage_time
                current_stage = stage_name
                last_stage_time = now
                logger.info(f"-> Transitioned to stage: {stage_name} | {payload.get('message')}")
        elif event_type == "video_progress":
            cur = payload.get("processed_frames", 0)
            tot = payload.get("total_frames", 0)
            if cur % 5 == 0 or cur == tot:
                logger.info(f"Processing frames: {cur}/{tot} ({payload.get('percent', 0)}%)")

    # 4. Run Pipeline
    logger.info("Executing video enhancement pipeline...")
    success = pipeline.run_job(job_id=job_id, output_dir=BASE_DIR / "outputs", event_callback=on_progress)
    t_end = time.time()
    total_time = t_end - t_start

    # Record last stage timing
    stage_timings[current_stage] = time.time() - last_stage_time

    # 5. Collect metrics
    final_job = job_mgr.get_video_job(job_id)
    ws_info = storage_mgr.get_workspace_info(job_id)

    peak_vram_mb = torch.cuda.max_memory_allocated(0) / (1024 * 1024) if torch.cuda.is_available() else 0

    output_path = Path(final_job["output_path"]) if final_job.get("output_path") else None
    output_exists = output_path.is_file() if output_path else False
    output_size_bytes = output_path.stat().st_size if output_exists else 0

    # Probe output video
    probe_res = analyzer.analyze(output_path) if output_exists else None

    logger.info("=" * 60)
    logger.info("BENCHMARK RESULTS SUMMARY:")
    logger.info("=" * 60)
    logger.info(f"Status:                 {final_job.get('status')}")
    logger.info(f"Total Elapsed Time:     {total_time:.2f} s")
    logger.info(f"Source Resolution:      {final_job.get('source_width')}x{final_job.get('source_height')}")
    logger.info(f"Target Resolution:      {final_job.get('target_width')}x{final_job.get('target_height')}")
    logger.info(f"Total Frames:           {final_job.get('total_frames')}")
    logger.info(f"Processed Frames:       {final_job.get('processed_frames')}")

    total_frames = final_job.get('total_frames', 30) or 30
    fps_throughput = total_frames / total_time if total_time > 0 else 0
    ai_time = stage_timings.get("PROCESSING", 0)
    ai_fps = total_frames / ai_time if ai_time > 0 else 0

    logger.info(f"AI Frame Processing:   {ai_time:.2f} s ({ai_fps:.2f} fps)")
    logger.info(f"Overall Pipeline FPS:   {fps_throughput:.2f} fps")
    logger.info(f"Peak GPU VRAM Used:     {peak_vram_mb:.1f} MB ({(peak_vram_mb / total_vram_mb * 100):.1f}% of total)")
    logger.info(f"Output File Size:       {output_size_bytes} bytes")
    logger.info(f"Workspace Peak Size:    {ws_info.get('total_size_mb', 0):.2f} MB")

    if probe_res:
        logger.info(f"Output Streams:         Video: {probe_res.get('width')}x{probe_res.get('height')} ({probe_res.get('codec')}), Has Audio: {probe_res.get('has_audio')}")

    # Generate Reports
    reports_dir = BASE_DIR / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    csv_rep = reports_dir / f"benchmark_report_{job_id}.csv"
    html_rep = reports_dir / f"benchmark_report_{job_id}.html"

    csv_data = generate_video_csv_report([final_job])
    csv_rep.write_bytes(csv_data)

    html_data = generate_video_html_report([final_job])
    html_rep.write_text(html_data, encoding="utf-8")

    logger.info(f"CSV Report:  {csv_rep}")
    logger.info(f"HTML Report: {html_rep}")

    # Save benchmark metrics json
    metrics = {
        "device": device_name,
        "total_vram_mb": total_vram_mb,
        "peak_vram_mb": peak_vram_mb,
        "total_frames": total_frames,
        "source_resolution": [final_job.get('source_width'), final_job.get('source_height')],
        "target_resolution": [final_job.get('target_width'), final_job.get('target_height')],
        "total_time_seconds": total_time,
        "ai_processing_seconds": ai_time,
        "ai_fps": ai_fps,
        "overall_fps": fps_throughput,
        "stage_timings": stage_timings,
        "output_size_bytes": output_size_bytes,
        "output_has_audio": probe_res.get("has_audio") if probe_res else False,
        "workspace_peak_mb": ws_info.get("total_size_mb", 0)
    }
    metrics_path = bench_dir / "benchmark_metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    logger.info(f"Benchmark metrics saved to: {metrics_path}")
    logger.info("=" * 60)
    return success

if __name__ == "__main__":
    success = run_benchmark()
    sys.exit(0 if success else 1)
