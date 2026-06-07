@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo ========================================
echo  夸父 Desktop v1.0.17 本地构建脚本
echo ========================================
echo.

REM 检查 Rust
where rustc >nul 2>&1
if %errorlevel% neq 0 (
    echo [1/5] 安装 Rust...
    winget install --id Rustlang.Rustup --silent --accept-package-agreements >nul 2>&1
    rustup-init -y --quiet >nul 2>&1
    call "%USERPROFILE%\.cargo\cargo_env.bat"
    echo Rust 安装完成
) else (
    echo [1/5] Rust 已安装
)

REM 检查 Node
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [2/5] 安装 Node.js...
    winget install OpenJS.NodeJS --silent --accept-package-agreements >nul 2>&1
) else (
    echo [2/5] Node.js 已安装
)

REM 安装前端依赖
echo [3/5] 安装前端依赖...
cd /d "%~dp0"
call npm ci

REM 前端构建
echo [4/5] 前端构建...
call npm run build

REM Tauri 构建
echo [5/5] Tauri 构建 (NSIS 安装包)...
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
    echo ========================================
    echo  ✗ 构建失败，请检查错误信息
    echo ========================================
)

pause
