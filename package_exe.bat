@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set JAVA_HOME=C:\Users\xinmu\.jdks\openjdk-27

echo [1/3] Maven 打包（含依赖复制到 target/lib）...
call C:\Users\xinmu\.workbuddy-ai\tools\maven\bin\mvn.cmd clean package -DskipTests || goto :err

echo [2/3] 复制应用 jar 到 lib...
copy /y target\fengming-toolbox-java-2.9.1.jar target\lib\ >nul || goto :err

echo [3/3] jpackage 生成 app-image（dist\FengMingToolboxJava）...
if exist dist rmdir /s /q dist
"%JAVA_HOME%\bin\jpackage" --type app-image --name FengMingToolboxJava ^
  --input target\lib --main-jar fengming-toolbox-java-2.9.1.jar ^
  --main-class cn.fmjj.toolbox.Launcher --dest dist --app-version 2.9.1 ^
  --vendor FMJJ --java-options "-Dfile.encoding=UTF-8" || goto :err

rem ---- 隐私校验：分发包不应含用户数据 ----
set LEAK=0
for %%D in (config download logs) do (
  if exist dist\FengMingToolboxJava\app\%%D (
    if /i not "%%D"=="config" (
      echo [警告] 包里含 %%D 目录，请清理后再分发
      set LEAK=1
    )
  )
)

rem ---- 登录态：自用包才复制（package_exe.bat keep）----
if /i "%1"=="keep" (
  if exist config\cookies.json (
    if not exist dist\FengMingToolboxJava\app\config mkdir dist\FengMingToolboxJava\app\config
    copy /y config\cookies.json dist\FengMingToolboxJava\app\config\cookies.json >nul
    echo 已恢复登录态（自用包）：app\config\cookies.json
  )
) else (
  if exist dist\FengMingToolboxJava\app\config\cookies.json del /q dist\FengMingToolboxJava\app\config\cookies.json
  echo 分发包：不含登录态（自用请执行 package_exe.bat keep）
)

echo.
echo 完成：dist\FengMingToolboxJava\FengMingToolboxJava.exe
echo 桌面快捷启动脚本：桌面\封茗工具箱 Java版.vbs
exit /b 0

:err
echo.
echo 打包失败，请查看上面的错误信息。
exit /b 1
