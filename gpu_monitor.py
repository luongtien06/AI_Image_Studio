# -*- coding: utf-8 -*-
"""
GPU & VRAM Monitoring Module for AI Image Studio.
Provides real-time system-level GPU metrics (used_mb, total_mb, free_mb, utilization_percent).
Uses NVIDIA Management Library (NVML) via ctypes as primary source of truth,
falling back to nvidia-smi CLI and PyTorch CUDA.
"""

import sys
import os
import ctypes
import logging
import subprocess
from typing import Dict, Any, Tuple, Optional
from ctypes import c_uint, c_ulonglong, byref, Structure, create_string_buffer

logger = logging.getLogger(__name__)

# NVML C Structures
class _NvmlMemory(Structure):
    _fields_ = [
        ('total', c_ulonglong),
        ('free', c_ulonglong),
        ('used', c_ulonglong),
    ]

class _NvmlUtilization(Structure):
    _fields_ = [
        ('gpu', c_uint),
        ('memory', c_uint),
    ]


def _query_nvml_telemetry(device_idx: int = 0) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Query NVML directly via ctypes for zero-overhead live GPU & VRAM telemetry."""
    nvml_dll = None
    if sys.platform == 'win32':
        candidates = [
            "nvml.dll",
            r"C:\Windows\System32\nvml.dll",
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvml.dll"
        ]
        for cand in candidates:
            try:
                nvml_dll = ctypes.CDLL(cand)
                break
            except Exception:
                continue
        if not nvml_dll:
            return None, "nvml.dll not found in standard system locations"
    else:
        for cand in ["libnvidia-ml.so.1", "libnvidia-ml.so"]:
            try:
                nvml_dll = ctypes.CDLL(cand)
                break
            except Exception:
                continue
        if not nvml_dll:
            return None, "libnvidia-ml shared library not found"

    # Initialize NVML
    try:
        init_res = nvml_dll.nvmlInit_v2()
        if init_res != 0:
            return None, f"nvmlInit_v2 failed with error code {init_res}"
    except Exception as e:
        return None, f"nvmlInit call failed: {e}"

    try:
        device = ctypes.c_void_p()
        dev_res = nvml_dll.nvmlDeviceGetHandleByIndex_v2(device_idx, byref(device))
        if dev_res != 0:
            return None, f"nvmlDeviceGetHandleByIndex_v2({device_idx}) failed with code {dev_res}"

        # 1. Device Name
        name_buf = create_string_buffer(96)
        nvml_dll.nvmlDeviceGetName(device, name_buf, 96)
        raw_name = name_buf.value.decode('utf-8', errors='ignore').strip()

        # 2. VRAM Memory
        mem = _NvmlMemory()
        mem_res = nvml_dll.nvmlDeviceGetMemoryInfo(device, byref(mem))
        if mem_res != 0:
            return None, f"nvmlDeviceGetMemoryInfo failed with code {mem_res}"

        total_bytes = mem.total
        free_bytes = mem.free
        used_bytes = mem.used

        total_mb = round(total_bytes / (1024.0 * 1024.0), 1)
        used_mb = round(used_bytes / (1024.0 * 1024.0), 1)
        free_mb = round(free_bytes / (1024.0 * 1024.0), 1)

        total_gb = round(total_bytes / (1024.0 ** 3), 1)
        used_gb = round(used_bytes / (1024.0 ** 3), 1)
        free_gb = round(free_bytes / (1024.0 ** 3), 1)

        used_pct = round((used_bytes / total_bytes) * 100.0, 1) if total_bytes > 0 else 0.0

        # 3. Core Utilization
        util = _NvmlUtilization()
        util_res = nvml_dll.nvmlDeviceGetUtilizationRates(device, byref(util))
        gpu_util = int(util.gpu) if util_res == 0 else None

        return {
            "telemetry_available": True,
            "telemetry_source": "nvml",
            "device_id": device_idx,
            "gpu_name": raw_name,
            "cuda_available": True,
            "total_mb": total_mb,
            "used_mb": used_mb,
            "free_mb": free_mb,
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "used_pct": used_pct,
            "utilization_percent": gpu_util,
            "status": "Active"
        }, None
    finally:
        try:
            nvml_dll.nvmlShutdown()
        except Exception:
            pass


def _query_nvidiasmi_telemetry(device_idx: int = 0) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Fallback: query nvidia-smi CLI for driver-level GPU & VRAM telemetry."""
    try:
        cmd = [
            "nvidia-smi",
            f"--id={device_idx}",
            "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits"
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=2.5, shell=False)
        if proc.returncode != 0:
            return None, proc.stderr.strip() or f"nvidia-smi exited with code {proc.returncode}"

        out = proc.stdout.strip()
        parts = [p.strip() for p in out.split(",")]
        if len(parts) >= 5:
            dev_id = int(parts[0])
            gpu_name = parts[1]
            used_mb = float(parts[2])
            total_mb = float(parts[3])
            free_mb = max(0.0, total_mb - used_mb)
            gpu_util = float(parts[4])

            total_gb = round(total_mb / 1024.0, 1)
            used_gb = round(used_mb / 1024.0, 1)
            free_gb = round(free_mb / 1024.0, 1)
            used_pct = round((used_mb / total_mb) * 100.0, 1) if total_mb > 0 else 0.0

            return {
                "telemetry_available": True,
                "telemetry_source": "nvidia-smi",
                "device_id": dev_id,
                "gpu_name": gpu_name,
                "cuda_available": True,
                "total_mb": round(total_mb, 1),
                "used_mb": round(used_mb, 1),
                "free_mb": round(free_mb, 1),
                "total_gb": total_gb,
                "used_gb": used_gb,
                "free_gb": free_gb,
                "used_pct": used_pct,
                "utilization_percent": round(gpu_util, 1),
                "status": "Active"
            }, None
        return None, f"Unexpected nvidia-smi format: {out}"
    except Exception as e:
        return None, str(e)


