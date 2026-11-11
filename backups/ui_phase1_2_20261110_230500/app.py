"""
AI Image Studio — Local AI Image Upscaler (Standardized Architecture)
Flask backend for Real-ESRGAN image upscaling.
Runs 100% locally on localhost. No cloud, no external APIs, zero telemetry.
"""

import os
import sys
import re
import uuid
import json
import time
import queue
import string
import logging
import traceback
import webbrowser
import subprocess
import threading
import urllib.request
import shutil
import ctypes
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from flask import (
    Flask, render_template, request, jsonify, send_file, send_from_directory,
    Response, stream_with_context
)
from PIL import Image, UnidentifiedImageError

import atexit
from ai_worker import get_ai_worker, STATE_READY, STATE_BUSY, STATE_RECOVERING, STATE_ERROR
from gpu_monitor import get_gpu_info
from job_manager import (
    get_job_manager,
    STATUS_QUEUED, STATUS_VALIDATING, STATUS_PROCESSING,
    STATUS_RETRY_WAIT, STATUS_COMPLETED, STATUS_FAILED,
    STATUS_CANCEL_REQUESTED, STATUS_CANCELLED
)
from job_logger import get_job_logger
from batch_exporter import BatchExporter
from report_generator import generate_csv_report, generate_html_report
from video import (
    FFmpegService, get_ffmpeg_service,
    VideoAnalyzer,
    FrameExtractor,
    AudioProcessor,
    VideoReconstructor,
    VideoStorageManager,
    VideoJobManager, get_video_job_manager,
    FrameProcessor,
    VideoRecoveryManager,
    generate_video_csv_report,
    generate_video_html_report,
    VideoPipeline,
    get_unique_video_output_path
)
from video.video_job_manager import (
    STATUS_QUEUED,
    STATUS_PROCESSING,
    STATUS_PAUSED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED
)

# ═══════════════════════════════════════════════════════════════
# Dynamic Root & Path Standardization (Pathlib)
# ═══════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "server": {
        "host": "127.0.0.1",
        "port": 5678,
        "version": "1.0.0"
    },
    "directories": {
        "default_input_dir": "uploads",
        "default_output_dir": "output",
        "thumbnails_dir": "thumbnails",
        "logs_dir": "logs"
    },
    "inference": {
        "model_name": "RealESRGAN_x4plus",
        "model_weights": "weights/RealESRGAN_x4plus.pth",
        "inference_script": "inference_realesrgan.py",
        "scale": 4,
        "default_tile": 128,
        "default_tile_pad": 20,
        "default_gpu_id": 0,
        "supported_tiles": [64, 96, 128]
    },
    "security": {
        "max_file_size_mb": 100,
        "max_pixels": 25000000,
        "upload_max_pixels": 150000000,
        "pillow_max_image_pixels": 250000000,
        "allowed_extensions": [".jpg", ".jpeg", ".png", ".webp", ".bmp"],
        "allowed_image_formats": ["JPEG", "PNG", "WEBP", "BMP"],
        "block_hidden_files": True,
        "block_system_files": ["desktop.ini", "thumbs.db"]
    }
}

def load_app_config():
    """Load configuration from config.json with fallback to default values."""
    if CONFIG_FILE.is_file():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
                # Deep merge top-level keys
                cfg = dict(DEFAULT_CONFIG)
                for k, v in user_cfg.items():
                    if isinstance(v, dict) and k in cfg:
                        cfg[k] = {**cfg[k], **v}
                    else:
                        cfg[k] = v
                return cfg
        except Exception as e:
            print(f"[WARNING] Could not parse config.json, using defaults: {e}")
    return DEFAULT_CONFIG

APP_CONFIG = load_app_config()

# Derive absolute directories from BASE_DIR
UPLOAD_DIR = (BASE_DIR / APP_CONFIG["directories"]["default_input_dir"]).resolve()
OUTPUT_DIR = (BASE_DIR / APP_CONFIG["directories"]["default_output_dir"]).resolve()
THUMBNAIL_DIR = (BASE_DIR / APP_CONFIG["directories"]["thumbnails_dir"]).resolve()
LOG_DIR = (BASE_DIR / APP_CONFIG["directories"]["logs_dir"]).resolve()

INFERENCE_SCRIPT = (BASE_DIR / APP_CONFIG["inference"]["inference_script"]).resolve()
MODEL_PATH = (BASE_DIR / APP_CONFIG["inference"]["model_weights"]).resolve()

ALLOWED_EXTENSIONS = set(ext.lower() for ext in APP_CONFIG["security"]["allowed_extensions"])
ALLOWED_IMAGE_FORMATS = set(fmt.upper() for fmt in APP_CONFIG["security"]["allowed_image_formats"])
BLOCKED_SYSTEM_FILES = set(fn.lower() for fn in APP_CONFIG["security"]["block_system_files"])
MAX_FILE_SIZE = APP_CONFIG["security"]["max_file_size_mb"] * 1024 * 1024
INPUT_MAX_PIXELS = int(APP_CONFIG["security"].get("upload_max_pixels", 150000000))
OUTPUT_MAX_PIXELS = int(APP_CONFIG["security"].get("output_max_pixels", APP_CONFIG["security"].get("pillow_max_image_pixels", 250000000)))
UPLOAD_MAX_PIXELS = INPUT_MAX_PIXELS
MAX_PIXELS = INPUT_MAX_PIXELS
PILLOW_MAX_IMAGE_PIXELS = int(APP_CONFIG["security"].get("pillow_max_image_pixels", 250000000))
try:
    from PIL import Image as _PIL_Image
    _PIL_Image.MAX_IMAGE_PIXELS = PILLOW_MAX_IMAGE_PIXELS
except Exception:
    pass
HISTORY_MAX = 500

PORT = int(APP_CONFIG["server"]["port"])
VERSION = APP_CONFIG["server"]["version"]

# Create required directories safely within BASE_DIR
DATA_DIR = (BASE_DIR / APP_CONFIG.get("directories", {}).get("data_dir", "data")).resolve()
EXPORTS_DIR = (BASE_DIR / APP_CONFIG.get("directories", {}).get("exports_dir", "data/exports")).resolve()
for d in [UPLOAD_DIR, OUTPUT_DIR, THUMBNAIL_DIR, LOG_DIR, DATA_DIR, EXPORTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Phase 3 Reliability: SQLite Job Manager & Structured Per-Job Logger
job_mgr = get_job_manager(base_dir=BASE_DIR, config=APP_CONFIG)
job_logger = get_job_logger(base_dir=BASE_DIR, config=APP_CONFIG)

# Phase 4 UX & Batch: Batch Exporter (ZIP)
batch_exporter = BatchExporter(export_dir=EXPORTS_DIR)

# Phase 5 Video Enhancement: Video Job Manager, Workspaces & Pipeline
VIDEO_WORKSPACES_DIR = (BASE_DIR / APP_CONFIG.get("directories", {}).get("video_workspaces_dir", "data/video_workspaces")).resolve()
VIDEO_WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)

video_job_mgr = get_video_job_manager(db_path=DATA_DIR / "jobs.db")
video_pipeline = VideoPipeline(base_dir=BASE_DIR, job_manager=video_job_mgr, config=APP_CONFIG)
video_storage_mgr = VideoStorageManager(base_workspaces_dir=VIDEO_WORKSPACES_DIR)

# ═══════════════════════════════════════════════════════════════
# Dynamic Python Interpreter Resolution
# ═══════════════════════════════════════════════════════════════

def find_working_python():
    """Dynamically determine Python executable with PyTorch & RealESRGAN dependencies."""
    candidates = []
    
    # 1. Current running interpreter
    candidates.append(Path(sys.executable))
    
    # 2. Conda active environment
    if "CONDA_PREFIX" in os.environ:
        candidates.append(Path(os.environ["CONDA_PREFIX"]) / "python.exe")
    
    # 3. User standard conda locations
    user_home = Path.home()
    standard_conda_dirs = [
        user_home / "anaconda3",
        user_home / "miniconda3",
        user_home / ".conda",
        Path("C:/ProgramData/anaconda3"),
        Path("C:/ProgramData/miniconda3")
    ]
    for c_dir in standard_conda_dirs:
        candidates.append(c_dir / "envs" / "realesrgan" / "python.exe")
        candidates.append(c_dir / "python.exe")

    for cand in candidates:
        if cand.is_file():
            try:
                # Test if candidate has required dependencies
                res = subprocess.run(
                    [str(cand), "-c", "import torch, basicsr, cv2, PIL; print('OK')"],
                    capture_output=True, text=True, timeout=10
                )
                if "OK" in res.stdout:
                    return str(cand)
            except Exception:
                continue

    return sys.executable

PYTHON_EXE = find_working_python()

# ═══════════════════════════════════════════════════════════════
# Logging Configuration
# ═══════════════════════════════════════════════════════════════

log_file = LOG_DIR / "app.log"
file_handler = RotatingFileHandler(
    str(log_file), maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
))
logging.basicConfig(level=logging.INFO, handlers=[file_handler, logging.StreamHandler()])
logger = logging.getLogger(__name__)

# Suppress noisy Werkzeug development server output
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# ═══════════════════════════════════════════════════════════════
# Flask App Setup
# ═══════════════════════════════════════════════════════════════

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# ═══════════════════════════════════════════════════════════════
# State Management
# ═══════════════════════════════════════════════════════════════

jobs_lock = threading.Lock()
jobs = {}  # job_id -> job dict
sse_clients = []
sse_lock = threading.Lock()

processing_queue = queue.Queue()
cancel_flag = threading.Event()
_job_cancel_events: Dict[str, threading.Event] = {}
_job_cancel_lock = threading.Lock()
_startup_queue_restored = False


def get_job_cancel_event(job_id: str) -> threading.Event:
    with _job_cancel_lock:
        if job_id not in _job_cancel_events:
            _job_cancel_events[job_id] = threading.Event()
        return _job_cancel_events[job_id]


def signal_job_cancellation(job_id: str):
    with _job_cancel_lock:
        if job_id in _job_cancel_events:
            _job_cancel_events[job_id].set()


is_processing = threading.Event()
current_process = None
current_process_lock = threading.Lock()
session_output_dir = OUTPUT_DIR

system_status = {
    'model_ok': False,
    'cuda_ok': False,
    'gpu_name': 'Unknown',
    'version': VERSION,
    'base_dir': str(BASE_DIR),
    'recovery': None
}

# ═══════════════════════════════════════════════════════════════
# History Management (Re-basing & Safe Persistence)
# ═══════════════════════════════════════════════════════════════

HISTORY_FILE = LOG_DIR / "history.json"
history_lock = threading.Lock()

def resolve_history_entry(entry):
    """
    Check and re-base input_path and output_path in a history entry:
    1. Keeps original input_path and output_path strings.
    2. Tests original path on disk. If exists: exists=True.
    3. If not, attempts resolution via relative path (rel_input_path / rel_output_path).
    4. If not found, attempts resolution via filename in UPLOAD_DIR or OUTPUT_DIR.
    5. If found at re-based location: sets *_path_resolved, exists=True, rebased=True.
    6. If genuinely missing: keeps original path, sets exists=False, missing=True.
    Never deletes or alters user records.
    """
    item = dict(entry)

    # Resolve input_path
    inp = item.get('input_path')
    if inp:
        p_inp = Path(inp)
        if p_inp.is_file():
            item['input_path_resolved'] = str(p_inp.resolve())
            item['input_exists'] = True
        else:
            resolved_inp = None
            rel_inp = item.get('rel_input_path')
            if rel_inp:
                cand = (BASE_DIR / rel_inp).resolve()
                if cand.is_file():
                    resolved_inp = cand
            if not resolved_inp:
                cand = (UPLOAD_DIR / p_inp.name).resolve()
                if cand.is_file():
                    resolved_inp = cand
            if resolved_inp:
                item['input_path_resolved'] = str(resolved_inp)
                item['input_exists'] = True
                item['input_rebased'] = True
            else:
                item['input_path_resolved'] = inp
                item['input_exists'] = False
                item['missing'] = True

    # Resolve output_path
    outp = item.get('output_path')
    if outp:
        p_outp = Path(outp)
        if p_outp.is_file():
            item['output_path_resolved'] = str(p_outp.resolve())
            item['output_exists'] = True
        else:
            resolved_outp = None
            rel_outp = item.get('rel_output_path')
            if rel_outp:
                cand = (BASE_DIR / rel_outp).resolve()
                if cand.is_file():
                    resolved_outp = cand
            if not resolved_outp:
                cand = (OUTPUT_DIR / p_outp.name).resolve()
                if cand.is_file():
                    resolved_outp = cand
            if resolved_outp:
                item['output_path_resolved'] = str(resolved_outp)
                item['output_exists'] = True
                item['output_rebased'] = True
            else:
                item['output_path_resolved'] = outp
                item['output_exists'] = False
                item['missing'] = True

    return item

