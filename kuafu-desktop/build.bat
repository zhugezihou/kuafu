@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo ========================================
echo  Kuafu Desktop - Local Build Script
echo  Step-by-step: dependencies ^> frontend ^> Rust
echo ========================================
echo.

cd /d "%~dp0"

REM ---- 1. Check Rust ----
where rustc >nul 2>&1
if %errorlevel% neq 0 (
    echo [1/6] Installing Rust...
    winget install --id Rustlang.Rustup --silent --accept-package-agreements >nul 2>&1
    start /wait rustup-init -y --quiet >nul 2>&1
    call "%USERPROFILE%\.cargo\cargo_env.bat"
    echo Rust installed.
) else (
    echo [1/6] Rust OK
)

REM ---- 2. Check Node.js ----
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [2/6] Installing Node.js...
    winget install OpenJS.NodeJS.LTS --silent --accept-package-agreements >nul 2>&1
    echo Node.js installed.
) else (
    echo [2/6] Node.js OK
)

REM ---- 3. npm install ----
echo [3/6] Installing frontend dependencies...
call npm install
if %errorlevel% neq 0 (
    echo npm install failed
    pause
    exit /b 1
)
echo Frontend dependencies installed.

REM ---- 4. Frontend build ----
echo [4/6] Building frontend...
call npm run build
if %errorlevel% neq 0 (
    echo Frontend build failed
    pause
    exit /b 1
)
echo Frontend build OK.

REM ---- 5. Prepare embedded Python ----
echo [5/6] Preparing embedded Python...
set "OUT=%~dp0src-tauri\python"
if not exist "%OUT%\python.exe" (
    echo Downloading embedded Python 3.12.9...
    curl.exe -L -o "%TEMP%\python.zip" "https://www.python.org/ftp/python/3.12.9/python-3.12.9-embed-amd64.zip"
    echo Extracting...
    powershell -Command "Expand-Archive -Path '%TEMP%\python.zip' -DestinationPath '%OUT%' -Force"
    echo Copying kuafu source...
    mkdir "%OUT%\kuafu" 2>nul
    xcopy /E /I /Y "%~dp0..\core" "%OUT%\kuafu\core\"
    copy /Y "%~dp0..\pyproject.toml" "%OUT%\kuafu\" >nul
    REM configure python._pth
    echo.>>"%OUT%\python._pth"
    echo ..\kuafu>>"%OUT%\python._pth"
    echo import site>>"%OUT%\python._pth"
    echo Embedded Python ready.
) else (
    echo [5/6] Embedded Python already exists
)

REM ---- 6. Tauri build ----
echo [6/6] Building Tauri desktop app...
call npm run tauri build
if %errorlevel% neq 0 (
    echo Tauri build failed!
    pause
    exit /b 1
)

echo ========================================
echo  Build complete!
echo  Installer: src-tauri\target\release\bundle\nsis\*.exe
echo ========================================
pause
