using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class ClientLauncher
{
    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string python = Path.Combine(root, ".venv", "Scripts", "pythonw.exe");
            if (!File.Exists(python) || !File.Exists(Path.Combine(root, "src", "api_post", "desktop.py")))
                throw new InvalidOperationException("Project runtime missing");
            bool smoke = args.Length == 1 && args[0] == "--smoke-test";
            var start = new ProcessStartInfo(python, "-m api_post.desktop" + (smoke ? " --smoke-test" : ""));
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            using (Process child = Process.Start(start))
            {
                if (smoke) { child.WaitForExit(); return child.ExitCode; }
            }
            return 0;
        }
        catch
        {
            MessageBox.Show("无法启动 ReqDock。请保留启动程序与项目目录在一起，并查看 workspace/runtime/desktop.log。", "ReqDock", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