def load_history(resolve_paths=True):
    """Load history from history.json with automatic backup recovery and optional path re-basing."""
    raw_history = []
    if HISTORY_FILE.is_file():
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
                    if isinstance(data, list):
                        raw_history = data
        except Exception as e:
            logger.warning(f"Error reading history.json ({e}). Checking backup...")
            bak_file = HISTORY_FILE.with_suffix('.bak')
            if bak_file.is_file():
                try:
                    with open(bak_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            raw_history = data
                except Exception:
                    pass

    if not resolve_paths:
        return raw_history

    return [resolve_history_entry(item) for item in raw_history]

def save_history_entry(entry):
    """Atomically save history entry with backup, relative path support, and duplicate prevention."""
    with history_lock:
        raw_history = load_history(resolve_paths=False)
        jid = entry.get('job_id')
        st = entry.get('status')
        if jid:
            for item in raw_history[:25]:
                if item.get('job_id') == jid and item.get('status') == st:
                    return
        raw_history.insert(0, entry)
        raw_history = raw_history[:HISTORY_MAX]

        tmp_file = HISTORY_FILE.with_suffix('.tmp')
        bak_file = HISTORY_FILE.with_suffix('.bak')
        try:
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(raw_history, f, ensure_ascii=False, indent=2)
            if HISTORY_FILE.is_file():
                try:
                    shutil.copy2(HISTORY_FILE, bak_file)
                except Exception:
                    pass
            tmp_file.replace(HISTORY_FILE)
        except Exception as e:
            logger.error(f"Failed to safely save history: {e}")
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except Exception:
                    pass

# ═══════════════════════════════════════════════════════════════
# SSE Helpers
# ═══════════════════════════════════════════════════════════════

def broadcast_event(event_type, data):
    """Send an SSE event to all connected clients."""
    msg = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with sse_lock:
        dead = []
        for i, q in enumerate(sse_clients):
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(i)
        for i in reversed(dead):
            sse_clients.pop(i)

# ═══════════════════════════════════════════════════════════════
# Safe Path & Boundary Validation
# ═══════════════════════════════════════════════════════════════

# Dynamically construct forbidden Windows system directories
FORBIDDEN_SYSTEM_ROOTS = set()
_sys_drive = os.environ.get("SystemDrive", "C:")
for _cand in [
    os.environ.get("SystemRoot", "C:/Windows"),
    os.environ.get("windir", "C:/Windows"),
    os.environ.get("ProgramFiles", "C:/Program Files"),
    os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"),
    os.environ.get("ProgramData", "C:/ProgramData"),
    "C:/Windows",
    "C:/Program Files",
    "C:/Program Files (x86)",
    "C:/ProgramData",
    f"{_sys_drive}/Windows",
    f"{_sys_drive}/Program Files",
    f"{_sys_drive}/Program Files (x86)",
    f"{_sys_drive}/ProgramData"
]:
    try:
        FORBIDDEN_SYSTEM_ROOTS.add(Path(_cand).resolve())
    except Exception:
        pass

def safe_path(path_str, allow_outside_base=True):
    """
    Standardize and validate a path against path traversal, UNC, device namespaces,
    and protected Windows system directories.
    Returns resolved Path object or None if invalid/forbidden.
    """
    if path_str is None:
        return None
    if isinstance(path_str, Path):
        path_str = str(path_str)
    if not isinstance(path_str, str):
        return None

    clean_str = path_str.strip()
    if not clean_str:
        return None

    # Explicit rejection of UNC paths (loopback and remote), device paths, and forward-slash UNC
    # Rejects: \\server\share, //server/share, \\127.0.0.1\c$, \\?\C:\, \\.\COM1, //?/..., //./...
    norm_slashes = clean_str.replace('/', '\\')
    if (norm_slashes.startswith('\\\\') or 
        clean_str.startswith('//') or
        norm_slashes.startswith('\\\\?\\') or
        norm_slashes.startswith('\\\\.\\') or
        clean_str.startswith('//?/') or
        clean_str.startswith('//./')):
        logger.warning(f"Blocked UNC or device path: {clean_str}")
        return None

    # Check for drive letter + device syntax like C:\.\ or C:\?\
    if len(clean_str) >= 4 and clean_str[1] == ':' and (clean_str[2:4] in ['\\.', '/?', '\\?']):
        logger.warning(f"Blocked invalid namespace path: {clean_str}")
        return None

    try:
        raw_p = Path(clean_str)
        # If relative, resolve relative to BASE_DIR
        resolved = (BASE_DIR / raw_p).resolve() if not raw_p.is_absolute() else raw_p.resolve()

        # Reject path if it tries to access protected system directories or subdirectories
        for forbidden in FORBIDDEN_SYSTEM_ROOTS:
            if resolved == forbidden:
                logger.warning(f"Access denied to protected system directory: {resolved}")
                return None
            try:
                resolved.relative_to(forbidden)
                logger.warning(f"Access denied to protected system sub-directory: {resolved}")
                return None
            except ValueError:
                pass

        if not allow_outside_base:
            try:
                resolved.relative_to(BASE_DIR)
            except ValueError:
                logger.warning(f"Path outside BASE_DIR not allowed: {resolved}")
                return None

        return resolved
    except Exception as e:
        logger.warning(f"Error validating path '{path_str}': {e}")
        return None

# ═══════════════════════════════════════════════════════════════
# Resource Safety & System Diagnostics
# ═══════════════════════════════════════════════════════════════

class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ('dwLength', ctypes.c_ulong),
        ('dwMemoryLoad', ctypes.c_ulong),
        ('ullTotalPhys', ctypes.c_ulonglong),
        ('ullAvailPhys', ctypes.c_ulonglong),
        ('ullTotalPageFile', ctypes.c_ulonglong),
        ('ullAvailPageFile', ctypes.c_ulonglong),
        ('ullTotalVirtual', ctypes.c_ulonglong),
        ('ullAvailVirtual', ctypes.c_ulonglong),
        ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
    ]

def get_available_ram_bytes() -> int:
    """Return available physical RAM in bytes using standard library."""
    try:
        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return int(stat.ullAvailPhys)
    except Exception:
        pass
    try:
        if hasattr(os, 'sysconf') and 'SC_AVPHYS_PAGES' in os.sysconf_names:
            return int(os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE'))
    except Exception:
        pass
    return 4 * 1024 * 1024 * 1024  # Fallback: 4GB default if undetectable


def check_output_resource_safety(
    projected_width: int,
    projected_height: int,
    output_dir: Union[str, Path],
    scale: int = 4
) -> Tuple[bool, Optional[str]]:
    """
    Validate pre-inference output resource safety:
    1. Output pixel limit vs OUTPUT_MAX_PIXELS (250 MP).
    2. Available RAM safety budget (headroom for full uncompressed buffer + encoding).
    3. Output filesystem free disk space.
    Returns (is_safe: bool, error_reason: Optional[str]).
    """
    proj_pixels = projected_width * projected_height
    if proj_pixels <= 0:
        return False, "Invalid projected output dimensions"

    # 1. Output pixel limit check
    if proj_pixels > OUTPUT_MAX_PIXELS:
        return False, (
            f"Output pixel limit exceeded: projected {projected_width}x{projected_height} "
            f"({proj_pixels:,} pixels, max allowed: {OUTPUT_MAX_PIXELS:,})"
        )

    # 2. RAM safety budget check
    # Uncompressed raw RGBA buffer: 4 bytes per pixel.
    # Working overhead for tile merging, numpy buffer and PIL encoding ~ 2.5x raw buffer.
    raw_buffer_bytes = proj_pixels * 4
    estimated_working_ram = int(raw_buffer_bytes * 2.5)
    safety_margin_ram = 512 * 1024 * 1024  # 512 MB OS headroom
    required_ram = estimated_working_ram + safety_margin_ram

    avail_ram = get_available_ram_bytes()
    if avail_ram < required_ram:
        return False, (
            f"Insufficient RAM for output: projected output requires ~{required_ram // (1024*1024)} MB RAM "
            f"(including 512 MB safe headroom), but only {avail_ram // (1024*1024)} MB is available"
        )

    # 3. Disk space safety check
    # Estimated compressed output size on disk (at least 50 MB, or raw_buffer_bytes // 2 for uncompressed PNG)
    estimated_disk_need = max(50 * 1024 * 1024, raw_buffer_bytes // 2)
    disk_safety_headroom = 500 * 1024 * 1024  # 500 MB minimum free disk after write
    try:
        disk_stat = shutil.disk_usage(str(output_dir))
        free_disk = disk_stat.free
        if free_disk < (estimated_disk_need + disk_safety_headroom):
            return False, (
                f"Insufficient disk space for output: output location requires at least "
                f"{(estimated_disk_need + disk_safety_headroom) // (1024*1024)} MB free space, "
                f"but only {free_disk // (1024*1024)} MB is available"
            )
    except Exception as e:
        logger.warning(f"Could not verify disk usage for {output_dir}: {e}")

    return True, None


# ═══════════════════════════════════════════════════════════════
# Deep Image Verification (Pillow) & Categorized Results
# ═══════════════════════════════════════════════════════════════

class ValidationResult(tuple):
    """
    Subclass of tuple (is_valid, reason, dimensions, fmt) with error category.
    Fully backwards-compatible with 4-tuple unpacking across all test suites:
        is_valid, reason, (w, h), fmt = validate_image_file(...)
    While providing:
        res.category: 'valid', 'empty', 'too_large', 'unsupported', 'corrupt', 'blocked'
    """
    def __new__(cls, is_valid: bool, reason: str, dimensions: tuple, fmt: str, category: str = "corrupt"):
        return super().__new__(cls, (is_valid, reason, dimensions, fmt))

    def __init__(self, is_valid: bool, reason: str, dimensions: tuple, fmt: str, category: str = "corrupt"):
        self.is_valid = is_valid
        self.reason = reason
        self.dimensions = dimensions
        self.fmt = fmt
        self.category = category


def validate_image_file(file_path, max_pixels=None, role="input"):
    """
    Deep verification of image file using Pillow:
    1. Checks file existence & size limits (differentiated for input vs output).
    2. Excludes desktop.ini, hidden files, non-whitelisted extensions.
    3. Verifies container structure with verify().
    4. Re-opens image, checks dimensions (w > 0, h > 0, w*h <= limit_pixels).
    5. Fully decodes raster image data using img.load() (rejecting truncated/corrupt IDAT/data).
    6. Ensures file handles are closed properly.
    Returns ValidationResult(is_valid: bool, reason: str, (width, height), format_name: str, category: str).
    """
    if file_path is None:
        return ValidationResult(False, "No file path provided", (0, 0), "", category="corrupt")
    p = Path(file_path)
    if not p.is_file():
        return ValidationResult(False, "File does not exist or is a directory", (0, 0), "", category="corrupt")

    filename_lower = p.name.lower()
    if filename_lower.startswith('.') or filename_lower in BLOCKED_SYSTEM_FILES:
        return ValidationResult(False, "Blocked system or hidden file", (0, 0), "", category="blocked")

    ext = p.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return ValidationResult(False, f"Extension '{ext}' is not allowed", (0, 0), "", category="unsupported")

    try:
        size = p.stat().st_size
        if size == 0:
            return ValidationResult(False, "File is empty (0 bytes)", (0, 0), "", category="empty")
        effective_max_size = max(MAX_FILE_SIZE * 5, 500 * 1024 * 1024) if role == "output" else MAX_FILE_SIZE
        if size > effective_max_size:
            return ValidationResult(
                False,
                f"File exceeds maximum allowed size ({effective_max_size // (1024*1024)} MB)",
                (0, 0),
                "",
                category="too_large"
            )
    except Exception as e:
        return ValidationResult(False, f"Cannot access file stats: {e}", (0, 0), "", category="corrupt")

    # Step 1: Open and verify image structure integrity
    try:
        with Image.open(p) as img:
            fmt = (img.format or "").upper()
            if fmt not in ALLOWED_IMAGE_FORMATS:
                return ValidationResult(False, f"Unsupported image format: {fmt}", (0, 0), fmt, category="unsupported")
            img.verify()
    except UnidentifiedImageError:
        return ValidationResult(False, "Corrupted or unrecognized image format (UnidentifiedImageError)", (0, 0), "", category="corrupt")
    except Image.DecompressionBombError as err:
        return ValidationResult(False, f"Decompression bomb detected: {err}", (0, 0), "", category="too_large")
    except (OSError, ValueError) as err:
        return ValidationResult(False, f"Corrupted container or header ({type(err).__name__}: {err})", (0, 0), "", category="corrupt")
    except Exception as err:
        return ValidationResult(False, f"Image verification failed ({type(err).__name__}: {err})", (0, 0), "", category="corrupt")

    # Step 2: Re-open after verify(), check dimensions & pixel limit, then full decode with load()
    limit_pixels = max_pixels if max_pixels is not None else (OUTPUT_MAX_PIXELS if role == "output" else INPUT_MAX_PIXELS)
    try:
        with Image.open(p) as img:
            fmt = (img.format or "").upper()
            w, h = img.size
            if w <= 0 or h <= 0:
                return ValidationResult(False, "Invalid image dimensions", (0, 0), fmt, category="corrupt")
            total_pixels = w * h
            if total_pixels > limit_pixels:
                return ValidationResult(
                    False,
                    f"Image exceeds maximum pixel limit: {w}x{h} ({total_pixels:,} pixels, max allowed: {limit_pixels:,})",
                    (w, h),
                    fmt,
                    category="too_large"
                )

            # Check EXIF orientation if present to get corrected dimensions
            try:
                exif = img.getexif()
                if exif:
                    orientation = exif.get(0x0112)
                    if orientation in (5, 6, 7, 8):
                        w, h = h, w
            except Exception:
                pass

            # Full raster decode to catch truncated files, corrupted IDAT blocks, bad WEBP chunks
            try:
                img.load()
            except OSError as err:
                # If it's a JPEG with slight trailing truncation (common in real-world camera/web JPEGs),
                # attempt graceful recovery with LOAD_TRUNCATED_IMAGES=True
                if fmt == "JPEG" and "truncated" in str(err).lower():
                    from PIL import ImageFile
                    old_setting = ImageFile.LOAD_TRUNCATED_IMAGES
                    try:
                        ImageFile.LOAD_TRUNCATED_IMAGES = True
                        with Image.open(p) as retry_img:
                            retry_img.load()
                        return ValidationResult(True, "Valid", (w, h), fmt, category="valid")
                    finally:
                        ImageFile.LOAD_TRUNCATED_IMAGES = old_setting
                raise

        return ValidationResult(True, "Valid", (w, h), fmt, category="valid")
    except UnidentifiedImageError:
        return ValidationResult(False, "Corrupted or unrecognized image format (UnidentifiedImageError)", (0, 0), "", category="corrupt")
    except Image.DecompressionBombError as err:
        return ValidationResult(False, f"Decompression bomb detected: {err}", (0, 0), "", category="too_large")
    except OSError as err:
        return ValidationResult(False, f"Corrupted or truncated image data (OSError: {err})", (0, 0), "", category="corrupt")
    except ValueError as err:
        return ValidationResult(False, f"Invalid image parameters (ValueError: {err})", (0, 0), "", category="corrupt")
    except Exception as err:
        return ValidationResult(False, f"Image decode failed ({type(err).__name__}: {err})", (0, 0), "", category="corrupt")

def human_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

def make_thumbnail(input_path, thumb_path, max_size=300):
    try:
        with Image.open(input_path) as img:
            img.thumbnail((max_size, max_size), Image.LANCZOS)
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGB')
            thumb_path_p = Path(thumb_path)
            thumb_path_p.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(thumb_path_p), 'JPEG', quality=85)
        return True
    except Exception as e:
        logger.error(f"Thumbnail generation error for {input_path}: {e}")
        return False

def unique_output_path(output_dir, basename, ext):
    """Generate a unique output file path to avoid overwriting existing files."""
    candidate = Path(output_dir) / f"{basename}_4x{ext}"
    if not candidate.exists():
        return candidate
    counter = 1
    while True:
        candidate = Path(output_dir) / f"{basename}_4x_{counter}{ext}"
        if not candidate.exists():
            return candidate
        counter += 1

# ═══════════════════════════════════════════════════════════════
# Startup Checks
# ═══════════════════════════════════════════════════════════════

def run_startup_checks():
    global system_status
    logger.info("Running startup checks...")

    # Check model
    system_status['model_ok'] = MODEL_PATH.is_file()
    if system_status['model_ok']:
        logger.info(f"Model OK: {MODEL_PATH}")
    else:
        logger.error(f"Model NOT FOUND: {MODEL_PATH}")

    # Check CUDA & GPU Info
    try:
        gpu_info = get_gpu_info()
        system_status['cuda_ok'] = gpu_info.get('cuda_available', False)
        system_status['gpu_name'] = gpu_info.get('gpu_name', 'Unknown')
        system_status['total_vram_gb'] = gpu_info.get('total_gb', 0.0)
        logger.info(f"CUDA: {system_status['cuda_ok']}, GPU: {system_status['gpu_name']}")
    except Exception as e:
        logger.error(f"CUDA check failed: {e}")
        system_status['cuda_ok'] = False

    # Check inference script
    if not INFERENCE_SCRIPT.is_file():
        logger.error(f"Inference script NOT FOUND: {INFERENCE_SCRIPT}")

    # Initialize Persistent In-Memory AI Worker
    if system_status['model_ok']:
        try:
            worker = get_ai_worker(base_dir=BASE_DIR)
            worker.initialize_model()
            system_status['worker_status'] = worker.state
            logger.info(f"Persistent AI Worker initialized: status={worker.state}")
        except Exception as e:
            logger.error(f"Failed to initialize AI Worker: {e}")
            system_status['worker_status'] = "ERROR"

    # Phase 3: Startup Crash Recovery via SQLite
    try:
        rec_report = job_mgr.recover_on_startup(
            validator_func=lambda p: validate_image_file(p, max_pixels=OUTPUT_MAX_PIXELS, role="output")
        )
        system_status['recovery'] = rec_report
        logger.info(f"Phase 3 Crash Recovery completed: {rec_report}")

        # Phase 5: Startup Crash Recovery for Video via SQLite
        video_rec = VideoRecoveryManager(video_job_mgr)
        v_rec_report = video_rec.recover_interrupted_jobs()
        system_status['video_recovery'] = v_rec_report
        logger.info(f"Phase 5 Video Crash Recovery completed: {v_rec_report}")

        # Sync existing jobs from SQLite to in-memory cache
        with jobs_lock:
            for r in job_mgr.get_all_jobs():
                jid = r['id']
                st = r['status'].lower()
                if r['status'] == STATUS_FAILED:
                    st = 'error'
                elif r['status'] == STATUS_QUEUED:
                    st = 'pending'
                elif r['status'] == STATUS_CANCEL_REQUESTED:
                    st = 'cancelling'
                elif r['status'] == STATUS_RETRY_WAIT:
                    st = 'retry'
                jobs[jid] = {
                    'id': jid,
                    'filename': r['filename'],
                    'input_path': r['input_path'],
                    'output_path': r.get('output_path'),
                    'width': None,
                    'height': None,
                    'dimensions': r.get('dimensions', ''),
                    'file_size': r.get('file_size', 0),
                    'file_size_human': human_size(r.get('file_size', 0)),
                    'status': st,
                    'raw_status': r['status'],
                    'retry_count': r.get('retry_count', 0),
                    'max_retries': r.get('max_retries', 3),
                    'error': r.get('error_message'),
                    'is_oom': bool(r.get('is_oom', 0)),
                    'start_time': None,
                    'end_time': None,
                    'elapsed': r.get('elapsed_seconds', 0.0),
                    'tile_size': r.get('tile_used') or APP_CONFIG["inference"]["default_tile"],
                    'thumbnail_url': f"/api/thumbnail/{jid}",
                    'output_dimensions': r.get('output_dimensions', ''),
                    'output_size': r.get('output_size', 0),
                    'source': 'sqlite'
                }

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
                    'tile_mode': APP_CONFIG["inference"].get("default_tile_mode", "auto"),
                    'tile_size': APP_CONFIG["inference"].get("default_tile", 128),
                    'export_format': APP_CONFIG["export"].get("default_format", "auto"),
                    'quality': APP_CONFIG["export"].get("default_quality", 95),
                    'output_dir': str(session_output_dir)
                })
            _startup_queue_restored = True
    except Exception as e:
        logger.error(f"Startup crash recovery failed: {e}")

