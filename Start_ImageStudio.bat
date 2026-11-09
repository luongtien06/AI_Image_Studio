@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
title AI Image Studio

echo ===================================================
echo             AI IMAGE STUDIO v1.0
echo       Local AI Image Upscaler (Real-ESRGAN)
echo ===================================================
echo.

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "PORT=5678"
set "URL=http://127.0.0.1:%PORT%"
set "LOG_DIR=%SCRIPT_DIR%logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "STARTUP_LOG=%LOG_DIR%\startup.log"

echo [%DATE% %TIME%] Starting AI Image Studio launch sequence > "%STARTUP_LOG%"
echo Script Directory: %SCRIPT_DIR% >> "%STARTUP_LOG%"

REM ?? Step 1: Detect Conda Installation (Fully Portable) ??
echo [1/5] Detecting Conda installation...
set "CONDA_BAT="

REM Check if conda is already in PATH
for /f "tokens=*" %%i in ('where conda.bat 2^>nul') do (
    if not defined CONDA_BAT if exist "%%i" set "CONDA_BAT=%%i"
)

REM Check standard user profile paths dynamically
if not defined CONDA_BAT if exist "%USERPROFILE%\anaconda3\condabin\conda.bat" set "CONDA_BAT=%USERPROFILE%\anaconda3\condabin\conda.bat"
if not defined CONDA_BAT if exist "%USERPROFILE%\miniconda3\condabin\conda.bat" set "CONDA_BAT=%USERPROFILE%\miniconda3\condabin\conda.bat"
if not defined CONDA_BAT if exist "%LOCALAPPDATA%\anaconda3\condabin\conda.bat" set "CONDA_BAT=%LOCALAPPDATA%\anaconda3\condabin\conda.bat"
if not defined CONDA_BAT if exist "%LOCALAPPDATA%\miniconda3\condabin\conda.bat" set "CONDA_BAT=%LOCALAPPDATA%\miniconda3\condabin\conda.bat"
if not defined CONDA_BAT if exist "C:\ProgramData\anaconda3\condabin\conda.bat" set "CONDA_BAT=C:\ProgramData\anaconda3\condabin\conda.bat"
if not defined CONDA_BAT if exist "C:\ProgramData\miniconda3\condabin\conda.bat" set "CONDA_BAT=C:\ProgramData\miniconda3\condabin\conda.bat"
if not defined CONDA_BAT if exist "C:\tools\anaconda3\condabin\conda.bat" set "CONDA_BAT=C:\tools\anaconda3\condabin\conda.bat"
if not defined CONDA_BAT if exist "C:\tools\miniconda3\condabin\conda.bat" set "CONDA_BAT=C:\tools\miniconda3\condabin\conda.bat"

if not defined CONDA_BAT (
    echo [ERROR] Conda was not found on your system!
    echo Please make sure Anaconda or Miniconda is installed.
    echo [ERROR] Conda not found >> "%STARTUP_LOG%"
    echo.
    pause
    exit /b 1
)
echo      Found Conda: %CONDA_BAT%
echo Found Conda: %CONDA_BAT% >> "%STARTUP_LOG%"

REM ?? Step 2: Detect 'realesrgan' Python Environment ??
echo [2/5] Detecting 'realesrgan' Python environment...
set "PYTHON_EXE="

REM 1. If currently inside activated realesrgan env
if defined CONDA_PREFIX (
    if exist "%CONDA_PREFIX%\python.exe" (
        for %%f in ("%CONDA_PREFIX%") do (
            if /i "%%~nxf"=="realesrgan" set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"
        )
    )
)

REM 2. Derive base directory of conda installation
if not defined PYTHON_EXE (
    for %%f in ("%CONDA_BAT%\..\..") do set "CONDA_ROOT=%%~ff"
    if exist "!CONDA_ROOT!\envs\realesrgan\python.exe" set "PYTHON_EXE=!CONDA_ROOT!\envs\realesrgan\python.exe"
)

REM 3. Check user profile conda env paths
if not defined PYTHON_EXE if exist "%USERPROFILE%\.conda\envs\realesrgan\python.exe" set "PYTHON_EXE=%USERPROFILE%\.conda\envs\realesrgan\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\anaconda3\envs\realesrgan\python.exe" set "PYTHON_EXE=%USERPROFILE%\anaconda3\envs\realesrgan\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\miniconda3\envs\realesrgan\python.exe" set "PYTHON_EXE=%USERPROFILE%\miniconda3\envs\realesrgan\python.exe"

if not defined PYTHON_EXE (
    echo [ERROR] Conda environment 'realesrgan' was not found!
    echo Please create the environment:
    echo   conda create -n realesrgan python=3.10 -y
    echo [ERROR] Environment 'realesrgan' not found >> "%STARTUP_LOG%"
    echo.
    pause
    exit /b 1
)
echo      Found Python: %PYTHON_EXE%
echo Found Python: %PYTHON_EXE% >> "%STARTUP_LOG%"

REM ?? Step 3: Check AI Model Weights (Portable Relative Path) ??
echo [3/5] Verifying model weights...
set "MODEL_PATH=%SCRIPT_DIR%weights\RealESRGAN_x4plus.pth"

