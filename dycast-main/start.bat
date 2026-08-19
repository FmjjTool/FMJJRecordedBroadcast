@echo off
chcp 65001 >nul
title 抖音弹幕姬 - DyCast

echo ====================================
echo   抖音弹幕姬 启动中...
echo ====================================
echo.

cd /d "%~dp0"

:: 1. 检测 Node.js（优先系统安装目录，避免被 WorkBuddy 内嵌 node 的坏 shim 干扰）
set "NODE_EXE="
if exist "C:\Program Files\nodejs\node.exe" (
    set "NODE_EXE=C:\Program Files\nodejs\node.exe"
)
if not defined NODE_EXE (
    where node >nul 2>&1
    if errorlevel 1 (
        echo [!] 未检测到 Node.js，请先安装 Node.js v18 或以上
        echo     下载: https://nodejs.org/zh-cn
        pause
        exit /b 1
    )
    set "NODE_EXE=node"
)

:: 2. 定位 npm（优先系统安装目录，回退 PATH 里的 npm；不依赖 pnpm，避开坏 shim）
set "NPM_CMD="
if exist "C:\Program Files\nodejs\npm.cmd" set "NPM_CMD=C:\Program Files\nodejs\npm.cmd"
if not defined NPM_CMD set "NPM_CMD=npm"

echo [i] Node.js: %NODE_EXE%
echo [i] npm: %NPM_CMD%
echo.

:: 3. 依赖不存在才安装（国内镜像加速）
if not exist "node_modules" (
    echo [*] 检测到前端依赖缺失，正在自动安装（首次约 1-3 分钟，请耐心等待）...
    call "%NPM_CMD%" install --registry=https://registry.npmmirror.com
    if errorlevel 1 (
        echo.
        echo [!] 依赖安装失败，请检查网络后重试
        pause
        exit /b 1
    )
    echo [√] 依赖安装完成
    echo.
) else (
    echo [√] 依赖已存在，跳过安装
    echo.
)

:: 4. 启动开发服务器
echo [*] 正在启动开发服务器，启动后请在浏览器打开显示的地址...
echo.
call "%NPM_CMD%" run dev
pause
