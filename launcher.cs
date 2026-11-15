using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("FMJJRecordedBroadcast 启动器")]
[assembly: AssemblyDescription("自动识别项目位置并启动 main.py（不打包，仅启动）")]
[assembly: AssemblyProduct("凤鸣工具箱")]
[assembly: AssemblyFileVersion("1.1.0.0")]
[assembly: AssemblyVersion("1.1.0.0")]

internal static class Launcher
{
    private const string EntryFile = "main.py";
    private const string MarkerDir = @"src\gui_qt";   // 项目特征目录，用于确认目录有效
    private const string AppFolderName = "FMJJRecordedBroadcast";
    private const int MaxScanDirs = 6000;             // 浅层扫描的目录预算，防卡顿

    private static int _scanned;

    private static readonly HashSet<string> SkipNames = new HashSet<string>(
        StringComparer.OrdinalIgnoreCase)
    {
        "Windows", "Windows.old", "Program Files", "Program Files (x86)",
        "ProgramData", "AppData", "$Recycle.Bin", "System Volume Information",
        "Recovery", "PerfLogs", "MSOCache", "Config.Msi", "node_modules",
        ".git", ".svn", ".hg", "__pycache__", ".venv", "venv", "site-packages"
    };

    [STAThread]
    private static void Main()
    {
        try
        {
            string appDir = ResolveAppDir();
            if (appDir == null)
                return; // 用户取消选择，静默退出

            string python = FindInterpreter(appDir);
            if (python == null)
            {
                Fail("未找到可用的 Python 解释器（pythonw.exe）。\n\n"
                     + "请先安装 Python 3.12 并勾选 Add Python to PATH，\n"
                     + "或在项目目录下创建 .venv 虚拟环境后重试。");
                return;
            }

            ProcessStartInfo psi = new ProcessStartInfo(python, EntryFile);
            psi.WorkingDirectory = appDir;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            Process.Start(psi);
        }
        catch (Exception ex)
        {
            Fail(ex.Message);
        }
    }

    // ---------- 项目目录自动识别 ----------

    private static string ResolveAppDir()
    {
        // 1. 上次记住的路径（项目搬家后会失效，自动重新查找）
        string remembered = ReadSavedAppDir();
        if (remembered != null && IsAppDir(remembered))
            return remembered;

        // 2. 启动器自身目录及往上两级（exe 放在项目内时直接命中）
        string dir = AppDomain.CurrentDomain.BaseDirectory
            .TrimEnd(Path.DirectorySeparatorChar);
        for (int i = 0; i < 3 && !string.IsNullOrEmpty(dir); i++)
        {
            if (IsAppDir(dir))
            {
                SaveAppDir(dir);
                return dir;
            }
            dir = Path.GetDirectoryName(dir);
        }

        // 3. 各磁盘常见位置 + 浅层扫描
        string found = SearchDrives();
        if (found != null)
        {
            SaveAppDir(found);
            return found;
        }

        // 4. 让用户指定一次，之后记住
        using (FolderBrowserDialog dlg = new FolderBrowserDialog())
        {
            dlg.Description = "未自动找到 " + AppFolderName
                + " 项目目录，请选择包含 main.py 的文件夹：";
            dlg.ShowNewFolderButton = false;
            if (dlg.ShowDialog() != DialogResult.OK)
                return null;

            if (!IsAppDir(dlg.SelectedPath))
            {
                Fail("所选目录不是有效的项目目录（需包含 main.py 与 " + MarkerDir + "）：\n"
                     + dlg.SelectedPath);
                return null;
            }
            SaveAppDir(dlg.SelectedPath);
            return dlg.SelectedPath;
        }
    }