# ═══════════════════════════════════════════════════════════════
# Processing Engine
# ═══════════════════════════════════════════════════════════════

def process_single_image(job):
    """Process a single image with Real-ESRGAN sequentially with SQLite state & logging."""
    global current_process
    job_id = job['id']
    raw_input_path = job.get('input_path')
    raw_output_dir = job.get('output_dir', session_output_dir)
    tile_size = job.get('tile_size', 128)
    tile_mode = job.get('tile_mode', 'auto')
    export_format = job.get('export_format', 'auto')
    quality = int(job.get('quality', 95))
    tile_used = tile_size

    # Ensure job is registered in SQLite (guarantees consistency for direct in-memory additions)
    if not job_mgr.get_job(job_id):
        try:
            job_mgr.add_job(
                job_id=job_id,
                filename=job.get('filename', 'unknown'),
                input_path=str(raw_input_path),
                dimensions=job.get('dimensions', ''),
                file_size=job.get('file_size', 0),
                max_retries=job.get('max_retries', 3)
            )
        except Exception:
            pass

    # Start structured per-job logging
    job_logger.start_job(
        job_id=job_id,
        filename=job.get('filename', 'unknown'),
        input_path=str(raw_input_path),
        tile_size=tile_size,
        tile_mode=tile_mode,
        export_format=export_format,
        quality=quality
    )

    # Validate paths through safe_path
    valid_in = safe_path(raw_input_path, allow_outside_base=True)
    valid_out = safe_path(raw_output_dir, allow_outside_base=True)

    if not valid_in or not valid_out:
        error_msg = "Invalid or restricted input/output path"
        logger.error(f"Security rejection for job {job.get('filename')}: input={raw_input_path}, output={raw_output_dir}")
        job_logger.log_error(job_id, error_msg)
        job_mgr.transition_status(job_id, STATUS_FAILED, error_message=error_msg, force=True)
        with jobs_lock:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['raw_status'] = STATUS_FAILED
            jobs[job_id]['error'] = error_msg
            jobs[job_id]['start_time'] = time.time()
            jobs[job_id]['end_time'] = time.time()
            jobs[job_id]['elapsed'] = 0.0
        broadcast_event('job_error', {
            'job_id': job_id,
            'filename': job.get('filename', 'unknown'),
            'status': 'error',
            'elapsed': 0.0,
            'error': error_msg,
            'is_oom': False
        })
        save_history_entry({
            'job_id': job_id,
            'filename': job.get('filename', 'unknown'),
            'status': 'error',
            'input_path': str(raw_input_path),
            'output_path': '',
            'rel_input_path': None,
            'rel_output_path': None,
            'tile_size': tile_used,
            'start_time': datetime.now().isoformat(),
            'end_time': datetime.now().isoformat(),
            'elapsed_seconds': 0.0,
            'error': error_msg,
            'input_size': 0,
            'output_size': 0,
            'input_dimensions': '',
            'output_dimensions': ''
        })
        return False

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
        broadcast_event('job_error', {
            'job_id': job_id,
            'filename': job.get('filename', 'unknown'),
            'status': 'error',
            'elapsed': 0.0,
            'error': error_msg,
            'is_oom': False
        })
        save_history_entry({
            'job_id': job_id,
            'filename': job.get('filename', 'unknown'),
            'status': 'error',
            'input_path': str(raw_input_path),
            'output_path': '',
            'rel_input_path': None,
            'rel_output_path': None,
            'tile_size': tile_used,
            'start_time': datetime.now().isoformat(),
            'end_time': datetime.now().isoformat(),
            'elapsed_seconds': 0.0,
            'error': error_msg,
            'input_size': 0,
            'output_size': 0,
            'input_dimensions': '',
            'output_dimensions': ''
        })
        return False

    # Deep verification of image before launching inference
    job_logger.log_step(job_id, "VALIDATING", "Validating image integrity and format")
    job_mgr.transition_status(job_id, STATUS_VALIDATING, force=True)
    is_valid_img, val_reason, (w, h), _ = validate_image_file(valid_in, max_pixels=INPUT_MAX_PIXELS, role="input")
    if not is_valid_img:
        error_msg = f"Invalid image: {val_reason}"
        logger.error(f"Image verification failed for {valid_in.name}: {val_reason}")
        job_logger.log_error(job_id, error_msg)
        job_mgr.schedule_retry(job_id, error_msg)  # Non-retryable -> FAILED
        with jobs_lock:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['raw_status'] = STATUS_FAILED
            jobs[job_id]['error'] = error_msg
            jobs[job_id]['start_time'] = time.time()
            jobs[job_id]['end_time'] = time.time()
            jobs[job_id]['elapsed'] = 0.0
        broadcast_event('job_error', {
            'job_id': job_id,
            'filename': job.get('filename', 'unknown'),
            'status': 'error',
            'elapsed': 0.0,
            'error': error_msg,
            'is_oom': False
        })
        save_history_entry({
            'job_id': job_id,
            'filename': job.get('filename', 'unknown'),
            'status': 'error',
            'input_path': str(valid_in),
            'output_path': '',
            'rel_input_path': None,
            'rel_output_path': None,
            'tile_size': tile_used,
            'start_time': datetime.now().isoformat(),
            'end_time': datetime.now().isoformat(),
            'elapsed_seconds': 0.0,
            'error': error_msg,
            'input_size': valid_in.stat().st_size if valid_in.exists() else 0,
            'output_size': 0,
            'input_dimensions': f"{w}x{h}" if w and h else "",
            'output_dimensions': ''
        })
        return False

    # Pre-inference Output Resource Safety Check (Package 2.1)
    scale_factor = 4
    proj_w = w * scale_factor
    proj_h = h * scale_factor
    safe, res_error = check_output_resource_safety(proj_w, proj_h, valid_out, scale=scale_factor)
    if not safe:
        error_msg = res_error
        logger.error(f"Pre-inference resource check failed for job {job_id} ({valid_in.name}): {error_msg}")
        job_logger.log_error(job_id, error_msg)
        job_mgr.schedule_retry(job_id, error_msg)  # Non-retryable -> transitions to FAILED
        with jobs_lock:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['raw_status'] = STATUS_FAILED
            jobs[job_id]['error'] = error_msg
            jobs[job_id]['start_time'] = time.time()
            jobs[job_id]['end_time'] = time.time()
            jobs[job_id]['elapsed'] = 0.0
        broadcast_event('job_error', {
            'job_id': job_id,
            'filename': job.get('filename', 'unknown'),
            'status': 'error',
            'elapsed': 0.0,
            'error': error_msg,
            'is_oom': False
        })
        save_history_entry({
            'job_id': job_id,
            'filename': job.get('filename', 'unknown'),
            'status': 'error',
            'input_path': str(valid_in),
            'output_path': '',
            'rel_input_path': None,
            'rel_output_path': None,
            'tile_size': tile_used,
            'start_time': datetime.now().isoformat(),
            'end_time': datetime.now().isoformat(),
            'elapsed_seconds': 0.0,
            'error': error_msg,
            'input_size': valid_in.stat().st_size if valid_in.exists() else 0,
            'output_size': 0,
            'input_dimensions': f"{w}x{h}" if w and h else "",
            'output_dimensions': ''
        })
        return False

    input_path = valid_in
    out_dir = valid_out
    out_dir.mkdir(parents=True, exist_ok=True)

    job_mgr.transition_status(job_id, STATUS_PROCESSING, force=True)
    with jobs_lock:
        jobs[job_id]['status'] = 'processing'
        jobs[job_id]['raw_status'] = STATUS_PROCESSING
        jobs[job_id]['start_time'] = time.time()

    broadcast_event('job_start', {
        'job_id': job_id,
        'filename': job['filename'],
        'status': 'processing'
    })

    worker = get_ai_worker(base_dir=BASE_DIR)

    def on_progress(pct, detail):
        job_logger.log_progress(job_id, pct, detail)
        job_mgr.update_job_progress(job_id, pct, detail)
        broadcast_event('job_progress', {
            'job_id': job_id,
            'filename': job['filename'],
            'status': 'processing',
            'progress_pct': pct,
            'detail': detail
        })

    def is_cancelled_check():
        evt = _job_cancel_events.get(job_id)
        return (evt is not None and evt.is_set()) or cancel_flag.is_set() or job_mgr.is_cancellation_requested(job_id)

    # Log GPU status
    try:
        gpu_info = get_gpu_info()
        job_logger.log_gpu_info(
            job_id,
            gpu_info.get('gpu_name', 'Unknown'),
            gpu_info.get('used_gb', 0.0),
            gpu_info.get('total_gb', 0.0)
        )
    except Exception:
        pass

    logger.info(f"Processing in-memory: {job['filename']} (mode={tile_mode}, tile={tile_size}, format={export_format})")
    max_retries = int(APP_CONFIG.get("reliability", {}).get("max_retries", 3))
    current_attempt = 0
    error_msg = None
    is_oom = False
    tile_used = tile_size
    output_dimensions = ""
    output_size = 0
    expected_output = None
    elapsed = 0.0

    while current_attempt <= max_retries:
        if is_cancelled_check():
            error_msg = "Job cancelled by user request"
            job_logger.log_cancelled(job_id, error_msg)
            job_mgr.transition_status(job_id, STATUS_CANCELLED)
            st_time = time.time()
            with jobs_lock:
                jobs[job_id]['status'] = 'cancelled'
                jobs[job_id]['raw_status'] = STATUS_CANCELLED
                jobs[job_id]['error'] = error_msg
                jobs[job_id]['end_time'] = time.time()
                st_time = jobs[job_id].get('start_time') or st_time
                elapsed = time.time() - st_time
            broadcast_event('job_error', {
                'job_id': job_id,
                'filename': job['filename'],
                'status': 'cancelled',
                'error': error_msg
            })
            save_history_entry({
                'job_id': job_id,
                'filename': job['filename'],
                'status': 'cancelled',
                'input_path': str(input_path),
                'output_path': '',
                'rel_input_path': None,
                'rel_output_path': None,
                'tile_size': tile_used,
                'start_time': datetime.fromtimestamp(st_time).isoformat(),
                'end_time': datetime.now().isoformat(),
                'elapsed_seconds': round(elapsed, 1),
                'error': error_msg,
                'input_size': input_path.stat().st_size if input_path.exists() else 0,
                'output_size': 0,
                'input_dimensions': job.get('dimensions', ''),
                'output_dimensions': ''
            })
            return False

        current_attempt += 1
        start_time = time.time()
        try:
            res = worker.process_image(
                input_path=input_path,
                output_dir=out_dir,
                tile_mode=tile_mode,
                requested_tile=tile_size if tile_mode != 'auto' else None,
                export_format=export_format,
                quality=quality,
                job_id=job_id,
                progress_callback=on_progress,
                is_cancelled_func=is_cancelled_check
            )
            expected_output = Path(res['output_path'])
            elapsed = res['elapsed_seconds']
            tile_used = res['tile_used']
            output_dimensions = res['output_dimensions']
            output_size = res['output_size']
            is_oom = res['oom_recovered']
            if is_oom:
                job_logger.log_oom_recovery(job_id, tile_size, tile_used)
            error_msg = None
            break
        except Exception as e:
            error_msg = str(e)
            elapsed = time.time() - start_time
            if "cancelled" in error_msg.lower() or is_cancelled_check():
                job_logger.log_cancelled(job_id, error_msg)
                job_mgr.transition_status(job_id, STATUS_CANCELLED)
                st_time = time.time()
                with jobs_lock:
                    jobs[job_id]['status'] = 'cancelled'
                    jobs[job_id]['raw_status'] = STATUS_CANCELLED
                    jobs[job_id]['error'] = error_msg
                    jobs[job_id]['end_time'] = time.time()
                    st_time = jobs[job_id].get('start_time') or st_time
                broadcast_event('job_error', {
                    'job_id': job_id,
                    'filename': job['filename'],
                    'status': 'cancelled',
                    'error': error_msg
                })
                save_history_entry({
                    'job_id': job_id,
                    'filename': job['filename'],
                    'status': 'cancelled',
                    'input_path': str(input_path),
                    'output_path': '',
                    'rel_input_path': None,
                    'rel_output_path': None,
                    'tile_size': tile_used,
                    'start_time': datetime.fromtimestamp(st_time).isoformat(),
                    'end_time': datetime.now().isoformat(),
                    'elapsed_seconds': round(elapsed, 1),
                    'error': error_msg,
                    'input_size': input_path.stat().st_size if input_path.exists() else 0,
                    'output_size': 0,
                    'input_dimensions': job.get('dimensions', ''),
                    'output_dimensions': ''
                })
                return False

            is_oom = ('out of memory' in error_msg.lower() or 'cuda out of memory' in error_msg.lower())
            job_logger.log_error(job_id, f"Inference attempt {current_attempt} failed: {error_msg}", exc=e)

            should_retry, new_retries, reason = job_mgr.schedule_retry(job_id, error_msg, is_oom=is_oom)
            if should_retry and current_attempt <= max_retries:
                backoff_base = float(APP_CONFIG.get("reliability", {}).get("retry_backoff_base", 2.0))
                backoff_time = min(4.0, (backoff_base ** (new_retries - 1)))
                job_logger.log_retry(job_id, new_retries, max_retries, f"{reason}. Waiting {backoff_time:.1f}s backoff.")
                with jobs_lock:
                    jobs[job_id]['status'] = 'retry'
                    jobs[job_id]['raw_status'] = STATUS_RETRY_WAIT
                    jobs[job_id]['retry_count'] = new_retries
                    jobs[job_id]['error'] = error_msg
                time.sleep(backoff_time)

                # Recheck cancellation after retry backoff before proceeding to next attempt
                if is_cancelled_check():
                    cancel_reason = "Job cancelled during retry backoff"
                    job_logger.log_cancelled(job_id, cancel_reason)
                    job_mgr.transition_status(job_id, STATUS_CANCELLED)
                    st_time = time.time()
                    with jobs_lock:
                        jobs[job_id]['status'] = 'cancelled'
                        jobs[job_id]['raw_status'] = STATUS_CANCELLED
                        jobs[job_id]['error'] = cancel_reason
                        jobs[job_id]['end_time'] = time.time()
                        st_time = jobs[job_id].get('start_time') or st_time
                    broadcast_event('job_error', {
                        'job_id': job_id,
                        'filename': job['filename'],
                        'status': 'cancelled',
                        'error': cancel_reason
                    })
                    save_history_entry({
                        'job_id': job_id,
                        'filename': job['filename'],
                        'status': 'cancelled',
                        'input_path': str(input_path),
                        'output_path': '',
                        'rel_input_path': None,
                        'rel_output_path': None,
                        'tile_size': tile_used,
                        'start_time': datetime.fromtimestamp(st_time).isoformat(),
                        'end_time': datetime.now().isoformat(),
                        'elapsed_seconds': round(elapsed, 1),
                        'error': cancel_reason,
                        'input_size': input_path.stat().st_size if input_path.exists() else 0,
                        'output_size': 0,
                        'input_dimensions': job.get('dimensions', ''),
                        'output_dimensions': ''
                    })
                    return False

                job_mgr.transition_status(job_id, STATUS_PROCESSING)
                with jobs_lock:
                    jobs[job_id]['status'] = 'processing'
                    jobs[job_id]['raw_status'] = STATUS_PROCESSING
                continue
            else:
                break

    output_found = bool(expected_output and expected_output.is_file())
    if not output_found and error_msg is None:
        error_msg = "Output file not found after processing."

    # Validate output image if output file was created
    if output_found and error_msg is None:
        valid, val_reason, (out_w, out_h), fmt = validate_image_file(expected_output, max_pixels=OUTPUT_MAX_PIXELS, role="output")
        if not valid:
            error_msg = f"Generated output validation failed: {val_reason}"
            logger.error(f"Output verification failed for job {job_id} ({expected_output}): {val_reason}")
            try:
                expected_output.unlink(missing_ok=True)
            except Exception:
                pass
            expected_output = None
            output_found = False

    # Update state
    if error_msg:
        job_logger.log_error(job_id, error_msg)
        job_mgr.transition_status(job_id, STATUS_FAILED, error_message=error_msg)
        with jobs_lock:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['raw_status'] = STATUS_FAILED
            jobs[job_id]['error'] = error_msg
            jobs[job_id]['output_path'] = None
            jobs[job_id]['is_oom'] = is_oom
            jobs[job_id]['end_time'] = time.time()
            jobs[job_id]['elapsed'] = round(elapsed, 1)
            jobs[job_id]['tile_size'] = tile_used
        status = 'error'
        event_type = 'job_error'
    else:
        dim_str = f"{out_w}x{out_h}"
        out_bytes = expected_output.stat().st_size
        job_mgr.transition_status(
            job_id,
            STATUS_COMPLETED,
            output_path=str(expected_output),
            output_dimensions=dim_str,
            output_size=out_bytes,
            elapsed_seconds=round(elapsed, 1),
            tile_used=tile_used
        )
        job_logger.log_complete(job_id, elapsed, str(expected_output), dim_str, out_bytes, tile_used)
        with jobs_lock:
            jobs[job_id]['status'] = 'completed'
            jobs[job_id]['raw_status'] = STATUS_COMPLETED
            jobs[job_id]['output_path'] = str(expected_output)
            jobs[job_id]['output_dimensions'] = dim_str
            jobs[job_id]['output_size'] = out_bytes
            jobs[job_id]['end_time'] = time.time()
            jobs[job_id]['elapsed'] = round(elapsed, 1)
            jobs[job_id]['tile_size'] = tile_used
            jobs[job_id]['error'] = None
        status = 'completed'
        event_type = 'job_complete'

    broadcast_event(event_type, {
        'job_id': job_id,
        'filename': job['filename'],
        'status': status,
        'elapsed': round(elapsed, 1),
        'error': error_msg,
        'is_oom': is_oom,
        'output_dimensions': jobs[job_id].get('output_dimensions', ''),
        'output_size': jobs[job_id].get('output_size', 0)
    })

    with jobs_lock:
        j = jobs[job_id]

    rel_in = None
    try:
        rel_in = str(Path(j['input_path']).resolve().relative_to(BASE_DIR)).replace('\\', '/')
    except Exception:
        pass

    rel_out = None
    try:
        out_p = j.get('output_path')
        if out_p:
            rel_out = str(Path(out_p).resolve().relative_to(BASE_DIR)).replace('\\', '/')
    except Exception:
        pass

    save_history_entry({
        'job_id': job_id,
        'filename': j['filename'],
        'status': status,
        'input_path': j['input_path'],
        'output_path': j.get('output_path', ''),
        'rel_input_path': rel_in,
        'rel_output_path': rel_out,
        'tile_size': tile_used,
        'start_time': datetime.fromtimestamp(j['start_time']).isoformat(),
        'end_time': datetime.now().isoformat(),
        'elapsed_seconds': round(elapsed, 1),
        'error': error_msg,
        'input_size': j.get('file_size', 0),
        'output_size': j.get('output_size', 0),
        'input_dimensions': j.get('dimensions', ''),
        'output_dimensions': j.get('output_dimensions', '')
    })

    return not bool(error_msg)

