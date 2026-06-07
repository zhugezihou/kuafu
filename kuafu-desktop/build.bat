@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo ========================================
echo  夸父 Desktop v1.0.17 本地构建脚本
echo  放 kuafu-desktop 目录下双击运行
echo ========================================
echo.

cd /d "%~dp0"

REM 检查 Rust
where rustc >nul 2>&1
if %errorlevel% neq 0 (
    echo [1/6] 安装 Rust...
    winget install --id Rustlang.Rustup --silent --accept-package-agreements >nul 2>&1
    start /wait rustup-init -y --quiet >nul 2>&1
    call "%USERPROFILE%\.cargo\cargo_env.bat"
    echo Rust 安装完成
) else (
    echo [1/6] Rust 已安装
)

REM 检查 Node
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [2/6] 安装 Node.js...
    winget install OpenJS.NodeJS --silent --accept-package-agreements >nul 2>&1
) else (
    echo [2/6] Node.js 已安装
)

REM 安装前端依赖
echo [3/6] 安装前端依赖...
call npm ci

REM 下载嵌入式 Python
echo [4/6] 准备嵌入式 Python...
set PYTHON_DIR=src-tauri\python
if exist "%PYTHON_DIR%\python.exe" (
    echo 嵌入式 Python 已存在，跳过下载
) else (
    md "%PYTHON_DIR%" 2>nul
    echo 下载 Python 3.12.9 embedded...
    curl.exe -L -o "%TEMP%\python.zip" https://www.python.org/ftp/python/3.12.9/python-3.12.9-embed-amd64.zip
    echo 解压...
    powershell -Command "Expand-Archive -Path '$env:TEMP\python.zip' -DestinationPath '%PYTHON_DIR%' -Force"
    echo 配置 python._pth...
    echo ..\kuafu>> "%PYTHON_DIR%\python._pth"
    echo import site>> "%PYTHON_DIR%\python._pth"
)

REM 复制夸父源码
echo [5/6] 复制夸父源码...
set KUAFFU_DIR=%PYTHON_DIR%\kuafu
if not exist "%KUAFFU_DIR%" md "%KUAFFU_DIR%"
xcopy /E /I /Y ..\core "%KUAFFU_DIR%\core\" >nul
copy /Y ..\pyproject.toml "%KUAFFU_DIR%\" >nul

REM 前端构建
echo [6/6] 前端构建 + Tauri 打包...
call npm run build
if %errorlevel% neq 0 (
    echo 前端构建失败
    pause
    exit /b 1
)

call npm run tauri build -- --bundles nsis
if %errorlevel% equ 0 (
    echo.
    echo ========================================
    echo  ✓ 构建成功！
    for /r src-tauri\target\release\bundle\nsis %%f in (*.exe) do (
        echo 安装包: %%f
    )
    echo ========================================
) else (
    echo.
    echo  ✗ 构建失败
)
pause