def _query_pytorch_telemetry(device_idx: int = 0) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Secondary Fallback: query PyTorch CUDA mem_get_info if CUDA is active in process."""
    try:
        import torch
        if not torch.cuda.is_available():
            return None, "PyTorch CUDA not available"

        gpu_name = torch.cuda.get_device_name(device_idx)
        free_bytes, total_bytes = torch.cuda.mem_get_info(device_idx)
        used_bytes = total_bytes - free_bytes

        total_mb = round(total_bytes / (1024.0 * 1024.0), 1)
        used_mb = round(used_bytes / (1024.0 * 1024.0), 1)
        free_mb = round(free_bytes / (1024.0 * 1024.0), 1)

        total_gb = round(total_bytes / (1024.0 ** 3), 1)
        used_gb = round(used_bytes / (1024.0 ** 3), 1)
        free_gb = round(free_bytes / (1024.0 ** 3), 1)
        used_pct = round((used_bytes / total_bytes) * 100.0, 1) if total_bytes > 0 else 0.0

        return {
            "telemetry_available": True,
            "telemetry_source": "torch",
            "device_id": device_idx,
            "gpu_name": gpu_name,
            "cuda_available": True,
            "total_mb": total_mb,
            "used_mb": used_mb,
            "free_mb": free_mb,
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "used_pct": used_pct,
            "utilization_percent": None,
            "status": "Active"
        }, None
    except Exception as e:
        return None, str(e)


def get_gpu_info(device_idx: int = 0) -> Dict[str, Any]:
    """
    Query current GPU and VRAM status using multi-tiered driver and system APIs:
    1. NVML C-library (primary, driver-level, zero process overhead, works across child/parent processes).
    2. nvidia-smi CLI (fallback).
    3. PyTorch CUDA (secondary fallback).
    4. Explicit error structure with telemetry_available=False if all unavailable.
    """
    # 1. Primary: Native NVML
    data, err_nvml = _query_nvml_telemetry(device_idx=device_idx)
    if data:
        return data

    logger.debug(f"NVML telemetry query failed: {err_nvml}; trying nvidia-smi fallback")

    # 2. Fallback: nvidia-smi
    data, err_smi = _query_nvidiasmi_telemetry(device_idx=device_idx)
    if data:
        return data

    logger.debug(f"nvidia-smi fallback failed: {err_smi}; trying PyTorch fallback")

    # 3. Secondary Fallback: PyTorch
    data, err_torch = _query_pytorch_telemetry(device_idx=device_idx)
    if data:
        return data

    # 4. Explicit Unavailable State
    err_summary = f"NVML: {err_nvml} | SMI: {err_smi} | Torch: {err_torch}"
    logger.warning(f"All GPU telemetry sources failed: {err_summary}")
    return {
        "telemetry_available": False,
        "telemetry_source": "none",
        "device_id": None,
        "gpu_name": "N/A",
        "cuda_available": False,
        "total_mb": None,
        "used_mb": None,
        "free_mb": None,
        "total_gb": None,
        "used_gb": None,
        "free_gb": None,
        "used_pct": None,
        "utilization_percent": None,
        "status": "Telemetry Unavailable",
        "error_message": err_summary
    }
