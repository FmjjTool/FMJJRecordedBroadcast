' 无 CMD 窗口启动工具箱（双击本文件：软件正常运行，不显示黑窗）
' 优先 pythonw（无控制台解释器）；找不到时退回隐藏窗口的 python
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)

On Error Resume Next
sh.Run "pythonw app_qt.py", 0, False
If Err.Number <> 0 Then
    Err.Clear
    sh.Run "python app_qt.py", 0, False
End If
On Error GoTo 0
