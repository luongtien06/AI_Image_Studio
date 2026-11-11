# -*- coding: utf-8 -*-
"""
GPU & VRAM Monitoring Module for AI Image Studio
Provides real-time CUDA, GPU name, and VRAM memory metrics using PyTorch native CUDA APIs.
"""

import logging

logger = logging.getLogger(__name__)

def get_gpu_info():
    """
    Query current GPU and VRAM status.
    Returns dictionary with cuda_available, gpu_name, total_gb, used_gb, free_gb, allocated_mb.
    """
    try:
        import torch
        if not torch.cuda.is_available():
            return {
                "cuda_available": False,
                "gpu_name": "CPU / No CUDA GPU",
                "total_gb": 0.0,
                "used_gb": 0.0,
                "free_gb": 0.0,
                "allocated_mb": 0.0,
                "reserved_mb": 0.0,
                "status": "CUDA Not Available"
            }

        device_id = 0
        gpu_name = torch.cuda.get_device_name(device_id)
        free_bytes, total_bytes = torch.cuda.mem_get_info(device_id)
        used_bytes = total_bytes - free_bytes

        alloc_bytes = torch.cuda.memory_allocated(device_id)
        res_bytes = torch.cuda.memory_reserved(device_id)

        return {
            "cuda_available": True,
            "device_id": device_id,
            "gpu_name": gpu_name,
            "total_gb": round(total_bytes / (1024 ** 3), 2),
            "used_gb": round(used_bytes / (1024 ** 3), 2),
            "free_gb": round(free_bytes / (1024 ** 3), 2),
            "allocated_mb": round(alloc_bytes / (1024 ** 2), 1),
            "reserved_mb": round(res_bytes / (1024 ** 2), 1),
            "used_pct": round((used_bytes / total_bytes) * 100, 1) if total_bytes > 0 else 0,
            "status": "Ready"
        }
    except Exception as e:
        logger.warning(f"Error querying GPU metrics: {e}")
        return {
            "cuda_available": False,
            "gpu_name": "Error querying GPU",
            "total_gb": 0.0,
            "used_gb": 0.0,
            "free_gb": 0.0,
            "allocated_mb": 0.0,
            "reserved_mb": 0.0,
            "status": f"Error: {e}"
        }
