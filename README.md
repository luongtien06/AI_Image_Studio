# AI Image Studio

**A local AI-powered image and video upscaling studio powered by Real-ESRGAN.**

AI Image Studio provides a browser-based interface for running AI image enhancement and supported video-processing workflows locally on your Windows computer.

The application is designed around the **RealESRGAN_x4plus** model, with an emphasis on local processing, resource management, and keeping your input files and generated results under your control.

> **Important:** AI Image Studio is an independent application built around the Real-ESRGAN project. It is not the official Real-ESRGAN application.

---

## Table of Contents

- [Highlights](#highlights)
- [Screenshots](#screenshots)
- [System Requirements](#system-requirements)
- [Hardware Compatibility](#hardware-compatibility)
- [Model Weights](#model-weights)
- [Installation](#installation)
- [Running the Application](#running-the-application)
- [Usage](#usage)
- [GPU and Performance Configuration](#gpu-and-performance-configuration)
- [Video Processing](#video-processing)
- [Project Structure](#project-structure)
- [Data Storage and Privacy](#data-storage-and-privacy)
- [Troubleshooting](#troubleshooting)
- [Technology and Credits](#technology-and-credits)
- [License](#license)

---

## Highlights

- **AI Image Upscaling** — Enhance image resolution using the RealESRGAN_x4plus model.
- **Local Processing** — Process images on your own computer without requiring a third-party image-processing service.
- **Browser-Based Interface** — Access the application through a locally hosted web interface.
- **GPU Acceleration** — Use a compatible NVIDIA GPU with CUDA-enabled PyTorch.
- **Resource-Aware Processing** — Manage processing resources to reduce GPU memory pressure.
- **Job Management** — Track processing jobs and their results through the application interface.
- **Video Processing** — Support video-processing workflows through FFmpeg integration, where available.
- **Windows Launcher** — Start the application using `Start_ImageStudio.bat`.

Available features and processing performance depend on the application version, installed dependencies, model weights, hardware, and configuration.

## Screenshots

Application screenshots will be added here.

<!-- Add screenshots when available. -->

---

## System Requirements

### Operating System

- Windows 10 or Windows 11 (64-bit).

### Software

- Python 3.10.
- Conda or Miniconda (recommended for environment management).
- Compatible PyTorch and torchvision versions.
- Application dependencies listed in the project's dependency files.
- FFmpeg for supported video-processing workflows.

### Hardware

- An NVIDIA GPU is recommended for accelerated image processing.
- GPU compatibility depends on the installed NVIDIA driver, CUDA support, and PyTorch build.
- Available GPU memory affects the maximum practical image size and processing configuration.
- CPU execution should only be considered if supported by the installed application and dependencies.

**AI Image Studio is not restricted to NVIDIA RTX 2050.** RTX 2050 is one hardware configuration used during development and testing. Other compatible NVIDIA GPUs may work, subject to their hardware capabilities and software environment.

---

## Hardware Compatibility

AI Image Studio is designed to run on different Windows computers, but performance and compatibility are hardware-dependent.

| Hardware | Expected usage |
|---|---|
| NVIDIA RTX GPUs | GPU acceleration when CUDA and PyTorch are compatible |
| NVIDIA GTX GPUs | Potential GPU acceleration, depending on CUDA compatibility |
| NVIDIA GPUs with more VRAM | More flexibility for larger images and processing tiles |
| Integrated graphics | No CUDA acceleration; CPU execution may be possible |
| AMD / Intel GPUs | Not guaranteed with the current NVIDIA CUDA-based environment |
| CPU-only systems | Depends on whether the installed application supports CPU inference |

### Important compatibility notes

1. The application does not require an RTX 2050 specifically.
2. GPU memory requirements depend on image dimensions, model, tile size, and other processing settings.
3. A GPU with more VRAM does not automatically guarantee compatibility with every PyTorch installation.
4. CPU processing may be significantly slower than GPU processing.
5. Multi-GPU selection and automatic hardware configuration should only be considered available when implemented in the installed application version.

---

## Model Weights

AI Image Studio primarily uses:

**RealESRGAN_x4plus** — 4× image super-resolution.

The model weights are not included in this repository.

Download the required model from the official Real-ESRGAN project or its documented release sources:

- [Real-ESRGAN GitHub Repository](https://github.com/xinntao/Real-ESRGAN)
- [Real-ESRGAN Releases](https://github.com/xinntao/Real-ESRGAN/releases)

Place the downloaded model file in the following location:

```text
weights/
└── RealESRGAN_x4plus.pth
```

The expected filename is:

```text
RealESRGAN_x4plus.pth
```

Make sure the filename and directory match the application configuration.

**Model file requirements:**

- The file must be a valid RealESRGAN_x4plus checkpoint.
- Do not rename an unrelated model to match the expected filename.
- Keep model weights outside Git unless redistribution is explicitly permitted.
- Respect the original model license and attribution requirements.

---

## Installation

The following instructions describe a fresh Windows installation.

### 1. Clone the repository

Open PowerShell:

```powershell
git clone https://github.com/luongtien06/AI_Image_Studio.git
cd AI_Image_Studio
```

### 2. Install Python

Install Python 3.10 (64-bit) and Conda or Miniconda.

Verify the installation:

```powershell
python --version
conda --version
```

If Python is managed through Conda, create a dedicated environment:

```powershell
conda create -n realesrgan python=3.10 -y
conda activate realesrgan
```

### 3. Install PyTorch

Install a PyTorch and torchvision combination compatible with your system.

Use the official installation selector:

[PyTorch - Get Started](https://pytorch.org/get-started/locally/)

For NVIDIA GPU acceleration, select an appropriate CUDA-enabled PyTorch build based on your NVIDIA driver and supported CUDA runtime.

Do not copy CUDA installation commands from another computer without checking compatibility.

Verify PyTorch:

```powershell
python -c "import torch; print('PyTorch:', torch.__version__); print('CUDA available:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

### 4. Install application dependencies

Install the dependencies specified by the repository's actual dependency manifest.

For example, if the repository contains `requirements.txt`:

```powershell
pip install -r requirements.txt
```

If the project uses another dependency manifest or installation procedure, follow that project's documented instructions instead.

Avoid installing arbitrary latest package versions over a tested environment.

### 5. Install the model weights

Create the model directory if necessary:

```powershell
New-Item -ItemType Directory -Force -Path weights
```

Copy the downloaded model into:

```text
weights/RealESRGAN_x4plus.pth
```

### 6. Configure FFmpeg (optional)

FFmpeg is required only for supported video-processing workflows.

Download FFmpeg from:

[FFmpeg Official Website](https://ffmpeg.org/download.html)

Ensure that `ffmpeg` and `ffprobe` are accessible through the Windows PATH, if required by the application.

Verify:

```powershell
ffmpeg -version
ffprobe -version
```

### 7. Review the application configuration

Review `config.json` before starting the application.

Check hardware-related settings, model paths, local directories, and other application-specific options.

Do not copy absolute paths from another computer unless those paths exist on the new system.

---

## Running the Application

After completing the installation:

1. Activate the project's Python environment.
2. Open the project directory.
3. Run the Windows launcher:

```powershell
.\Start_ImageStudio.bat
```

4. Follow the terminal output.
5. Open the local application address displayed by the launcher in your browser.

The default local address for this project is:

```text
http://127.0.0.1:5678
```

The application is intended for local use. Do not expose the development server directly to public networks without reviewing its security configuration.

---

## Usage

The general workflow is:

1. Start AI Image Studio.
2. Open the local browser interface.
3. Select input images or use the available folder-selection workflow.
4. Configure the available processing options.
5. Start the processing job.
6. Monitor job status and resource usage.
7. Access the generated output through the application.

### Input and Output

The application may create or use local directories for:

- Uploaded images.
- Generated output images.
- Processing jobs.
- Logs.
- Persistent job and application data.

Actual locations depend on the application configuration.

**Important:** Keep original images and important files backed up. AI Image Studio should be used with non-destructive processing workflows.

---

## GPU and Performance Configuration

GPU performance depends on several factors:

- GPU model and architecture.
- Available VRAM.
- Image dimensions.
- Model architecture.
- Tile size and tile padding.
- Other GPU applications using memory.
- PyTorch and CUDA compatibility.

### Tile Size

Tile size determines how image processing is divided into smaller sections.

A smaller tile generally reduces GPU memory requirements but may increase processing time.

A larger tile may improve processing efficiency but requires more GPU memory.

For the development configuration using an NVIDIA RTX 2050 Laptop GPU with 4 GB VRAM, Tile 128 is a practical starting point.

Other computers may require different settings.

Do not assume the same tile size is optimal for every GPU.

### Performance Recommendations

- Close unnecessary GPU-intensive applications.
- Use a compatible NVIDIA driver.
- Keep sufficient free disk space.
- Avoid running multiple heavy inference jobs simultaneously.
- Reduce tile size if CUDA out-of-memory errors occur.
- Keep original input files separate from generated output files.

---

## Video Processing

AI Image Studio includes a video-processing workflow through FFmpeg integration where supported by the installed application.

Video processing may require:

- FFmpeg.
- FFprobe.
- Sufficient disk space.
- Compatible video and audio codecs.
- Additional processing time and system resources.

Video-processing capabilities and supported formats depend on the current application implementation and local FFmpeg installation.

---

## Project Structure

Simplified repository structure:

```text
AI_Image_Studio/
├── archive/             # Retained legacy or upstream files
├── video/               # Video-processing integration
├── weights/             # Local model weights
├── uploads/             # Local input/runtime files
├── output/              # Generated results
├── logs/                # Runtime logs
├── data/                # Persistent application data
├── Start_ImageStudio.bat
├── config.json
├── requirements.txt     # If included in the repository
└── README.md
```

Some directories are created automatically when the application runs.

The actual repository may contain additional source files, templates, tests, scripts, and configuration files.

---

## Data Storage and Privacy

AI Image Studio is designed for local processing.

Input images, generated results, job information, and runtime logs may be stored on the local computer.

Recommended practices:

- Keep private images and generated content in local directories.
- Back up important input files and generated results.
- Do not commit private images, model weights, runtime databases, logs, or credentials to Git.
- Review application configuration before using the application in a shared environment.
- Avoid exposing the local application server to untrusted networks.

The actual data-storage behavior depends on the application configuration and installed version.

---

## Troubleshooting

### 1. Conda or Python environment cannot be found

Check that Conda is installed and that the correct environment exists.

```powershell
conda env list
conda activate realesrgan
python --version
```

### 2. PyTorch cannot detect the NVIDIA GPU

Verify the NVIDIA driver:

```powershell
nvidia-smi
```

Verify PyTorch CUDA availability:

```powershell
python -c "import torch; print(torch.cuda.is_available()); print(torch.version.cuda)"
```

If CUDA is unavailable, check the NVIDIA driver and reinstall a compatible PyTorch build.

### 3. CUDA out-of-memory error

Try the following:

- Close other GPU-intensive applications.
- Reduce the configured tile size.
- Process smaller images.
- Restart the application if GPU memory remains occupied.

### 4. Model file is missing

Verify that this file exists:

```text
weights/RealESRGAN_x4plus.pth
```

Check that the filename and configured model path are correct.

### 5. FFmpeg is not detected

Verify:

```powershell
ffmpeg -version
ffprobe -version
```

If the commands are unavailable, check the FFmpeg installation and Windows PATH.

### 6. Application does not start

Check:

- Python version.
- Active Conda environment.
- Installed dependencies.
- Model file availability.
- Application configuration.
- Terminal error messages.

Review the application logs when available.

---

## Technology and Credits

AI Image Studio builds upon the work of the following open-source projects:

- [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN)
- [BasicSR](https://github.com/XPixelGroup/BasicSR)
- [PyTorch](https://pytorch.org/)
- [FFmpeg](https://ffmpeg.org/)

All third-party software and model assets remain subject to their respective licenses and attribution requirements.

---

## License

AI Image Studio is an independent application that incorporates or uses third-party open-source software.

The upstream Real-ESRGAN project includes a BSD 3-Clause license.

Refer to the repository's `LICENSE` file and the individual licenses of third-party dependencies before modifying, redistributing, or commercially using the project.

Retain all applicable original copyright notices and license terms.

---

## Repository

[https://github.com/luongtien06/AI_Image_Studio](https://github.com/luongtien06/AI_Image_Studio)

**AI Image Studio — Local AI Image Enhancement.**