def processing_worker():
    """Background worker that sequentially processes jobs from processing_queue."""
    while True:
        try:
            job_data = processing_queue.get(block=True)
        except Exception as e:
            logger.error(f"Error getting item from processing_queue: {e}")
            break

        if job_data is None:
            # Sentinel shutdown signal
            processing_queue.task_done()
            logger.info("Processing worker received shutdown sentinel; exiting cleanly.")
            break

        try:
            is_processing.set()
            job_ids = job_data.get('job_ids', [])
            tile_size = job_data.get('tile_size', 128)
            tile_mode = job_data.get('tile_mode', 'auto')
            export_format = job_data.get('export_format', 'auto')
            quality = job_data.get('quality', 95)
            out_dir = job_data.get('output_dir', session_output_dir)

            completed = 0
            errors = 0
            cancelled = False

            for i, job_id in enumerate(job_ids):
                evt = _job_cancel_events.get(job_id)
                if cancel_flag.is_set() or (evt is not None and evt.is_set()) or job_mgr.is_cancellation_requested(job_id):
                    job_mgr.cancel_all_pending()
                    with jobs_lock:
                        for remaining_id in job_ids[i:]:
                            if remaining_id in jobs and jobs[remaining_id]['status'] in ('pending', 'retry'):
                                jobs[remaining_id]['status'] = 'cancelled'
                                jobs[remaining_id]['raw_status'] = STATUS_CANCELLED
                    cancelled = True
                    break

                with jobs_lock:
                    if job_id not in jobs:
                        continue
                    job = dict(jobs[job_id])
                    if job['status'] not in ('pending', 'retry'):
                        continue

                job['tile_size'] = tile_size
                job['tile_mode'] = tile_mode
                job['export_format'] = export_format
                job['quality'] = quality
                job['output_dir'] = out_dir

                success = process_single_image(job)
                if success:
                    completed += 1
                else:
                    errors += 1

            broadcast_event('queue_complete', {
                'total': len(job_ids),
                'completed': completed,
                'errors': errors,
                'cancelled': cancelled
            })

            if cancelled:
                cancel_flag.clear()

        except Exception as e:
            logger.error(f"Worker exception during batch execution: {e}", exc_info=True)
        finally:
            is_processing.clear()
            processing_queue.task_done()

worker_thread = threading.Thread(target=processing_worker, daemon=True)
worker_thread.start()

# ═══════════════════════════════════════════════════════════════
# API Routes
# ═══════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status')
def api_status():
    return jsonify(system_status)

@app.route('/api/config')
def api_config():
    """Sanitized config endpoint."""
    return jsonify({
        "server": APP_CONFIG["server"],
        "directories": {
            "default_input_dir": str(UPLOAD_DIR),
            "default_output_dir": str(OUTPUT_DIR),
            "exports_dir": str(EXPORTS_DIR),
        },
        "inference": APP_CONFIG["inference"],
        "ui": APP_CONFIG.get("ui", {
            "default_language": "vi",
            "supported_languages": ["vi", "en"],
            "default_view_mode": "grid",
            "page_size": 50
        }),
        "security": {
            "max_file_size_mb": APP_CONFIG["security"]["max_file_size_mb"],
            "max_pixels": MAX_PIXELS,
            "upload_max_pixels": UPLOAD_MAX_PIXELS,
            "input_max_pixels": INPUT_MAX_PIXELS,
            "output_max_pixels": OUTPUT_MAX_PIXELS,
            "allowed_extensions": list(ALLOWED_EXTENSIONS)
        }
    })

@app.route('/api/storage-info')
def api_storage_info():
    """Returns directory sizes and file counts for storage auditing."""
    def get_stats(folder_path):
        p = Path(folder_path)
        if not p.is_dir():
            return {"count": 0, "bytes": 0, "human": "0 B"}
        files = [f for f in p.rglob("*") if f.is_file()]
        total_bytes = sum(f.stat().st_size for f in files)
        return {
            "count": len(files),
            "bytes": total_bytes,
            "human": human_size(total_bytes)
        }

    return jsonify({
        "uploads": get_stats(UPLOAD_DIR),
        "output": get_stats(OUTPUT_DIR),
        "thumbnails": get_stats(THUMBNAIL_DIR),
        "logs": get_stats(LOG_DIR),
        "weights": get_stats(BASE_DIR / "weights")
    })