if not exist "%MODEL_PATH%" (
    echo [ERROR] Model weights file not found!
    echo Expected: %MODEL_PATH%
    echo Please place RealESRGAN_x4plus.pth into the weights\ folder.
    echo [ERROR] Model not found: %MODEL_PATH% >> "%STARTUP_LOG%"
    echo.
    pause
    exit /b 1
)
echo      Found Model: %MODEL_PATH%
echo Found Model: %MODEL_PATH% >> "%STARTUP_LOG%"

REM ?? Step 4: Verify Python Dependencies ??
echo [4/5] Checking required Python dependencies...

"%PYTHON_EXE%" -c "import importlib.util; modules=['torch','torchvision','flask','cv2','PIL','basicsr','realesrgan']; missing=[m for m in modules if importlib.util.find_spec(m) is None]; print('Missing modules: '+', '.join(missing) if missing else 'All required modules are installed.'); raise SystemExit(1 if missing else 0)"

if errorlevel 1 (
    echo.
    echo [ERROR] One or more required Python modules are missing.
    echo No packages have been installed or modified.
    echo Please repair the realesrgan environment using the project setup instructions.
    echo Dependency verification failed >> "%STARTUP_LOG%"
    echo.
    pause
    exit /b 1
)

echo Dependencies verified.
echo Dependencies verified >> "%STARTUP_LOG%"

REM Check CUDA without modifying the Python environment
echo Checking CUDA availability...

"%PYTHON_EXE%" -c "import torch; available=torch.cuda.is_available(); print('CUDA available: '+str(available)); print('GPU: '+(torch.cuda.get_device_name(0) if available else 'Not detected'))"

if errorlevel 1 (
    echo [WARNING] Could not determine CUDA status.
    echo Application startup will continue.
    echo CUDA check failed >> "%STARTUP_LOG%"
) else (
    "%PYTHON_EXE%" -c "import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)" >nul 2>&1
    if errorlevel 1 (
        echo [WARNING] CUDA is not available. GPU acceleration may be unavailable.
        echo CUDA unavailable >> "%STARTUP_LOG%"
    ) else (
        echo CUDA device verified.
        echo CUDA available >> "%STARTUP_LOG%"
    )
)
REM ?? Step 5: Check Port and Prevent Duplicate Instances ??
echo [5/5] Checking port %PORT%...
set "PORT_IN_USE="
"%PYTHON_EXE%" -c "import socket; s=socket.socket(); err=s.connect_ex(('127.0.0.1', %PORT%)); s.close(); exit(0 if err==0 else 1)" >nul 2>&1
if not errorlevel 1 set "PORT_IN_USE=1"

if defined PORT_IN_USE (
    echo Port %PORT% is currently listening.
    echo Checking if AI Image Studio is already running...
    "%PYTHON_EXE%" -c "import urllib.request, json, sys; sys.exit(0 if 'version' in json.loads(urllib.request.urlopen('http://127.0.0.1:%PORT%/api/status', timeout=2).read().decode()) else 1)" >nul 2>&1
    if not errorlevel 1 (
        echo.
        echo [INFO] AI Image Studio is ALREADY running on %URL%!
        echo Opening existing application in your browser...
        start "" "%URL%"
        echo.
        echo Window will close in 5 seconds.
        ping 127.0.0.1 -n 6 >nul
        exit /b 0
    ) else (
        echo.
        echo [ERROR] Port %PORT% is occupied by another application.
        echo To prevent conflicts, AI Image Studio will not terminate the other program.
        echo Please close the application using port %PORT% or modify port in config.json.
        echo.
        pause
        exit /b 1
    )
)
echo      Port %PORT% is available.
echo Port %PORT% is available >> "%STARTUP_LOG%"

REM ?? Ensure Working Directories Exist ??
if not exist "%SCRIPT_DIR%uploads" mkdir "%SCRIPT_DIR%uploads"
if not exist "%SCRIPT_DIR%output" mkdir "%SCRIPT_DIR%output"
if not exist "%SCRIPT_DIR%thumbnails" mkdir "%SCRIPT_DIR%thumbnails"
if not exist "%SCRIPT_DIR%data" mkdir "%SCRIPT_DIR%data"

REM ?? Launch Server ??
echo.
echo ===================================================
echo  Starting server at %URL%
echo  The web browser will open automatically once ready.
echo  Keep this window open while using the application.
echo  Press Ctrl+C to safely stop the server.
echo ===================================================
echo.

call "%CONDA_BAT%" activate realesrgan
cd /d "%SCRIPT_DIR%"
"%PYTHON_EXE%" app.py

if errorlevel 1 (
    echo.
    echo ===================================================
    echo  [ERROR] AI Image Studio stopped unexpectedly!
    echo  Please check logs in %LOG_DIR%\app.log
    if exist "%LOG_DIR%\startup_error.log" (
        echo  Startup error traceback:
        type "%LOG_DIR%\startup_error.log"
    )
    echo ===================================================
    echo.
    pause
    exit /b 1
)

echo.
echo AI Image Studio has shut down cleanly.
pause