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

echo.
echo 完成：dist\FengMingToolboxJava\FengMingToolboxJava.exe
echo 桌面快捷启动脚本：桌面\封茗工具箱 Java版.vbs
exit /b 0

:err
echo.
echo 打包失败，请查看上面的错误信息。
exit /b 1