@app.route('/api/upload', methods=['POST'])
def api_upload():
    if 'files' not in request.files:
        return jsonify({'error': 'No files provided'}), 400

    files = request.files.getlist('files')
    results = []
    errors = []
    summary = {
        'total': len(files),
        'valid': 0,
        'duplicates': 0,
        'unsupported': 0,
        'too_large': 0,
        'corrupt': 0
    }
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    for f in files:
        if not f or not f.filename:
            continue

        raw_filename = Path(f.filename).name  # Prevent directory injection in filename
        if raw_filename.startswith('.') or raw_filename.lower() in BLOCKED_SYSTEM_FILES:
            summary['unsupported'] += 1
            errors.append({'filename': raw_filename, 'reason': 'Blocked system or hidden file', 'category': 'unsupported'})
            continue

        base_name = Path(raw_filename).stem
        ext = Path(raw_filename).suffix.lower()

        if ext not in ALLOWED_EXTENSIONS:
            summary['unsupported'] += 1
            errors.append({'filename': raw_filename, 'reason': f"Extension '{ext}' is not allowed", 'category': 'unsupported'})
            continue

        # Prevent overwriting while tracking filename collisions
        dest_filename = raw_filename
        dest_path = UPLOAD_DIR / dest_filename
        if dest_path.exists():
            counter = 1
            while True:
                dest_filename = f"{base_name}_{counter}{ext}"
                dest_path = UPLOAD_DIR / dest_filename
                if not dest_path.exists():
                    break
                counter += 1

        f.save(str(dest_path))

        # Deep verification using Pillow with INPUT_MAX_PIXELS (allows high-res & reprocessed images)
        res = validate_image_file(dest_path, max_pixels=INPUT_MAX_PIXELS, role="input")
        is_valid, reason, (width, height), fmt = res
        category = getattr(res, 'category', 'corrupt')
        if not is_valid:
            logger.warning(f"Rejected uploaded file {dest_filename} [{category}]: {reason}")
            if category == 'too_large':
                summary['too_large'] += 1
            elif category == 'unsupported':
                summary['unsupported'] += 1
            else:
                summary['corrupt'] += 1
            errors.append({'filename': raw_filename, 'reason': reason, 'category': category})
            try:
                dest_path.unlink(missing_ok=True)
            except Exception:
                pass
            continue

        file_size = dest_path.stat().st_size

        # Check for duplicate source files across current jobs
        existing_dup = None
        with jobs_lock:
            for j in jobs.values():
                if j.get('input_path') == str(dest_path):
                    existing_dup = j
                    break
                if (j.get('filename') == raw_filename or j.get('filename') == dest_filename) and j.get('file_size') == file_size:
                    # Compare content hash if size matches
                    try:
                        p_existing = Path(j.get('input_path', ''))
                        if p_existing.is_file() and p_existing.stat().st_size == file_size:
                            h_dest = hashlib.sha256(dest_path.read_bytes()).hexdigest()
                            h_existing = hashlib.sha256(p_existing.read_bytes()).hexdigest()
                            if h_dest == h_existing:
                                existing_dup = j
                                break
                    except Exception:
                        existing_dup = j
                        break

        if existing_dup:
            summary['duplicates'] += 1
            # Clean up redundant duplicate staged file to prevent disk clutter
            try:
                dest_path.unlink(missing_ok=True)
            except Exception:
                pass

            results.append({
                'job_id': existing_dup['id'],
                'filename': existing_dup.get('filename', dest_filename),
                'width': existing_dup.get('width', width),
                'height': existing_dup.get('height', height),
                'dimensions': existing_dup.get('dimensions', f"{width}x{height}"),
                'file_size': existing_dup.get('file_size', file_size),
                'file_size_human': existing_dup.get('file_size_human', human_size(file_size)),
                'status': existing_dup.get('status', 'pending'),
                'is_duplicate': True,
                'thumbnail_url': f"/api/thumbnail/{existing_dup['id']}",
                'source': existing_dup.get('source', 'upload')
            })
            summary['valid'] += 1
            continue

        job_id = str(uuid.uuid4())
        thumb_path = THUMBNAIL_DIR / f"{job_id}.jpg"
        make_thumbnail(dest_path, thumb_path)

        job_entry = {
            'id': job_id,
            'filename': dest_filename,
            'input_path': str(dest_path),
            'output_path': None,
            'width': width,
            'height': height,
            'dimensions': f"{width}x{height}",
            'file_size': file_size,
            'file_size_human': human_size(file_size),
            'status': 'pending',
            'raw_status': STATUS_QUEUED,
            'retry_count': 0,
            'max_retries': 3,
            'error': None,
            'is_oom': False,
            'start_time': None,
            'end_time': None,
            'elapsed': None,
            'tile_size': APP_CONFIG["inference"]["default_tile"],
            'thumbnail_url': f"/api/thumbnail/{job_id}",
            'is_duplicate': False,
            'source': 'upload'
        }

        try:
            job_mgr.add_job(
                job_id=job_id,
                filename=dest_filename,
                input_path=str(dest_path),
                dimensions=f"{width}x{height}",
                file_size=file_size,
                max_retries=3
            )
        except Exception as e:
            logger.error(f"Failed to record uploaded job in SQLite: {e}")

        with jobs_lock:
            jobs[job_id] = job_entry

        results.append({
            'job_id': job_id,
            'filename': dest_filename,
            'width': width,
            'height': height,
            'dimensions': f"{width}x{height}",
            'file_size': file_size,
            'file_size_human': human_size(file_size),
            'status': 'pending',
            'is_duplicate': False,
            'source': 'upload',
            'thumbnail_url': f"/api/thumbnail/{job_id}"
        })
        summary['valid'] += 1

    logger.info(f"Accepted {len(results)} valid files into {UPLOAD_DIR} (summary: {summary})")
    return jsonify({'status': 'success', 'files': results, 'jobs': results, 'summary': summary, 'errors': errors})

@app.route('/api/select-folder', methods=['POST'])
def api_select_folder():
    data = request.get_json() or {}
    folder_str = data.get('path')
    if not folder_str:
        folder_path = UPLOAD_DIR
        folder_path.mkdir(parents=True, exist_ok=True)
    else:
        folder_path = safe_path(folder_str, allow_outside_base=True)
        if not folder_path:
            return jsonify({'error': 'Invalid or restricted folder path'}), 403

        if not folder_path.is_dir():
            return jsonify({'error': 'Thư mục không tồn tại trên hệ thống.'}), 404

        if not os.access(str(folder_path), os.R_OK):
            return jsonify({'error': 'Không có quyền đọc thư mục này.'}), 403

    results = []
    try:
        entries = sorted(os.listdir(str(folder_path)))
    except PermissionError:
        return jsonify({'error': 'Permission denied'}), 403

    for entry in entries:
        file_path = folder_path / entry
        if not file_path.is_file():
            continue

        # Deep Pillow verification (with INPUT_MAX_PIXELS to allow high-res & reprocessed images)
        is_valid, _, (width, height), _ = validate_image_file(file_path, max_pixels=INPUT_MAX_PIXELS, role="input")
        if not is_valid:
            continue

        file_size = file_path.stat().st_size

        existing_job = None
        with jobs_lock:
            for j in jobs.values():
                if j.get('input_path') == str(file_path):
                    existing_job = j
                    break

        if not existing_job:
            try:
                with job_mgr.get_connection() as conn:
                    cur = conn.execute(
                        "SELECT id FROM jobs WHERE input_path = ? ORDER BY created_at DESC LIMIT 1",
                        (str(file_path),)
                    )
                    row = cur.fetchone()
                    if row:
                        db_job = job_mgr.get_job(row[0])
                        if db_job:
                            existing_job = {
                                'id': db_job['id'],
                                'filename': db_job['filename'],
                                'input_path': db_job['input_path'],
                                'output_path': db_job.get('output_path'),
                                'width': width,
                                'height': height,
                                'dimensions': db_job.get('dimensions', f"{width}x{height}"),
                                'file_size': db_job.get('file_size', file_size),
                                'file_size_human': human_size(db_job.get('file_size', file_size)),
                                'status': 'completed' if db_job['status'] == STATUS_COMPLETED else ('error' if db_job['status'] == STATUS_FAILED else 'pending'),
                                'raw_status': db_job['status'],
                                'retry_count': db_job.get('retry_count', 0),
                                'max_retries': db_job.get('max_retries', 3),
                                'error': db_job.get('error_message'),
                                'is_oom': bool(db_job.get('is_oom', 0)),
                                'start_time': db_job.get('started_at'),
                                'end_time': db_job.get('completed_at'),
                                'elapsed': db_job.get('elapsed_seconds'),
                                'tile_size': db_job.get('tile_used') or APP_CONFIG["inference"]["default_tile"],
                                'thumbnail_url': f"/api/thumbnail/{db_job['id']}",
                                'source': 'folder',
                                'output_dimensions': db_job.get('output_dimensions', ''),
                                'output_size': db_job.get('output_size', 0)
                            }
                            with jobs_lock:
                                jobs[db_job['id']] = existing_job
            except Exception as e:
                logger.warning(f"Error checking SQLite existing job for {file_path}: {e}")

        if existing_job:
            job_id = existing_job['id']
            thumb_path = THUMBNAIL_DIR / f"{job_id}.jpg"
            if not thumb_path.is_file():
                make_thumbnail(file_path, thumb_path)
            results.append({
                'job_id': job_id,
                'filename': existing_job['filename'],
                'width': existing_job['width'],
                'height': existing_job['height'],
                'dimensions': existing_job.get('dimensions', ''),
                'file_size': existing_job.get('file_size', file_size),
                'file_size_human': existing_job.get('file_size_human', human_size(file_size)),
                'status': existing_job['status'],
                'elapsed': existing_job.get('elapsed'),
                'error': existing_job.get('error'),
                'is_oom': existing_job.get('is_oom', False),
                'thumbnail_url': f"/api/thumbnail/{job_id}",
                'output_dimensions': existing_job.get('output_dimensions', ''),
                'output_size': existing_job.get('output_size', 0),
                'source': existing_job.get('source', 'folder')
            })
        else:
            job_id = str(uuid.uuid4())
            thumb_path = THUMBNAIL_DIR / f"{job_id}.jpg"
            make_thumbnail(file_path, thumb_path)

            job_entry = {
                'id': job_id,
                'filename': entry,
                'input_path': str(file_path),
                'output_path': None,
                'width': width,
                'height': height,
                'dimensions': f"{width}x{height}",
                'file_size': file_size,
                'file_size_human': human_size(file_size),
                'status': 'pending',
                'raw_status': STATUS_QUEUED,
                'retry_count': 0,
                'max_retries': 3,
                'error': None,
                'is_oom': False,
                'start_time': None,
                'end_time': None,
                'elapsed': None,
                'tile_size': APP_CONFIG["inference"]["default_tile"],
                'thumbnail_url': f"/api/thumbnail/{job_id}",
                'source': 'folder'
            }

            try:
                job_mgr.add_job(
                    job_id=job_id,
                    filename=entry,
                    input_path=str(file_path),
                    dimensions=f"{width}x{height}",
                    file_size=file_size,
                    max_retries=3
                )
            except Exception as e:
                logger.error(f"Failed to record folder job in SQLite: {e}")

            with jobs_lock:
                jobs[job_id] = job_entry

            results.append({
                'job_id': job_id,
                'filename': entry,
                'width': width,
                'height': height,
                'dimensions': f"{width}x{height}",
                'file_size': file_size,
                'file_size_human': human_size(file_size),
                'status': 'pending',
                'elapsed': None,
                'error': None,
                'is_oom': False,
                'thumbnail_url': f"/api/thumbnail/{job_id}",
                'output_dimensions': '',
                'output_size': 0,
                'source': 'folder'
            })

    logger.info(f"Loaded {len(results)} valid images from {folder_path}")
    return jsonify({
        'status': 'success',
        'files': results,
        'jobs': results,
        'folder': str(folder_path),
        'total_valid': len(results),
        'valid_count': len(results),
        'count': len(results),
        'source': 'folder'
    })

@app.route('/api/thumbnail/<job_id>')
def api_thumbnail(job_id):
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({'error': 'Invalid ID'}), 400

    thumb_path = THUMBNAIL_DIR / f"{job_id}.jpg"
    if thumb_path.is_file():
        return send_file(str(thumb_path), mimetype='image/jpeg')
    return jsonify({'error': 'Thumbnail not found'}), 404

@app.route('/api/process', methods=['POST'])
def api_process():
    data = request.get_json()
    if not data or 'job_ids' not in data:
        return jsonify({'error': 'No job IDs provided'}), 400

    job_ids = data['job_ids']
    raw_tile = data.get('tile_size', 'auto')
    tile_mode = 'auto'
    tile_size = APP_CONFIG["inference"]["default_tile"]

    if str(raw_tile).lower() == 'auto':
        tile_mode = 'auto'
    else:
        try:
            val = int(raw_tile)
            if val in APP_CONFIG["inference"]["supported_tiles"]:
                tile_size = val
                tile_mode = 'manual'
        except (ValueError, TypeError):
            tile_mode = 'auto'

    export_format = data.get('export_format', 'auto')
    try:
        quality = int(data.get('quality', 95))
        quality = max(10, min(100, quality))
    except (ValueError, TypeError):
        quality = 95

    output_dir_str = data.get('output_dir', None)
    if output_dir_str:
        out_path = safe_path(output_dir_str, allow_outside_base=True)
        if not out_path:
            return jsonify({'error': 'Invalid output directory'}), 403
        out_path.mkdir(parents=True, exist_ok=True)
        target_output_dir = out_path
    else:
        target_output_dir = session_output_dir

    valid_ids = []
    with jobs_lock:
        for jid in job_ids:
            if jid in jobs and jobs[jid]['status'] in ('pending', 'error', 'cancelled', 'retry'):
                jobs[jid]['status'] = 'pending'
                jobs[jid]['raw_status'] = STATUS_QUEUED
                jobs[jid]['error'] = None
                valid_ids.append(jid)
                try:
                    job_mgr.reset_for_retry(jid)
                except Exception:
                    pass

    if not valid_ids:
        return jsonify({'error': 'No valid jobs to process'}), 400

    processing_queue.put({
        'job_ids': valid_ids,
        'tile_mode': tile_mode,
        'tile_size': tile_size,
        'export_format': export_format,
        'quality': quality,
        'output_dir': str(target_output_dir)
    })

    logger.info(f"Queued {len(valid_ids)} jobs (mode={tile_mode}, tile={tile_size}, format={export_format}, quality={quality})")
    return jsonify({
        'total_jobs': len(valid_ids),
        'tile_mode': tile_mode,
        'tile_size': tile_size,
        'export_format': export_format,
        'quality': quality
    })