    private static bool IsAppDir(string dir)
    {
        if (string.IsNullOrEmpty(dir))
            return false;
        try
        {
            return File.Exists(Path.Combine(dir, EntryFile))
                && Directory.Exists(Path.Combine(dir, MarkerDir));
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static string SearchDrives()
    {
        // 3.1 各磁盘的常规开发目录，命中即用
        string[] commonFolders = { "PythonApps", "Projects", "Code", "Dev", "GitHub", "workspace" };
        foreach (DriveInfo drive in DriveInfo.GetDrives())
        {
            if (!IsUsableDrive(drive))
                continue;
            string root = drive.RootDirectory.FullName;

            string direct = Path.Combine(root, AppFolderName);
            if (IsAppDir(direct))
                return direct;

            foreach (string folder in commonFolders)
            {
                string candidate = Path.Combine(root, folder, AppFolderName);
                if (IsAppDir(candidate))
                    return candidate;
            }
        }

        // 3.2 浅层扫描（磁盘根目录、桌面、文档、下载、用户目录，两层深度）
        _scanned = 0;
        List<string> roots = new List<string>();
        foreach (DriveInfo drive in DriveInfo.GetDrives())
        {
            if (IsUsableDrive(drive))
                roots.Add(drive.RootDirectory.FullName);
        }
        AddSpecialFolder(roots, Environment.SpecialFolder.Desktop);
        AddSpecialFolder(roots, Environment.SpecialFolder.MyDocuments);
        AddSpecialFolder(roots, Environment.SpecialFolder.UserProfile);

        List<string> hits = new List<string>();
        foreach (string root in roots)
            CollectCandidates(root, 2, hits);

        return PickNewest(hits);
    }

    private static bool IsUsableDrive(DriveInfo drive)
    {
        try
        {
            return drive.DriveType == DriveType.Fixed && drive.IsReady;
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static void AddSpecialFolder(List<string> roots, Environment.SpecialFolder folder)
    {
        try
        {
            string path = Environment.GetFolderPath(folder);
            if (!string.IsNullOrEmpty(path) && !roots.Contains(path))
                roots.Add(path);
        }
        catch (Exception)
        {
        }
    }

    private static void CollectCandidates(string root, int depth, List<string> hits)
    {
        if (depth < 1 || _scanned >= MaxScanDirs)
            return;

        string[] children;
        try
        {
            children = Directory.GetDirectories(root);
        }
        catch (Exception)
        {
            return;
        }

        foreach (string child in children)
        {
            if (++_scanned > MaxScanDirs)
                return;

            string name = Path.GetFileName(child);
            if (SkipNames.Contains(name))
                continue;

            if (IsAppDir(child) && !hits.Contains(child))
                hits.Add(child);

            CollectCandidates(child, depth - 1, hits);
        }
    }

    private static string PickNewest(List<string> dirs)
    {
        string best = null;
        DateTime bestTime = DateTime.MinValue;
        foreach (string dir in dirs)
        {
            try
            {
                DateTime time = File.GetLastWriteTimeUtc(Path.Combine(dir, EntryFile));
                if (best == null || time > bestTime)
                {
                    best = dir;
                    bestTime = time;
                }
            }
            catch (Exception)
            {
            }
        }
        return best;
    }

    // ---------- Python 解释器自动识别 ----------

    private static string FindInterpreter(string appDir)
    {
        // 1. 项目自带虚拟环境
        string[] venvNames = { ".venv", "venv" };
        foreach (string venv in venvNames)
        {
            string candidate = Path.Combine(appDir, venv, @"Scripts\pythonw.exe");
            if (File.Exists(candidate))
                return candidate;
        }

        // 2. 注册表里已安装的 Python（优先 3.12，其次最高 3.x）
        string fromRegistry = FindPythonFromRegistry();
        if (fromRegistry != null)
            return fromRegistry;

        // 3. 用户目录常见安装位置
        string programs = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Programs", "Python");
        string fromUserDir = FindPythonUnder(programs);
        if (fromUserDir != null)
            return fromUserDir;

        // 4. PATH 里的 pythonw / pyw / python
        string[] names = { "pythonw.exe", "pyw.exe", "python.exe" };
        foreach (string name in names)
        {
            string onPath = FindOnPath(name);
            if (onPath != null)
                return onPath;
        }

        // 5. Program Files 下常见安装位置
        string[] programRoots =
        {
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86)
        };
        foreach (string root in programRoots)
        {
            string candidate = FindPythonUnder(root);
            if (candidate != null)
                return candidate;
        }

        return null;
    }

    private static string FindPythonFromRegistry()
    {
        string best = null;
        Version bestVersion = null;
        RegistryHive[] hives = { RegistryHive.CurrentUser, RegistryHive.LocalMachine };
        RegistryView[] views = { RegistryView.Registry64, RegistryView.Registry32 };

        foreach (RegistryHive hive in hives)
        {
            foreach (RegistryView view in views)
            {
                try
                {
                    using (RegistryKey baseKey = RegistryKey.OpenBaseKey(hive, view))
                    using (RegistryKey core = baseKey.OpenSubKey(@"Software\Python\PythonCore"))
                    {
                        if (core == null)
                            continue;

                        foreach (string versionName in core.GetSubKeyNames())
                        {
                            Version version;
                            if (!TryParsePythonVersion(versionName, out version))
                                continue;

                            using (RegistryKey install = core.OpenSubKey(versionName + @"\InstallPath"))
                            {
                                if (install == null)
                                    continue;

                                string path = install.GetValue("WindowedExecutablePath") as string;
                                if (string.IsNullOrEmpty(path) || !File.Exists(path))
                                {
                                    string installDir = install.GetValue(null) as string;
                                    if (string.IsNullOrEmpty(installDir))
                                        continue;
                                    path = Path.Combine(installDir, "pythonw.exe");
                                }
                                if (!File.Exists(path))
                                    continue;

                                // 3.12 视为最稳版本（项目实测环境），其余取最高
                                bool better;
                                if (bestVersion == null)
                                    better = true;
                                else if (IsStableVersion(version))
                                    better = !IsStableVersion(bestVersion);
                                else if (IsStableVersion(bestVersion))
                                    better = false;
                                else
                                    better = version > bestVersion;
                                if (better)
                                {
                                    best = path;
                                    bestVersion = version;
                                }
                            }
                        }
                    }
                }
                catch (Exception)
                {
                }
            }
        }
        return best;
    }

    private static bool IsStableVersion(Version version)
    {
        return version.Major == 3 && version.Minor == 12;
    }

    private static bool TryParsePythonVersion(string name, out Version version)
    {
        version = null;
        string value = (name ?? string.Empty).Split('-')[0].Trim();
        if (!Version.TryParse(value, out version))
            return false;
        return version.Major >= 3;
    }

    private static string FindPythonUnder(string root)
    {
        try
        {
            if (!Directory.Exists(root))
                return null;

            string direct = Path.Combine(root, "pythonw.exe");
            if (File.Exists(direct))
                return direct;

            List<string> candidates = new List<string>();
            foreach (string dir in Directory.GetDirectories(root))
            {
                string name = Path.GetFileName(dir);
                if (name.StartsWith("Python3", StringComparison.OrdinalIgnoreCase))
                    candidates.Add(dir);
            }
            candidates.Sort(StringComparer.OrdinalIgnoreCase);
            candidates.Reverse();

            foreach (string dir in candidates)
            {
                string exe = Path.Combine(dir, "pythonw.exe");
                if (File.Exists(exe))
                    return exe;
            }
        }
        catch (Exception)
        {
        }
        return null;
    }

    private static string FindOnPath(string name)
    {
        string path = Environment.GetEnvironmentVariable("PATH") ?? string.Empty;
        foreach (string raw in path.Split(';'))
        {
            if (string.IsNullOrWhiteSpace(raw))
                continue;
            try
            {
                string candidate = Path.Combine(
                    Environment.ExpandEnvironmentVariables(raw.Trim().Trim('"')), name);
                if (File.Exists(candidate))
                    return candidate;
            }
            catch (Exception)
            {
            }
        }
        return null;
    }

    // ---------- 记忆上次识别到的路径 ----------

    private static string ConfigPath
    {
        get
        {
            string appData = Environment.GetFolderPath(
                Environment.SpecialFolder.ApplicationData);
            return Path.Combine(appData, "FMJJRecordedBroadcast", "launcher.ini");
        }
    }

    private static string ReadSavedAppDir()
    {
        try
        {
            if (!File.Exists(ConfigPath))
                return null;
            foreach (string line in File.ReadAllLines(ConfigPath))
            {
                string text = line.Trim();
                if (text.StartsWith("AppDir=", StringComparison.OrdinalIgnoreCase))
                {
                    string value = text.Substring("AppDir=".Length).Trim().Trim('"');
                    return value.Length > 0 ? value : null;
                }
            }
        }
        catch (Exception)
        {
        }
        return null;
    }

    private static void SaveAppDir(string dir)
    {
        try
        {
            string path = ConfigPath;
            string content = "AppDir=" + dir + Environment.NewLine;
            if (File.Exists(path) && File.ReadAllText(path) == content)
                return;
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, content);
        }
        catch (Exception)
        {
        }
    }

    private static void Fail(string message)
    {
        MessageBox.Show(message, "FMJJRecordedBroadcast 启动失败",
            MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
