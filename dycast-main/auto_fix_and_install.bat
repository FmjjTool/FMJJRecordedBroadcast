@echo off
setlocal EnableDelayedExpansion

echo ===================================================
echo 环境及依赖自动修复安装脚本 (Windows)
echo ===================================================
echo.

:: 1. 检查并安装 Node.js 和 npm
echo [1/4] 检查 Node.js 和 npm...
node -v >nul 2>&1
if !errorlevel! neq 0 (
    echo [!] 未检测到 Node.js，正在通过 winget 安装 Node.js (建议使用官方渠道)...
    winget install OpenJS.NodeJS
    echo [i] Node.js 安装完成，如果后续命令找不到 npm，请重新打开命令行窗口。
) else (
    echo [i] Node.js 已安装:
    node -v
    npm -v
)
echo.

:: 2. 检查并安装 pnpm
echo [2/4] 检查 pnpm...
pnpm -v >nul 2>&1
if !errorlevel! neq 0 (
    echo [!] 未检测到 pnpm，正在通过 npm 全局安装...
    npm install -g pnpm --registry=https://registry.npmmirror.com
) else (
    echo [i] pnpm 已安装:
    pnpm -v
)
echo.

:: 3. 检查并安装 Python
echo [3/4] 检查 Python...
python --version >nul 2>&1
if !errorlevel! neq 0 (
    echo [!] 未检测到 Python，正在通过 winget 安装 Python 3...
    winget install Python.Python.3.11
    echo [i] Python 安装完成，如果后续命令找不到 python，请重新打开命令行窗口。
) else (
    echo [i] Python 已安装:
    python --version
)
echo.

:: 4. 修复前端项目依赖问题
echo [4/4] 正在修复项目前端依赖 (绕过 SSH, 强制重装)...
echo.

:: (a) 配置 git 将 ssh 替换为 https
echo [-] 配置 Git: 把 GitHub ssh 链接替换为 https...
git config --global url."https://github.com/".insteadOf ssh://git@github.com/
git config --global url."https://github.com/".insteadOf git@github.com:

:: (b) 清理残留
echo [-] 清理 node_modules 和 package-lock.json...
if exist node_modules rmdir /s /q node_modules
if exist package-lock.json del /f /q package-lock.json

echo [-] 清理 npm 缓存...
call npm cache clean --force

:: (c) 如果 package.json 中有 webworkify-webpack，通过 powershell 自动移除 (如有需要)
:: 由于 package.json 里面目前没看到 webworkify-webpack，这步只是兜底
powershell -Command "(Get-Content package.json) -replace '\"webworkify-webpack\": \".*?\",?', '' | Set-Content package.json"

:: (d) 重新安装核心及所有依赖 (使用淘宝镜像)
echo [-] 安装核心依赖 rollup 和 vite...
call npm install rollup vite --save-dev --registry=https://registry.npmmirror.com

echo [-] 重新安装所有依赖...
call npm install --registry=https://registry.npmmirror.com

echo.
echo ===================================================
echo 修复完成！
echo 请检查上方是否有错误信息。如果没有，可以尝试运行 npm run dev 或 start.bat
echo ===================================================
pause