@app.route('/api/progress')
def api_progress():
    def event_stream():
        q = queue.Queue(maxsize=200)
        with sse_lock:
            sse_clients.append(q)
        try:
            with jobs_lock:
                for jid, job in jobs.items():
                    data = json.dumps({
                        'job_id': jid,
                        'filename': job['filename'],
                        'status': job['status'],
                        'elapsed': job.get('elapsed'),
                        'error': job.get('error'),
                        'is_oom': job.get('is_oom', False),
                        'output_dimensions': job.get('output_dimensions', ''),
                        'output_size': job.get('output_size', 0)
                    }, ensure_ascii=False)
                    yield f"event: job_state\ndata: {data}\n\n"

            while True:
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except queue.Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            with sse_lock:
                if q in sse_clients:
                    sse_clients.remove(q)

    return Response(
        stream_with_context(event_stream()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    )

@app.route('/api/cancel', methods=['POST'])
def api_cancel():
    cancel_flag.set()
    job_mgr.cancel_all_pending()
    with _job_cancel_lock:
        for evt in _job_cancel_events.values():
            evt.set()
    logger.info("Safe cancel requested")
    with jobs_lock:
        for j in jobs.values():
            if j['status'] in ('pending', 'retry'):
                j['status'] = 'cancelled'
                j['raw_status'] = STATUS_CANCELLED
        completed = sum(1 for j in jobs.values() if j['status'] == 'completed')
    return jsonify({'cancelled': True, 'completed_count': completed})

@app.route('/api/retry', methods=['POST'])
def api_retry():
    data = request.get_json()
    if not data or 'job_ids' not in data:
        return jsonify({'error': 'No job IDs provided'}), 400

    job_ids = data['job_ids']
    raw_tile = data.get('tile_size', 64)
    tile_mode = 'manual'
    tile_size = 64
    if str(raw_tile).lower() == 'auto':
        tile_mode = 'auto'
    else:
        try:
            val = int(raw_tile)
            if val in APP_CONFIG["inference"]["supported_tiles"]:
                tile_size = val
        except (ValueError, TypeError):
            tile_size = 64

    export_format = data.get('export_format', 'auto')
    try:
        quality = int(data.get('quality', 95))
        quality = max(10, min(100, quality))
    except (ValueError, TypeError):
        quality = 95

    output_dir = data.get('output_dir', str(session_output_dir))

    requeued = []
    with jobs_lock:
        for jid in job_ids:
            if jid in jobs and jobs[jid]['status'] in ('error', 'cancelled', 'retry'):
                reset_ok = job_mgr.reset_for_retry(jid)
                if not reset_ok:
                    logger.warning(f"Could not reset job {jid} in SQLite for retry; skipping in-memory update.")
                    continue
                jobs[jid]['status'] = 'pending'
                jobs[jid]['raw_status'] = STATUS_QUEUED
                jobs[jid]['error'] = None
                jobs[jid]['is_oom'] = False
                jobs[jid]['retry_count'] = 0
                requeued.append(jid)

    if requeued:
        processing_queue.put({
            'job_ids': requeued,
            'tile_mode': tile_mode,
            'tile_size': tile_size,
            'export_format': export_format,
            'quality': quality,
            'output_dir': output_dir
        })

    logger.info(f"Retrying {len(requeued)} jobs (mode={tile_mode}, tile={tile_size})")
    return jsonify({'requeued': len(requeued), 'tile_mode': tile_mode, 'tile_size': tile_size})

@app.route('/api/reprocess', methods=['POST'])
def api_reprocess():
    """
    Allow reprocessing previously processed images:
    Generates a new unique Job ID, preserves historical records & prior outputs,
    and enqueues the image for fresh processing.
    """
    data = request.get_json() or {}
    job_ids = data.get('job_ids', [])
    single_id = data.get('job_id')
    if single_id and single_id not in job_ids:
        job_ids.append(single_id)

    if not job_ids:
        return jsonify({'error': 'No job IDs provided'}), 400

    new_jobs = []
    with jobs_lock:
        for jid in job_ids:
            target = jobs.get(jid)
            if not target:
                db_record = job_mgr.get_job(jid)
                if db_record:
                    target = {
                        'id': db_record['id'],
                        'filename': db_record['filename'],
                        'input_path': db_record['input_path'],
                        'file_size': db_record.get('file_size', 0),
                        'width': 0,
                        'height': 0
                    }

            if not target or not target.get('input_path'):
                continue

            inp_path = Path(target['input_path'])
            if not inp_path.is_file():
                continue

            res = validate_image_file(inp_path, max_pixels=INPUT_MAX_PIXELS, role="input")
            is_valid, reason, (width, height), fmt = res
            if not is_valid:
                continue

            new_jid = str(uuid.uuid4())
            new_thumb = THUMBNAIL_DIR / f"{new_jid}.jpg"
            old_thumb = THUMBNAIL_DIR / f"{jid}.jpg"
            if old_thumb.is_file():
                try:
                    shutil.copy2(str(old_thumb), str(new_thumb))
                except Exception:
                    make_thumbnail(inp_path, new_thumb)
            else:
                make_thumbnail(inp_path, new_thumb)

            file_size = inp_path.stat().st_size
            new_job_entry = {
                'id': new_jid,
                'filename': target['filename'],
                'input_path': str(inp_path),
                'output_path': None,
                'width': width or target.get('width', 0),
                'height': height or target.get('height', 0),
                'dimensions': f"{width}x{height}",
                'file_size': file_size,
                'file_size_human': human_size(file_size),
                'status': 'pending',
                'raw_status': STATUS_QUEUED,
                'retry_count': 0,
                'max_retries': 3,
                'error': None,
                'is_oom': False,
                'start_time': None,
                'end_time': None,
                'elapsed': None,
                'tile_size': data.get('tile_size', APP_CONFIG["inference"]["default_tile"]),
                'thumbnail_url': f"/api/thumbnail/{new_jid}",
                'is_duplicate': False,
                'source': 'reprocess',
                'reprocessed_from': jid
            }

            try:
                job_mgr.add_job(
                    job_id=new_jid,
                    filename=target['filename'],
                    input_path=str(inp_path),
                    dimensions=f"{width}x{height}",
                    file_size=file_size,
                    max_retries=3
                )
            except Exception as e:
                logger.error(f"Failed to record reprocessed job in SQLite: {e}")
                if new_thumb.is_file():
                    try:
                        new_thumb.unlink()
                    except Exception:
                        pass
                continue

            jobs[new_jid] = new_job_entry
            new_jobs.append(new_job_entry)

    if not new_jobs:
        return jsonify({'error': 'No eligible jobs could be reprocessed'}), 400

    auto_process = data.get('auto_process', False)
    if auto_process:
        new_ids = [j['id'] for j in new_jobs]
        raw_tile = data.get('tile_size', 'auto')
        tile_mode = 'auto'
        tile_size = APP_CONFIG["inference"]["default_tile"]
        if str(raw_tile).lower() == 'auto':
            tile_mode = 'auto'
        else:
            try:
                val = int(raw_tile)
                if val in APP_CONFIG["inference"]["supported_tiles"]:
                    tile_size = val
                    tile_mode = 'manual'
            except (ValueError, TypeError):
                tile_mode = 'auto'

        export_format = data.get('export_format', 'auto')
        try:
            quality = int(data.get('quality', 95))
            quality = max(10, min(100, quality))
        except (ValueError, TypeError):
            quality = 95

        output_dir_str = data.get('output_dir', None)
        if output_dir_str:
            out_path = safe_path(output_dir_str, allow_outside_base=True)
            target_out = out_path if out_path else session_output_dir
        else:
            target_out = session_output_dir

        processing_queue.put({
            'job_ids': new_ids,
            'tile_mode': tile_mode,
            'tile_size': tile_size,
            'export_format': export_format,
            'quality': quality,
            'output_dir': str(target_out)
        })

    logger.info(f"Created {len(new_jobs)} reprocessed job(s) (auto_process={auto_process})")
    return jsonify({
        'reprocessed_count': len(new_jobs),
        'jobs': new_jobs
    })

@app.route('/api/result/<job_id>')
def api_result(job_id):
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({'error': 'Invalid ID'}), 400

    with jobs_lock:
        job = jobs.get(job_id)

    if not job or job.get('status') != 'completed' or not job.get('output_path'):
        return jsonify({'error': 'Result not found or job not completed'}), 404

    output_path = Path(job['output_path'])
    if not output_path.is_file():
        return jsonify({'error': 'Output file not found'}), 404

    return send_file(str(output_path))

@app.route('/api/original/<job_id>')
def api_original(job_id):
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({'error': 'Invalid ID'}), 400

    with jobs_lock:
        job = jobs.get(job_id)

    if not job or not job.get('input_path'):
        return jsonify({'error': 'Original not found'}), 404

    input_path = Path(job['input_path'])
    if not input_path.is_file():
        return jsonify({'error': 'Input file not found'}), 404

    return send_file(str(input_path))

@app.route('/api/history')
def api_history():
    return jsonify(load_history())

@app.route('/api/open-folder', methods=['POST'])
def api_open_folder():
    data = request.get_json()
    if not data or 'path' not in data:
        return jsonify({'error': 'No path provided'}), 400

    folder_path = safe_path(data['path'], allow_outside_base=True)
    if not folder_path or not folder_path.is_dir():
        return jsonify({'error': 'Directory not found or access denied'}), 404

    try:
        subprocess.Popen(['explorer', str(folder_path)], creationflags=subprocess.CREATE_NO_WINDOW)
        return jsonify({'opened': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/set-output-dir', methods=['POST'])
def api_set_output_dir():
    global session_output_dir
    data = request.get_json()
    if not data or 'path' not in data:
        return jsonify({'error': 'No path provided'}), 400

    dir_path = safe_path(data['path'], allow_outside_base=True)
    if not dir_path:
        return jsonify({'error': 'Invalid or restricted output directory'}), 403

    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        session_output_dir = dir_path
        logger.info(f"Session output dir updated: {dir_path}")
        return jsonify({'output_dir': str(dir_path)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def is_local_request():
    """Verify if the request originates from localhost / local loopback."""
    remote = request.remote_addr
    # Test client often sets remote_addr to '127.0.0.1' or None
    if remote in ('127.0.0.1', '::1', 'localhost', 'testclient', None):
        return True
    return False

def launch_native_folder_dialog(initial_dir=None, title="Chọn thư mục ảnh - AI Image Studio", timeout=180):
    """
    Launch Windows native Folder Browser Dialog via isolated subprocess.
    Returns dict:
      {"status": "success", "path": str(validated_path)}
      {"status": "cancelled", "message": "..."}
      {"status": "error", "error": "..."}
    """
    script_path = BASE_DIR / "folder_dialog.py"
    if not script_path.is_file():
        return {"status": "error", "error": "Tệp hỗ trợ duyệt thư mục (folder_dialog.py) không tồn tại."}

    cmd = [sys.executable, str(script_path)]
    if initial_dir:
        v_init = safe_path(initial_dir, allow_outside_base=True)
        if v_init and v_init.is_dir():
            cmd.append(str(v_init))
        else:
            cmd.append("")
    else:
        cmd.append("")

    if title:
        cmd.append(str(title))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=timeout,
            shell=False
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"Native folder dialog timed out after {timeout}s")
        return {"status": "error", "error": "Hộp thoại chọn thư mục đã hết thời gian chờ (Timeout)."}
    except Exception as e:
        logger.error(f"Failed to launch native folder dialog subprocess: {e}")
        return {"status": "error", "error": f"Không thể mở hộp thoại chọn thư mục: {str(e)}"}

    if proc.returncode != 0:
        err_msg = proc.stderr.strip() if proc.stderr else f"Exit code {proc.returncode}"
        logger.error(f"Folder dialog subprocess error: {err_msg}")
        return {"status": "error", "error": f"Lỗi hộp thoại: {err_msg}"}

    raw_path = proc.stdout.strip()
    if not raw_path:
        return {"status": "cancelled", "message": "Người dùng đã đóng hoặc hủy hộp thoại chọn thư mục."}

    validated = safe_path(raw_path, allow_outside_base=True)
    if not validated:
        return {"status": "error", "error": "Đường dẫn thư mục không hợp lệ hoặc bị hạn chế bảo mật."}

    if not validated.is_dir():
        return {"status": "error", "error": "Thư mục đã chọn không tồn tại."}

    if not os.access(str(validated), os.R_OK):
        return {"status": "error", "error": "Không có quyền đọc thư mục đã chọn."}

    return {"status": "success", "path": str(validated)}

@app.route('/api/browse-folder', methods=['GET', 'POST'])
def api_browse_folder():
    if request.method == 'POST':
        # Security: only permit local requests to launch native dialog
        if not is_local_request():
            return jsonify({
                'status': 'error',
                'error': 'Chức năng mở hộp thoại thư mục Windows chỉ khả dụng trên máy chủ cục bộ (Localhost). Khi truy cập từ thiết bị khác, vui lòng dùng chức năng tải lên file.',
                'is_remote': True
            }), 403

        data = request.get_json(silent=True) or {}

        # Allow direct path validation/selection if provided (e.g., testing or manual input)
        direct_path = data.get('path')
        if direct_path:
            v_path = safe_path(direct_path, allow_outside_base=True)
            if not v_path:
                return jsonify({'status': 'error', 'error': 'Đường dẫn thư mục không hợp lệ hoặc bị hạn chế bảo mật.'}), 403
            if not v_path.is_dir():
                return jsonify({'status': 'error', 'error': 'Thư mục không tồn tại trên hệ thống.'}), 404
            if not os.access(str(v_path), os.R_OK):
                return jsonify({'status': 'error', 'error': 'Không có quyền đọc thư mục này.'}), 403
            return jsonify({'status': 'success', 'path': str(v_path)})

        # Launch native Windows dialog via subprocess
        initial_dir = data.get('initial_dir')
        title = data.get('title') or "Chọn thư mục ảnh - AI Image Studio"
        result = launch_native_folder_dialog(initial_dir=initial_dir, title=title)

        status_code = 200
        if result.get('status') == 'error':
            status_code = 400
        return jsonify(result), status_code

    # GET: Existing directory browser for backward compatibility / fallback
    path_param = request.args.get('path', '')

    if not path_param:
        drives = []
        for letter in string.ascii_uppercase:
            drive_p = Path(f"{letter}:/")
            if drive_p.exists():
                drives.append(f"{letter}:\\")
        return jsonify({'current': '', 'parent': '', 'folders': drives, 'is_root': True})

    path = safe_path(path_param, allow_outside_base=True)
    if not path or not path.is_dir():
        return jsonify({'error': 'Not a valid directory or access denied'}), 404

    parent = path.parent
    is_root = (parent == path)
    parent_str = '' if is_root else str(parent)

    folders = []
    try:
        for entry in sorted(os.listdir(str(path))):
            full_p = path / entry
            if full_p.is_dir():
                if entry.startswith('.') or entry.startswith('$'):
                    continue
                folders.append(entry)
    except PermissionError:
        pass

    return jsonify({
        'current': str(path),
        'parent': parent_str,
        'folders': folders,
        'is_root': is_root
    })

@app.route('/api/jobs')
def api_jobs():
    status_filter = request.args.get('status')
    search_query = request.args.get('search') or request.args.get('q')
    batch_id = request.args.get('batch_id')
    sort_by = request.args.get('sort_by') or request.args.get('sort')
    order = request.args.get('order', 'ASC')
    page = request.args.get('page')
    limit = request.args.get('limit')

    # If any filtering/sorting/pagination parameter is supplied:
    if any(param is not None for param in [status_filter, search_query, batch_id, sort_by, page, limit]):
        try:
            page_num = max(1, int(page)) if page else 1
        except (ValueError, TypeError):
            page_num = 1
        try:
            limit_num = max(1, int(limit)) if limit else 50
        except (ValueError, TypeError):
            limit_num = 50

        offset = (page_num - 1) * limit_num
        job_rows, total_count = job_mgr.query_jobs(
            status=status_filter,
            search=search_query,
            batch_id=batch_id,
            sort_by=sort_by,
            order=order,
            limit=limit_num,
            offset=offset
        )

        formatted_jobs = []
        with jobs_lock:
            for r in job_rows:
                jid = r['id']
                live = jobs.get(jid, {})
                st = live.get('status', r.get('status', '').lower())
                formatted_jobs.append({
                    'job_id': jid,
                    'filename': r.get('filename') or live.get('filename'),
                    'dimensions': r.get('dimensions') or live.get('dimensions', ''),
                    'file_size': r.get('file_size') or live.get('file_size', 0),
                    'file_size_human': human_size(r.get('file_size') or live.get('file_size', 0)),
                    'status': st,
                    'raw_status': r.get('status'),
                    'retry_count': r.get('retry_count', 0),
                    'max_retries': r.get('max_retries', 3),
                    'error': r.get('error_message') or live.get('error'),
                    'is_oom': bool(r.get('is_oom', 0)) or live.get('is_oom', False),
                    'elapsed': r.get('elapsed_seconds') or live.get('elapsed'),
                    'thumbnail_url': f"/api/thumbnail/{jid}",
                    'output_dimensions': r.get('output_dimensions') or live.get('output_dimensions', ''),
                    'output_size': r.get('output_size') or live.get('output_size', 0),
                    'source': live.get('source') or ('folder' if 'uploads' not in str(r.get('input_path', '')) else 'upload'),
                    'created_at': r.get('created_at'),
                    'completed_at': r.get('completed_at')
                })

        import math
        total_pages = math.ceil(total_count / limit_num) if limit_num > 0 else 1
        return jsonify({
            'jobs': formatted_jobs,
            'total': total_count,
            'page': page_num,
            'limit': limit_num,
            'total_pages': total_pages
        })

    # Default fallback without query parameters: return simple list for 100% backward compatibility
    with jobs_lock:
        result = []
        for jid, job in jobs.items():
            result.append({
                'job_id': jid,
                'filename': job.get('filename', 'unknown'),
                'dimensions': job.get('dimensions', ''),
                'file_size': job.get('file_size', 0),
                'file_size_human': job.get('file_size_human', ''),
                'status': job.get('status', 'pending'),
                'raw_status': job.get('raw_status', job.get('status', 'pending')),
                'retry_count': job.get('retry_count', 0),
                'max_retries': job.get('max_retries', 3),
                'error': job.get('error'),
                'is_oom': job.get('is_oom', False),
                'elapsed': job.get('elapsed'),
                'thumbnail_url': job.get('thumbnail_url', ''),
                'output_dimensions': job.get('output_dimensions', ''),
                'output_size': job.get('output_size', 0),
                'source': job.get('source', 'upload')
            })
    return jsonify(result)

@app.route('/api/export/zip', methods=['GET', 'POST'])
def api_export_zip():
    """Export completed jobs as a streamed ZIP archive without blocking AI worker."""
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        job_ids = data.get('job_ids')
        mode = data.get('mode', 'all' if not job_ids else 'selected')
        batch_id = data.get('batch_id')
    else:
        mode = request.args.get('mode', 'all')
        job_ids_param = request.args.get('job_ids')
        job_ids = [jid.strip() for jid in job_ids_param.split(',') if jid.strip()] if job_ids_param else None
        batch_id = request.args.get('batch_id')

    if mode == 'selected' and not job_ids:
        return jsonify({'error': 'No job IDs provided for selected export'}), 400

    completed_jobs = job_mgr.get_completed_jobs(job_ids=job_ids if mode == 'selected' else None, batch_id=batch_id)
    if not completed_jobs:
        return jsonify({'error': 'No completed jobs found to export'}), 404

    try:
        batch_exporter.cleanup_expired_archives(max_age_seconds=3600)
        res = batch_exporter.create_zip_archive(completed_jobs)
        if not res.get("success"):
            return jsonify({'error': res.get('error', 'Failed to generate ZIP archive')}), 404

        archive_path = Path(res["archive_path"])
        logger.info(f"Created ZIP export: {archive_path.name} ({len(completed_jobs)} jobs)")

        return send_file(
            str(archive_path),
            as_attachment=True,
            download_name=archive_path.name,
            mimetype='application/zip'
        )
    except Exception as e:
        logger.error(f"Error creating ZIP archive: {e}")
        return jsonify({'error': f'Failed to generate ZIP archive: {e}'}), 500

@app.route('/api/export/report', methods=['GET', 'POST'])
def api_export_report():
    """Generate comprehensive CSV or offline HTML report for jobs."""
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        fmt = (data.get('format') or 'csv').lower()
        mode = data.get('mode', 'all')
        job_ids = data.get('job_ids')
        batch_id = data.get('batch_id')
    else:
        fmt = (request.args.get('format') or 'csv').lower()
        mode = request.args.get('mode', 'all')
        job_ids_param = request.args.get('job_ids')
        job_ids = [jid.strip() for jid in job_ids_param.split(',') if jid.strip()] if job_ids_param else None
        batch_id = request.args.get('batch_id')

    if mode == 'selected' and job_ids:
        job_rows = job_mgr.get_jobs_by_ids(job_ids)
    else:
        job_rows = job_mgr.get_all_jobs()

    if not job_rows:
        return jsonify({'error': 'No jobs found to generate report'}), 404

    stats = job_mgr.get_report_statistics(job_ids=job_ids if mode == 'selected' else None, batch_id=batch_id)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    if fmt == 'html':
        content = generate_html_report(job_rows, stats)
        filename = f"image_studio_report_{timestamp}.html"
        return Response(
            content,
            mimetype='text/html; charset=utf-8',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'}
        )
    else:
        content = generate_csv_report(job_rows, stats)
        filename = f"image_studio_report_{timestamp}.csv"
        return Response(
            content,
            mimetype='text/csv; charset=utf-8',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'}
        )

@app.route('/api/jobs/<job_id>')
def api_get_job(job_id):
    """Retrieve full job details by ID."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({'error': 'Invalid job ID format'}), 400

    job = job_mgr.get_job(job_id)
    if not job:
        with jobs_lock:
            job = jobs.get(job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)

@app.route('/api/jobs/<job_id>/log')
def api_get_job_log(job_id):
    """Retrieve structured per-job log."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({'error': 'Invalid job ID format'}), 400

    log_content = job_logger.get_job_log(job_id)
    log_path = job_logger._get_log_path(job_id)
    return jsonify({
        'job_id': job_id,
        'log': log_content,
        'exists': log_path.is_file(),
        'size_bytes': log_path.stat().st_size if log_path.is_file() else 0
    })

@app.route('/api/database/backup', methods=['POST'])
def api_database_backup():
    """Create a consistent SQLite backup snapshot."""
    try:
        backup_path = job_mgr.backup_database()
        return jsonify({
            'success': True,
            'backup_path': str(backup_path),
            'size_bytes': backup_path.stat().st_size
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/remove-job', methods=['POST'])
def api_remove_job():
    data = request.get_json()
    if not data or 'job_id' not in data:
        return jsonify({'error': 'No job ID'}), 400

    job_id = data['job_id']
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404
        if job['status'] == 'processing':
            return jsonify({'error': 'Cannot remove while processing'}), 400
        del jobs[job_id]

    job_mgr.delete_job(job_id)
    thumb = THUMBNAIL_DIR / f"{job_id}.jpg"
    thumb.unlink(missing_ok=True)

    return jsonify({'removed': True})

@app.route('/api/clear-jobs', methods=['POST'])
def api_clear_jobs():
    """Clear jobs list without deleting input images."""
    removed = 0
    with jobs_lock:
        to_remove = [jid for jid, j in jobs.items() if j['status'] != 'processing']
        for jid in to_remove:
            jobs.pop(jid)
            removed += 1
            thumb = THUMBNAIL_DIR / f"{jid}.jpg"
            thumb.unlink(missing_ok=True)

    job_mgr.clear_finished_jobs()
    return jsonify({'removed': removed})

@app.route('/api/default-input-dir')
def api_default_input_dir():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return jsonify({'input_dir': str(UPLOAD_DIR)})

@app.route('/api/default-output-dir')
def api_default_output_dir():
    session_output_dir.mkdir(parents=True, exist_ok=True)
    return jsonify({'output_dir': str(session_output_dir)})

@app.route('/api/gpu-stats')
def api_gpu_stats():
    """Return real-time GPU and VRAM metrics + Worker status."""
    worker = get_ai_worker(base_dir=BASE_DIR)
    return jsonify(worker.get_status())

@app.route('/api/preview/<job_id>', methods=['GET', 'POST'])
def api_preview(job_id):
    """Run real RealESRGAN_x4plus inference on a 256x256 center patch for fast preview."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({'error': 'Invalid job ID'}), 400

    with jobs_lock:
        job = jobs.get(job_id)

    if not job or not job.get('input_path'):
        return jsonify({'error': 'Job or input file not found'}), 404

    inp_p = safe_path(job['input_path'])
    if not inp_p or not inp_p.is_file():
        return jsonify({'error': 'Input image file not found on disk'}), 404

    data = {}
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}

    try:
        patch_size = int(data.get('patch_size') or request.args.get('patch_size', 256))
    except (ValueError, TypeError):
        patch_size = 256

    format_type = str(data.get('format') or request.args.get('format', 'webp')).lower()
    try:
        quality = int(data.get('quality') or request.args.get('quality', 90))
    except (ValueError, TypeError):
        quality = 90

    worker = get_ai_worker(base_dir=BASE_DIR)
    result = worker.generate_quick_preview(
        input_path=inp_p,
        thumbnails_dir=THUMBNAIL_DIR,
        patch_size=patch_size,
        format_type=format_type,
        quality=quality
    )
    if not result.get('success'):
        return jsonify(result), 400
    return jsonify(result)

@app.route('/thumbnails/<path:filename>')
def serve_thumbnail(filename):
    """Serve thumbnail and preview patch images."""
    return send_from_directory(str(THUMBNAIL_DIR), filename)

# ═══════════════════════════════════════════════════════════════
# Phase 5: Video Enhancement API Endpoints
# ═══════════════════════════════════════════════════════════════

@app.route('/api/video/upload', methods=['POST'])
def api_video_upload():
    """
    Upload one or more video files to uploads/ directory.
    Validates container extension, path safety, and max video size.
    """
    if 'files' not in request.files and 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    uploaded_files = request.files.getlist('files')
    if not uploaded_files or not uploaded_files[0].filename:
        single_f = request.files.get('file')
        if single_f and single_f.filename:
            uploaded_files = [single_f]
        else:
            return jsonify({'error': 'No files provided'}), 400

    max_v_size_mb = float(APP_CONFIG.get("video", {}).get("max_video_size_mb", 2048))
    max_bytes = int(max_v_size_mb * 1024 * 1024)
    supported_v_exts = set(APP_CONFIG.get("video", {}).get("supported_containers", [".mp4", ".mkv", ".mov", ".avi", ".webm"]))

    accepted = []
    rejected = []

    for f in uploaded_files:
        filename = f.filename.strip()
        # Path safety & traversal rejection
        if not filename or '/' in filename or '\\' in filename or filename.startswith('..'):
            rejected.append({"filename": filename, "reason": "Invalid or unsafe filename"})
            continue

        ext = Path(filename).suffix.lower()
        if ext not in supported_v_exts:
            rejected.append({"filename": filename, "reason": f"Unsupported container extension: {ext}"})
            continue

        # Determine destination filename with collision avoidance
        raw_target = UPLOAD_DIR / filename
        if raw_target.exists():
            stem = Path(filename).stem
            ext = Path(filename).suffix
            counter = 1
            while (UPLOAD_DIR / f"{stem}_{counter}{ext}").exists():
                counter += 1
            final_target = UPLOAD_DIR / f"{stem}_{counter}{ext}"
            saved_filename = final_target.name
        else:
            final_target = raw_target
            saved_filename = filename

        # Stream save to uploads directory with in-flight size enforcement
        temp_target = final_target.with_suffix(f"{final_target.suffix}.upload_tmp")
        bytes_written = 0
        exceeded = False
        try:
            with open(temp_target, "wb") as out_f:
                chunk_size = 1024 * 1024  # 1 MB chunk
                while True:
                    chunk = f.stream.read(chunk_size)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    if bytes_written > max_bytes:
                        exceeded = True
                        break
                    out_f.write(chunk)

            if exceeded:
                temp_target.unlink(missing_ok=True)
                rejected.append({"filename": filename, "reason": f"File size exceeds limit ({max_v_size_mb:.0f} MB)"})
                continue

            if bytes_written == 0:
                temp_target.unlink(missing_ok=True)
                rejected.append({"filename": filename, "reason": "Uploaded video file is empty (0 bytes)"})
                continue

            temp_target.replace(final_target)
            accepted.append({
                "filename": saved_filename,
                "original_filename": filename,
                "path": str(final_target),
                "size": bytes_written,
                "size_human": human_size(bytes_written)
            })
        except Exception as e:
            temp_target.unlink(missing_ok=True)
            rejected.append({"filename": filename, "reason": str(e)})

    return jsonify({
        "success": True,
        "accepted": accepted,
        "rejected": rejected,
        "total_accepted": len(accepted),
        "total_rejected": len(rejected)
    })

@app.route('/api/video/analyze', methods=['POST'])
def api_video_analyze():
    """Analyze a video file and return full stream, resolution, and storage metadata."""
    data = request.get_json(silent=True) or {}
    source_str = data.get("source_path") or data.get("filename")
    if not source_str:
        return jsonify({"error": "source_path or filename is required"}), 400

    target = safe_path(source_str)
    if not target or not target.is_file():
        target = UPLOAD_DIR / Path(source_str).name
        if not target.is_file():
            return jsonify({"error": f"Video file not found: {source_str}"}), 404

    try:
        analysis = video_pipeline.analyzer.analyze(target)
        return jsonify(analysis)
    except Exception as e:
        logger.error(f"Video analysis failed for {target}: {e}")
        return jsonify({"valid": False, "error": str(e)}), 400

@app.route('/api/video/jobs', methods=['GET', 'POST'])
def api_video_jobs():
    """
    GET: Query video jobs with filtering, sorting, pagination, search.
    POST: Create a new video job and optionally start it.
    """
    if request.method == 'GET':
        status = request.args.get('status')
        search = request.args.get('search')
        sort_by = request.args.get('sort_by', 'created_at')
        order = request.args.get('order', 'DESC')
        try:
            page = int(request.args.get('page', 1))
            limit = int(request.args.get('limit', 50))
        except (ValueError, TypeError):
            page, limit = 1, 50

        res = video_job_mgr.query_video_jobs(
            status=status,
            search=search,
            sort_by=sort_by,
            order=order,
            page=page,
            limit=limit
        )
        return jsonify(res)

    # POST: Create video job
    data = request.get_json(silent=True) or {}
    source_str = data.get("source_path") or data.get("filename")
    if not source_str:
        return jsonify({"error": "source_path is required"}), 400

    target = safe_path(source_str)
    if not target or not target.is_file():
        target = UPLOAD_DIR / Path(source_str).name
        if not target.is_file():
            return jsonify({"error": f"Video file not found: {source_str}"}), 404

    # Analyze metadata
    try:
        analysis = video_pipeline.analyzer.analyze(target)
    except Exception as e:
        return jsonify({"error": f"Failed to analyze video: {e}"}), 400

    job_id = str(uuid.uuid4())
    ws = video_storage_mgr.get_workspace(job_id)

    processing_cfg = {
        "quality_preset": data.get("quality_preset", "balanced"),
        "tile_mode": data.get("tile_mode", "auto"),
        "tile_size": data.get("tile_size"),
        "scale": 4
    }

    job_record = video_job_mgr.create_video_job(
        job_id=job_id,
        filename=target.name,
        source_path=str(target),
        workspace_dir=str(ws),
        video_metadata=analysis["video"],
        audio_metadata=analysis["audio"],
        processing_config=processing_cfg,
        file_size=analysis["file_size"]
    )

    auto_start = bool(data.get("auto_start", False))
    if auto_start:
        claimed, _, _ = video_job_mgr.claim_job_for_execution(job_id, allowed_statuses=(STATUS_QUEUED,))
        if claimed:
            def bg_run():
                try:
                    video_pipeline.run_job(job_id=job_id, output_dir=OUTPUT_DIR, event_callback=broadcast_event)
                except Exception as e:
                    logger.error(f"Unhandled exception in video worker thread for job {job_id}: {e}")
                    video_job_mgr.release_job_claim(job_id, STATUS_FAILED, error_message=str(e))
            t = threading.Thread(target=bg_run, daemon=True, name=f"VideoWorker-{job_id[:8]}")
            t.start()

    return jsonify({"success": True, "job": job_record})

@app.route('/api/video/jobs/<job_id>', methods=['GET', 'DELETE'])
def api_single_video_job(job_id):
    """GET single video job details or DELETE video job and its workspace."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({"error": "Invalid job UUID"}), 400

    if request.method == 'GET':
        job = video_job_mgr.get_video_job(job_id)
        if not job:
            return jsonify({"error": "Video job not found"}), 404
        ws_info = video_storage_mgr.get_workspace_info(job_id)
        job["workspace_info"] = ws_info
        return jsonify(job)

    # DELETE
    if video_pipeline.is_job_running(job_id):
        video_pipeline.cancel_job(job_id)
        stopped = video_pipeline.wait_for_job_stopped(job_id, timeout=5.0)
        if not stopped:
            return jsonify({
                "error": "Video job is still actively running or terminating; cannot delete workspace until execution stops.",
                "status": "terminating"
            }), 409

    video_storage_mgr.cleanup_workspace(job_id)
    video_job_mgr.delete_video_job(job_id)
    return jsonify({"success": True, "deleted": job_id})

@app.route('/api/video/jobs/<job_id>/start', methods=['POST'])
def api_video_job_start(job_id):
    """Start video enhancement pipeline for job_id in background worker thread with atomic claim."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({"error": "Invalid job UUID"}), 400

    claimed, msg, job = video_job_mgr.claim_job_for_execution(job_id, allowed_statuses=(STATUS_QUEUED,))
    if not claimed:
        curr_st = job.get("status") if job else "UNKNOWN"
        status_code = 409 if curr_st == STATUS_PROCESSING else 400
        return jsonify({"error": msg, "job_status": curr_st}), status_code

    def bg_run():
        try:
            video_pipeline.run_job(job_id=job_id, output_dir=OUTPUT_DIR, event_callback=broadcast_event)
        except Exception as e:
            logger.error(f"Unhandled exception in video worker thread for job {job_id}: {e}")
            video_job_mgr.release_job_claim(job_id, STATUS_FAILED, error_message=str(e))

    t = threading.Thread(target=bg_run, daemon=True, name=f"VideoWorker-{job_id[:8]}")
    try:
        t.start()
    except Exception as e:
        logger.error(f"Failed to start thread for video job {job_id}: {e}")
        video_job_mgr.release_job_claim(job_id, STATUS_QUEUED, error_message=f"Thread start failed: {e}")
        return jsonify({"error": f"Failed to start worker thread: {e}"}), 500

    return jsonify({"success": True, "status": "started", "job_id": job_id})

@app.route('/api/video/jobs/<job_id>/pause', methods=['POST'])
def api_video_job_pause(job_id):
    """Signal video pipeline to pause at safe frame boundary."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({"error": "Invalid job UUID"}), 400

    paused = video_pipeline.pause_job(job_id)
    return jsonify({"success": True, "paused": paused, "job_id": job_id})

@app.route('/api/video/jobs/<job_id>/resume', methods=['POST'])
def api_video_job_resume(job_id):
    """Resume a paused video job with atomic claim."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({"error": "Invalid job UUID"}), 400

    claimed, msg, job = video_job_mgr.claim_job_for_execution(job_id, allowed_statuses=(STATUS_PAUSED, STATUS_QUEUED))
    if not claimed:
        curr_st = job.get("status") if job else "UNKNOWN"
        status_code = 409 if curr_st == STATUS_PROCESSING else 400
        return jsonify({"error": msg, "job_status": curr_st}), status_code

    def bg_run():
        try:
            video_pipeline.run_job(job_id=job_id, output_dir=OUTPUT_DIR, event_callback=broadcast_event)
        except Exception as e:
            logger.error(f"Unhandled exception in video worker thread for job {job_id}: {e}")
            video_job_mgr.release_job_claim(job_id, STATUS_FAILED, error_message=str(e))

    t = threading.Thread(target=bg_run, daemon=True, name=f"VideoWorker-{job_id[:8]}")
    try:
        t.start()
    except Exception as e:
        logger.error(f"Failed to start thread for video job {job_id}: {e}")
        video_job_mgr.release_job_claim(job_id, STATUS_PAUSED, error_message=f"Thread start failed: {e}")
        return jsonify({"error": f"Failed to start worker thread: {e}"}), 500

    return jsonify({"success": True, "status": "resumed", "job_id": job_id})

@app.route('/api/video/jobs/<job_id>/cancel', methods=['POST'])
def api_video_job_cancel(job_id):
    """Cancel a video job and terminate active subprocesses cleanly."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({"error": "Invalid job UUID"}), 400

    job = video_job_mgr.get_video_job(job_id)
    if not job:
        return jsonify({"error": "Video job not found"}), 404

    curr_st = job.get("status")
    if curr_st in (STATUS_COMPLETED, STATUS_FAILED):
        return jsonify({"error": f"Cannot cancel job in terminal state '{curr_st}'.", "job_status": curr_st}), 400

    if curr_st == STATUS_CANCELLED:
        return jsonify({"success": True, "cancelled": True, "message": "Job is already cancelled.", "job_status": STATUS_CANCELLED}), 200

    cancelled = video_pipeline.cancel_job(job_id)
    if curr_st in (STATUS_QUEUED, STATUS_PAUSED):
        video_job_mgr.transition_status(job_id, STATUS_CANCELLED, force=True)

    return jsonify({"success": True, "cancelled": cancelled, "job_id": job_id})

@app.route('/api/video/jobs/<job_id>/progress', methods=['GET'])
def api_video_job_progress(job_id):
    """Get real-time frame progress and estimated time remaining."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({"error": "Invalid job UUID"}), 400

    job = video_job_mgr.get_video_job(job_id)
    if not job:
        return jsonify({"error": "Video job not found"}), 404

    tot = job.get("total_frames") or 1
    cur = job.get("processed_frames") or 0
    pct = min(100, int((cur / tot) * 100)) if tot > 0 else 0

    return jsonify({
        "job_id": job_id,
        "status": job.get("status"),
        "current_stage": job.get("current_stage"),
        "total_frames": tot,
        "processed_frames": cur,
        "progress_pct": pct,
        "tile_used": job.get("tile_used"),
        "elapsed_seconds": job.get("elapsed_seconds", 0.0),
        "output_path": job.get("output_path"),
        "error_message": job.get("error_message")
    })

@app.route('/api/video/jobs/<job_id>/download', methods=['GET'])
def api_video_job_download(job_id):
    """Download finalized enhanced video."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({"error": "Invalid job UUID"}), 400

    job = video_job_mgr.get_video_job(job_id)
    if not job or not job.get("output_path"):
        return jsonify({"error": "Output video not found for this job"}), 404

    target = safe_path(job["output_path"])
    if not target or not target.is_file():
        return jsonify({"error": "Output video file missing on disk"}), 404

    return send_file(
        str(target),
        mimetype="video/mp4",
        as_attachment=True,
        download_name=target.name
    )

@app.route('/api/video/jobs/<job_id>/report', methods=['GET'])
def api_video_job_report(job_id):
    """Download CSV or HTML processing report for video."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({"error": "Invalid job UUID"}), 400

    job = video_job_mgr.get_video_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    fmt = request.args.get("format", "html").lower()
    stats = video_job_mgr.get_statistics()

    if fmt == "csv":
        csv_bytes = generate_video_csv_report([job], stats=stats)
        filename = f"Video_Report_{job_id[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            csv_bytes,
            mimetype="text/csv; charset=utf-8-sig",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    else:
        html_str = generate_video_html_report([job], stats=stats)
        return Response(html_str, mimetype="text/html; charset=utf-8")

@app.route('/api/video/storage', methods=['GET'])
def api_video_storage():
    """Report storage usage across all video workspaces."""
    try:
        active_jobs = [j["id"] for j in video_job_mgr.query_video_jobs(limit=1000)["jobs"]]
        abandoned = video_storage_mgr.list_abandoned_workspaces(active_jobs)
        total_ws_bytes = 0
        for ws in VIDEO_WORKSPACES_DIR.iterdir():
            if ws.is_dir():
                for p in ws.rglob("*"):
                    if p.is_file():
                        try:
                            total_ws_bytes += p.stat().st_size
                        except Exception:
                            pass

        total_disk, used_disk, free_disk = shutil.disk_usage(str(VIDEO_WORKSPACES_DIR.anchor))
        return jsonify({
            "workspaces_dir": str(VIDEO_WORKSPACES_DIR),
            "total_workspace_bytes": total_ws_bytes,
            "total_workspace_human": human_size(total_ws_bytes),
            "free_disk_bytes": free_disk,
            "free_disk_human": f"{free_disk / (1024*1024*1024):.2f} GB",
            "active_workspaces_count": len(active_jobs),
            "abandoned_workspaces": abandoned
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@atexit.register
def cleanup_on_exit():
    """Ensure worker and CUDA resources are released cleanly on process exit."""
    try:
        worker = get_ai_worker(base_dir=BASE_DIR)
        worker.stop()
    except Exception:
        pass

def launch_browser_when_ready():
    """Wait for server readiness before opening browser."""
    url = f"http://127.0.0.1:{PORT}"
    status_url = f"{url}/api/status"
    for _ in range(60):
        time.sleep(0.5)
        try:
            req = urllib.request.Request(status_url, headers={'User-Agent': 'ImageStudio-StartupCheck'})
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status == 200:
                    logger.info(f"Server is responding at {url}. Opening browser...")
                    webbrowser.open(url)
                    return
        except Exception:
            pass
    logger.warning("Server took too long to respond. Browser not opened automatically.")

# ═══════════════════════════════════════════════════════════════
# Application Entrypoint
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print(f"\n  ========================================")
    print(f"     AI Image Studio v{VERSION}")
    print(f"     Local AI Image Upscaler (Standardized)")
    print(f"     Base Directory: {BASE_DIR}")
    print(f"     http://127.0.0.1:{PORT}")
    print(f"  ========================================\n")

    run_startup_checks()

    if not system_status['model_ok']:
        print(f"  [ERROR] Model weights not found at: {MODEL_PATH}")
    if not system_status['cuda_ok']:
        print(f"  [WARNING] CUDA not available on GPU: {system_status.get('gpu_name')}")

    browser_thread = threading.Thread(target=launch_browser_when_ready, daemon=True)
    browser_thread.start()

    try:
        app.run(
            host=APP_CONFIG["server"]["host"],
            port=PORT,
            debug=False,
            threaded=True
        )
    except Exception as e:
        err_msg = traceback.format_exc()
        logger.error(f"Fatal error starting server: {e}\n{err_msg}")
        err_file = LOG_DIR / "startup_error.log"
        try:
            err_file.write_text(err_msg, encoding='utf-8')
        except Exception:
            pass
        print(f"\n[FATAL ERROR] Could not start server on port {PORT}: {e}")
        raise
