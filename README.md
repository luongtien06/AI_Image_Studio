# AI Image Studio

**A local AI-powered image and video upscaling studio built with [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN).**

AI Image Studio provides a convenient local interface for running AI upscaling workflows on your own computer. It is designed to use the **RealESRGAN x4plus** model while keeping processing and files under your control.

> This repository contains an application built around Real-ESRGAN. It is not the official Real-ESRGAN project.

## Highlights

- **AI image upscaling** powered by the RealESRGAN x4plus model.
- **Local processing** — run the application on your own machine instead of uploading images to a third-party service.
- **Browser-based interface** served locally by the application.
- **Video-processing workflow** with FFmpeg integration, where supported by the local installation.
- **GPU acceleration** through a compatible NVIDIA GPU and CUDA-enabled PyTorch installation.
- **Windows launcher** (`Start_ImageStudio.bat`) to start the application using the configured Python environment.

Features and available options may depend on the installed model, Python packages, GPU, and FFmpeg configuration.

## Screenshots

Screenshots will be added here.

## Requirements

The following is the intended environment. Your actual hardware and package versions may differ.

- Windows 10 or Windows 11
- Python 3.10
- Conda (recommended for managing the Python environment)
- PyTorch and torchvision installed in a compatible configuration
- Real-ESRGAN and its required dependencies
- An NVIDIA GPU is recommended for CUDA acceleration; CPU execution may be slower
- FFmpeg for video workflows

For GPU acceleration, install a PyTorch build compatible with your NVIDIA driver and CUDA runtime. Follow the official [PyTorch installation selector](https://pytorch.org/get-started/locally/) rather than copying a CUDA command intended for a different system.

## Model

The primary model used by this application is:

- **RealESRGAN_x4plus** — 4× image super-resolution.

The model weights are not included in this repository. Obtain the weights from the official [Real-ESRGAN releases](https://github.com/xinntao/Real-ESRGAN) or the project's documented model source, and place the required file in the local `weights` directory.

Expected filename:

```text
weights/
└── RealESRGAN_x4plus.pth
```

Respect the model's original license and attribution when downloading, using, or redistributing its weights.

## Installation

### 1. Clone the repository

```powershell
git clone https://github.com/luongtien06/AI_Image_Studio.git
cd AI_Image_Studio
```

### 2. Prepare the Python environment

Create or activate the Conda environment used for this project. For example:

```powershell
conda create -n realesrgan python=3.10 -y
conda activate realesrgan
```

Install PyTorch, torchvision, and the application dependencies using versions compatible with your system. Refer to the official PyTorch and Real-ESRGAN documentation for the appropriate installation commands.

### 3. Add the model weights

Create the `weights` directory if it does not exist, then place `RealESRGAN_x4plus.pth` inside it.

### 4. Configure FFmpeg (video workflows)

Install FFmpeg and make sure `ffmpeg` and `ffprobe` are available on your system `PATH`, or configure the executable location using the application's supported settings.

### 5. Start the application

From the project directory, run:

```powershell
.\Start_ImageStudio.bat
```

The launcher checks the configured Python environment and required dependencies before starting. Follow the messages in the terminal for the local application address and any setup issues.

## Usage

1. Start AI Image Studio with `Start_ImageStudio.bat`.
2. Open the local address printed by the launcher in your browser.
3. Choose the input image or use the available video workflow.
4. Select the available processing options.
5. Start processing and save the generated result to your configured output location.

The exact controls and supported formats are determined by the version of the application you are running.

## Project Structure

A simplified overview of the repository:

```text
AI_Image_Studio/
├── archive/          # Retained legacy upstream packaging/workflow files
├── video/            # Video-processing integration
├── weights/          # Local model weights (not tracked by Git)
├── uploads/           # Local input/runtime files (not tracked by Git)
├── output/            # Local generated files (not tracked by Git)
├── logs/              # Local runtime logs (not tracked by Git)
├── data/              # Local runtime data (not tracked by Git)
├── Start_ImageStudio.bat
├── config.json
└── README.md
```

Some runtime directories are created by the application when needed and may not exist in a fresh clone.

## Privacy and Local Data

AI Image Studio is intended to run locally. Input files, generated outputs, logs, and other runtime data may be stored on your computer according to the application configuration.

- Keep private images and generated content in local folders.
- Back up important inputs and outputs independently.
- Do not commit model weights, personal images, runtime databases, logs, or secrets to Git.
- Review `config.json` before using the application in a shared or exposed environment.

## Troubleshooting

### The launcher cannot find Conda or the Python environment

Install Conda, ensure the configured environment exists, and check that the launcher can locate it.

### PyTorch cannot use the NVIDIA GPU

Check that your NVIDIA driver is installed and that your PyTorch build is compatible with your system. Verify CUDA availability from the active environment:

```powershell
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

### FFmpeg is not detected

Install FFmpeg and add its `bin` directory to `PATH`. Open a new terminal and verify:

```powershell
ffmpeg -version
ffprobe -version
```

### The model file is missing

Confirm that `weights/RealESRGAN_x4plus.pth` exists and that the filename matches the application's configuration.

## Credits and License

AI Image Studio builds on the work of the **Real-ESRGAN** project by Xintao Wang and contributors, along with the project's underlying open-source dependencies.

- Real-ESRGAN: https://github.com/xinntao/Real-ESRGAN
- BasicSR: https://github.com/XPixelGroup/BasicSR
- PyTorch: https://pytorch.org/

The upstream Real-ESRGAN project includes a BSD 3-Clause license. The original `LICENSE` file and its copyright notices must be retained. Check the license files of all bundled or installed dependencies before redistributing them.

---

**Repository:** https://github.com/luongtien06/AI_Image_Studio
