// GeekTray - 极客输入法管家
// 常驻托盘，右键菜单承载极客输入法全部管理功能
// 编译: csc /target:winexe /codepage:65001 /out:GeekTray.exe GeekTray.cs
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace GeekIME
{
    static class Program
    {
        public static void Log(string msg)
        {
            try { File.AppendAllText(@"F:\7.输入法\geek_tray.log", DateTime.Now + "  " + msg + Environment.NewLine); } catch { }
        }

        [STAThread]
        static void Main()
        {
            bool created;
            var mutex = new Mutex(true, "GeekIME_GeekTray_SingleInstance", out created);
            if (!created)
            {
                // 已有管家在运行：直接打开 Web 管理台
                Log("second instance -> open web");
                try { Process.Start(Theme.WebUrl); } catch { }
                return;
            }
            Log("started");
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.ThreadException += (s, e) => Log("THREAD-ERROR: " + e.Exception);
            AppDomain.CurrentDomain.UnhandledException += (s, e) => Log("UNHANDLED: " + e.ExceptionObject);
            try
            {
                Application.Run(new TrayContext());
            }
            catch (Exception ex)
            {
                Log("CRASH: " + ex);
            }
            Log("exited");
            GC.KeepAlive(mutex);
        }
    }

    // ─────────────────────────────── 配色 ───────────────────────────────
    static class Theme
    {
        public static readonly Color Bg = Color.FromArgb(28, 28, 36);        // 菜单底
        public static readonly Color Hover = Color.FromArgb(58, 58, 78);     // 悬停
        public static readonly Color Text = Color.FromArgb(235, 235, 245);   // 文字
        public static readonly Color Dim = Color.FromArgb(140, 140, 160);    // 次要文字
        public static readonly Color Accent = Color.FromArgb(124, 108, 255); // 极客紫
        public const string RimeDir = @"F:\7.输入法";
        public const string WeaselDir = @"F:\12.Rime\weasel-0.17.4";
        public const int DefaultPort = 8765;
        // Web 管理台实际端口写在 web_port.txt（8765 被占用时 app.py 会自动顺延）
        public static readonly string PortFile = System.IO.Path.Combine(RimeDir, @"geek_web\web_port.txt");
        public static string WebUrl
        {
            get
            {
                int p = DefaultPort;
                try
                {
                    if (System.IO.File.Exists(PortFile))
                    {
                        int v;
                        if (int.TryParse(System.IO.File.ReadAllText(PortFile).Trim(), out v) && v > 0 && v < 65536) p = v;
                    }
                }
                catch { }
                return "http://127.0.0.1:" + p;
            }
        }
    }

    // ─────────────────────────────── 托盘主体 ───────────────────────────────
    class TrayContext : ApplicationContext
    {
        private NotifyIcon tray;
        private ContextMenuStrip menu;
        private Form _host;                    // 隐藏宿主窗体（UI 线程回调用）
        private System.Windows.Forms.Timer _clipTimer; // 剪贴板轮询
        private string _lastSeen = "";         // 上次见到的剪贴板内容
        private string _ownWrite = "";         // 自己写回的内容（防自触发）
        private bool _aiBusy = false;          // AI 问答进行中
        private ToolStripMenuItem _aiSwitch;   // AI 开关菜单项

        public TrayContext()
        {
            _host = new Form
            {
                ShowInTaskbar = false,
                FormBorderStyle = FormBorderStyle.None,
                Opacity = 0,
                StartPosition = FormStartPosition.Manual,
                Location = new Point(-3000, -3000),
                Size = new Size(1, 1)
            };
            _host.Show();

            menu = BuildMenu();
            tray = new NotifyIcon();
            tray.Icon = MakeIcon();
            tray.Text = "极客输入法管家";
            tray.ContextMenuStrip = menu;
            tray.Visible = true;
            tray.DoubleClick += (s, e) => OpenWeb();
            tray.ShowBalloonTip(2000, "极客输入法管家", "已启动 ✓\n右键图标 = 管理菜单\n双击图标 = Web 管理台", ToolTipIcon.Info);

            _clipTimer = new System.Windows.Forms.Timer { Interval = 800 };
            _clipTimer.Tick += (s, e) => PollClipboard();
            _clipTimer.Start();

            // 开机即静默拉起 Web 管理台服务（pythonw 无窗口）
            Task.Run(() => EnsureWeb());
        }

        // 确保 Web 管理台服务在跑（静默，无任何窗口）
        private void EnsureWeb()
        {
            if (WebAlive(new Uri(Theme.WebUrl).Port)) return;
            var psi = new ProcessStartInfo
            {
                FileName = "pythonw.exe",
                Arguments = "-X utf8 \"" + Theme.RimeDir + @"\geek_web\app.py" + "\"",
                WorkingDirectory = Theme.RimeDir + @"\geek_web",
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            };
            try { Process.Start(psi); } catch { }
            // 端口可能被占用而顺延，每次都重新读 web_port.txt
            for (int i = 0; i < 24 && !WebAlive(new Uri(Theme.WebUrl).Port); i++) Thread.Sleep(300);
        }

        // ── 菜单构建 ──
        private ContextMenuStrip BuildMenu()
        {
            var m = new ContextMenuStrip();
            m.Renderer = new GeekRenderer();
            m.ShowImageMargin = true;
            m.Font = new Font("Microsoft YaHei UI", 9.5f);

            var header = new ToolStripMenuItem("  ⚡ 极客输入法管家") { Enabled = false };
            header.ForeColor = Theme.Accent;

            var web = Item("打开 Web 管理台", Icons.Globe, (s, e) => OpenWeb());
            var phrase = Item("快速添加自定义短语…", Icons.Plus, (s, e) => QuickPhrase());

            var deploy = Item("重新部署（后台无头）", Icons.Refresh, (s, e) => { DeployFast(); });
            var restart = Item("重启输入法服务", Icons.Power, (s, e) => { RestartServer(); });

            // ── AI 助手区块 ──
            _aiSwitch = Item("AI 剪贴板问答", Icons.Spark, (s, e) =>
            {
                var cfg = AiHelper.Load();
                cfg.Enabled = !cfg.Enabled;
                AiHelper.Save(cfg);
                _aiSwitch.Checked = cfg.Enabled;
                _lastSeen = ""; // 重置，避免旧内容误触发
                tray.ShowBalloonTip(2000, "AI 剪贴板问答",
                    cfg.Enabled ? "已开启 ✓ 复制任何内容即自动向 AI 提问，答案写回剪贴板" : "已关闭",
                    ToolTipIcon.Info);
            });
            _aiSwitch.Checked = AiHelper.Load().Enabled;
            var aiOnce = Item("立即问 AI（当前剪贴板）", Icons.Spark, (s, e) =>
            {
                string t = "";
                try { t = Clipboard.GetText(); } catch { }
                if (string.IsNullOrWhiteSpace(t)) { tray.ShowBalloonTip(2000, "AI", "剪贴板是空的", ToolTipIcon.Warning); return; }
                _lastSeen = t;
                AskAi(t);
            });

            var settings = Item("输入法设定 / 方案选单", Icons.Gear, (s, e) => Start(Theme.WeaselDir + @"\WeaselDeployer.exe", ""));
            var userDir = Item("打开用户文件夹", Icons.Folder, (s, e) => Start("explorer.exe", "\"" + Theme.RimeDir + "\""));
            var engMem = Item("编辑英文记忆词库", Icons.Book, (s, e) => Start("notepad.exe", "\"" + Theme.RimeDir + @"\english_user.txt" + "\""));

            var autostart = Item("开机自动启动（输入法+管家）", Icons.Bolt, (s, e) =>
            {
                ToggleAutostart();
                ((ToolStripMenuItem)s).Checked = AutostartEnabled();
            });
            autostart.Checked = AutostartEnabled();

            var about = Item("关于极客输入法", Icons.Info, (s, e) =>
                MessageBox.Show("极客输入法 GeekIME\n基于小狼毫 Weasel 0.17.4 (GPLv3) 深度定制\n\n双拼输入 · 英文模式 · v模式 · 自定义短语\nWeb 管理台: " + Theme.WebUrl + "\n\n功能: 托盘右键 / 双击托盘打开管理台",
                    "关于", MessageBoxButtons.OK, MessageBoxIcon.Information));

            var quit = Item("退出管家", Icons.Exit, (s, e) =>
            {
                var go = MessageBox.Show("确定退出极客管家？\n\n退出后托盘图标消失，需要手动运行 GeekTray.exe 才能重新启动。",
                    "退出确认", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
                if (go != DialogResult.Yes) return;
                Program.Log("quit by user");
                tray.Visible = false;
                Application.Exit();
            });

            m.Items.Add(header);
            m.Items.Add(new ToolStripSeparator());
            m.Items.Add(web);
            m.Items.Add(phrase);
            m.Items.Add(new ToolStripSeparator());
            m.Items.Add(_aiSwitch);
            m.Items.Add(aiOnce);
            m.Items.Add(new ToolStripSeparator());
            m.Items.Add(deploy);
            m.Items.Add(restart);
            m.Items.Add(new ToolStripSeparator());
            m.Items.Add(settings);
            m.Items.Add(userDir);
            m.Items.Add(engMem);
            m.Items.Add(new ToolStripSeparator());
            m.Items.Add(autostart);
            m.Items.Add(about);
            m.Items.Add(new ToolStripSeparator());
            m.Items.Add(quit);
            return m;
        }

        private ToolStripMenuItem Item(string text, char icon, EventHandler onClick)
        {
            var it = new ToolStripMenuItem("  " + text, null, onClick);
            it.Image = IconFromChar(icon).ToBitmap();
            it.ForeColor = Theme.Text;
            return it;
        }

        // ── 功能：Web 管理台 ──
        // 端口上有响应不算数，要确认是我们的管理台（避免误判别的程序占用端口）
        private bool WebAlive(int port)
        {
            if (port <= 0) return false;
            try
            {
                var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + port + "/api/ping");
                req.Timeout = 800;
                req.Proxy = null;
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var sr = new StreamReader(resp.GetResponseStream()))
                    return sr.ReadToEnd().IndexOf("GeekIME", StringComparison.Ordinal) >= 0;
            }
            catch { return false; }
        }

        private void OpenWeb()
        {
            EnsureWeb();
            Start(Theme.WebUrl, "");
        }

        // ── 功能：快速添加自定义短语 ──
        private void QuickPhrase()
        {
            using (var f = new QuickPhraseForm())
            {
                if (f.ShowDialog() != DialogResult.OK) return;
                if (string.IsNullOrEmpty(f.Word) || string.IsNullOrEmpty(f.Code)) return;

                if (!WebAlive(new Uri(Theme.WebUrl).Port)) EnsureWeb(); // 后台服务没起来就先静默拉起

                var payload = new JavaScriptSerializer().Serialize(new Dictionary<string, object>
                {
                    { "word", f.Word }, { "code", f.Code }, { "weight", f.Weight }
                });
                string resp = PostJson("/api/phrases", payload);
                if (resp == null)
                {
                    tray.ShowBalloonTip(4000, "极客输入法", "Web 管理台未响应，添加失败", ToolTipIcon.Error);
                    return;
                }
                string err = null;
                try
                {
                    var d = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    if (d != null && d.ContainsKey("error")) err = Convert.ToString(d["error"]);
                }
                catch { }

                if (err != null)
                    tray.ShowBalloonTip(4000, "添加失败", Truncate(err, 100), ToolTipIcon.Error);
                else
                    tray.ShowBalloonTip(3000, "极客输入法",
                        "已添加：" + f.Word + "（" + f.Code + "）\n已自动分组，正在后台部署…", ToolTipIcon.Info);
            }
        }

        // 向本地 Web 管理台发 JSON POST（静默，失败返回 null）
        private static string PostJson(string path, string json)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(Theme.WebUrl.TrimEnd('/') + path);
                req.Method = "POST";
                req.ContentType = "application/json; charset=utf-8";
                req.Timeout = 10000;
                req.Proxy = null;
                byte[] data = Encoding.UTF8.GetBytes(json);
                req.ContentLength = data.Length;
                using (var s = req.GetRequestStream()) s.Write(data, 0, data.Length);
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                    return sr.ReadToEnd();
            }
            catch (WebException wex)
            {
                // 400/409 等错误响应里带 JSON，同样回传给调用方判断
                try
                {
                    using (var er = wex.Response)
                    using (var sr = new StreamReader(er.GetResponseStream(), Encoding.UTF8))
                        return sr.ReadToEnd();
                }
                catch { }
                Program.Log("PostJson " + path + " failed: " + wex.Message);
                return null;
            }
            catch (Exception ex)
            {
                Program.Log("PostJson " + path + " failed: " + ex.Message);
                return null;
            }
        }

        // ── AI 剪贴板问答 ──
        private void PollClipboard()
        {
            try
            {
                var cfg = AiHelper.Peek();
                if (cfg == null || !cfg.Enabled || _aiBusy) return;
                string text = Clipboard.GetText();
                if (string.IsNullOrWhiteSpace(text)) return;
                if (text == _lastSeen) return;
                _lastSeen = text;
                if (text == _ownWrite) return; // 自己写回的，不触发
                if (string.IsNullOrEmpty(cfg.BaseUrl) || string.IsNullOrEmpty(cfg.ApiKey) || string.IsNullOrEmpty(cfg.Model)) return;
                AskAi(text);
            }
            catch { } // 剪贴板被占用等情况静默跳过
        }

        private void AskAi(string question)
        {
            var cfg = AiHelper.Load();
            if (string.IsNullOrEmpty(cfg.BaseUrl) || string.IsNullOrEmpty(cfg.ApiKey) || string.IsNullOrEmpty(cfg.Model))
            {
                tray.ShowBalloonTip(2500, "AI 未配置", "请到 Web 管理台「AI 助手」页填写 API 地址、Key 和模型", ToolTipIcon.Warning);
                return;
            }
            _aiBusy = true;
            tray.ShowBalloonTip(1200, "AI 思考中…", Truncate(question, 60), ToolTipIcon.Info);
            string url = AiHelper.BuildUrl(cfg.BaseUrl);
            string body = AiHelper.MakeBody(cfg, question);
            string err = null;
            Task.Run(() =>
            {
                string answer = AiHelper.Call(url, cfg.ApiKey, body, out err);
                _host.BeginInvoke((Action)(() =>
                {
                    _aiBusy = false;
                    if (err != null)
                    {
                        tray.ShowBalloonTip(3000, "AI 请求失败", Truncate(err, 120), ToolTipIcon.Error);
                        AiHelper.LogHistory(question, "[错误] " + err);
                        return;
                    }
                    try
                    {
                        _ownWrite = answer;
                        Clipboard.SetText(answer);
                    }
                    catch { }
                    tray.ShowBalloonTip(2500, "AI 已回答 ✓", Truncate(answer, 100), ToolTipIcon.Info);
                    AiHelper.LogHistory(question, answer);
                }));
            });
        }

        private static string Truncate(string s, int n)
        {
            if (string.IsNullOrEmpty(s)) return "";
            s = s.Replace("\r", "").Replace("\n", " ");
            return s.Length <= n ? s : s.Substring(0, n) + "…";
        }

        // ── 功能：部署（全程后台无头，不弹任何窗口） ──
        private void DeployFast()
        {
            tray.ShowBalloonTip(1500, "极客输入法", "正在后台重新部署…", ToolTipIcon.Info);
            Task.Run(() =>
            {
                bool ok = false;
                string err = null;
                try
                {
                    foreach (var p in Process.GetProcessesByName("WeaselServer"))
                    {
                        try { p.Kill(); } catch { }
                    }
                    Thread.Sleep(1200);

                    var psi = new ProcessStartInfo
                    {
                        FileName = "powershell.exe",
                        Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + Theme.RimeDir + @"\do_build.ps1" + "\"",
                        UseShellExecute = false,
                        CreateNoWindow = true,
                        WindowStyle = ProcessWindowStyle.Hidden,
                        RedirectStandardOutput = true,
                        RedirectStandardError = true
                    };
                    var proc = Process.Start(psi);
                    proc.OutputDataReceived += (s, e) => { };
                    proc.ErrorDataReceived += (s, e) => { };
                    proc.BeginOutputReadLine();
                    proc.BeginErrorReadLine();
                    if (!proc.WaitForExit(900000)) err = "部署超时（超过 15 分钟）";
                    else ok = proc.ExitCode == 0;
                }
                catch (Exception ex) { err = ex.Message; }

                Start(Theme.WeaselDir + @"\WeaselServer.exe", "");
                Program.Log("deploy headless ok=" + ok + " err=" + err);
                if (err != null)
                    tray.ShowBalloonTip(4000, "极客输入法", "部署失败：" + Truncate(err, 80), ToolTipIcon.Error);
                else if (ok)
                    tray.ShowBalloonTip(3000, "极客输入法", "部署完成，已生效 ✓", ToolTipIcon.Info);
                else
                    tray.ShowBalloonTip(4000, "极客输入法", "部署失败，请查看 .buildlog 目录", ToolTipIcon.Error);
            });
        }

        private void RestartServer()
        {
            try { foreach (var p in Process.GetProcessesByName("WeaselServer")) p.Kill(); } catch { }
            Thread.Sleep(1200);
            Start(Theme.WeaselDir + @"\WeaselServer.exe", "");
            tray.ShowBalloonTip(2000, "极客输入法", "输入法服务已重启 ✓", ToolTipIcon.Info);
        }

        // ── 功能：开机自启（HKCU Run）──
        private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";

        private bool AutostartEnabled()
        {
            using (var k = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(RunKey, false))
                return k != null && k.GetValue("GeekIME_Tray") != null && k.GetValue("GeekIME_WeaselServer") != null;
        }

        private void ToggleAutostart()
        {
            using (var k = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(RunKey, true))
            {
                if (AutostartEnabled())
                {
                    k.DeleteValue("GeekIME_Tray", false);
                    k.DeleteValue("GeekIME_WeaselServer", false);
                }
                else
                {
                    k.SetValue("GeekIME_WeaselServer", "\"" + Theme.WeaselDir + @"\WeaselServer.exe" + "\"");
                    k.SetValue("GeekIME_Tray", "\"" + Application.ExecutablePath + "\"");
                }
            }
            tray.ShowBalloonTip(2000, "极客输入法",
                AutostartEnabled() ? "已开启开机自启：登录后输入法立即就绪 ✓" : "已关闭开机自启",
                ToolTipIcon.Info);
        }

        // ── 工具 ──
        private void Start(string file, string args)
        {
            try { Process.Start(file, args); } catch { }
        }

        // ── 图标生成（蓝紫圆角 + 极）──
        private Icon MakeIcon()
        {
            using (var b = new Bitmap(64, 64))
            using (var g = Graphics.FromImage(b))
            using (var br = new LinearGradientBrush(new Rectangle(0, 0, 64, 64),
                Color.FromArgb(124, 108, 255), Color.FromArgb(56, 189, 248), 45f))
            {
                g.SmoothingMode = SmoothingMode.AntiAlias;
                using (var path = RoundRect(2, 2, 60, 60, 16)) g.FillPath(br, path);
                using (var f = new Font("Microsoft YaHei UI", 26f, FontStyle.Bold, GraphicsUnit.Pixel))
                using (var sf = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
                    g.DrawString("极", f, Brushes.White, new RectangleF(0, 2, 64, 62), sf);
                return Icon.FromHandle(b.GetHicon());
            }
        }

        private Icon IconFromChar(char c)
        {
            using (var b = new Bitmap(20, 20))
            using (var g = Graphics.FromImage(b))
            {
                g.SmoothingMode = SmoothingMode.AntiAlias;
                g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAlias;
                using (var f = new Font("Segoe UI Symbol", 11f, FontStyle.Bold))
                using (var sf = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
                    g.DrawString(c.ToString(), f, new SolidBrush(Theme.Accent), new RectangleF(0, 0, 20, 20), sf);
                return Icon.FromHandle(b.GetHicon());
            }
        }

        private static GraphicsPath RoundRect(int x, int y, int w, int h, int r)
        {
            var p = new GraphicsPath();
            p.AddArc(x, y, 2 * r, 2 * r, 180, 90);
            p.AddArc(x + w - 2 * r, y, 2 * r, 2 * r, 270, 90);
            p.AddArc(x + w - 2 * r, y + h - 2 * r, 2 * r, 2 * r, 0, 90);
            p.AddArc(x, y + h - 2 * r, 2 * r, 2 * r, 90, 90);
            p.CloseFigure();
            return p;
        }
    }

    // ─────────────────────────────── 图标字符 ───────────────────────────────
    static class Icons
    {
        public const char Globe = '\uE774';   // 网络
        public const char Plus = '\uE710';    // 加号
        public const char Refresh = '\uE72C'; // 刷新
        public const char Hammer = '\uE8AB';  // 工具
        public const char Power = '\uE7E8';   // 电源
        public const char Gear = '\uE713';    // 齿轮
        public const char Folder = '\uE8B7';  // 文件夹
        public const char Book = '\uE8F1';    // 书
        public const char Bolt = '\uE945';    // 闪电
        public const char Info = '\uE946';    // 信息
        public const char Exit = '\uE7E8';    // 退出
        public const char Spark = '\uE99A';   // 星火（AI）
    }

    // ─────────────────────────────── 深色菜单渲染 ───────────────────────────────
    class GeekRenderer : ToolStripProfessionalRenderer
    {
        public GeekRenderer() : base(new GeekColors()) { }

        protected override void OnRenderMenuItemBackground(ToolStripItemRenderEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            var r = new Rectangle(Point.Empty, e.Item.Size);
            if (e.Item.Selected || e.Item.Pressed)
            {
                using (var b = new SolidBrush(Theme.Hover))
                    FillRound(g, b, new Rectangle(3, 2, r.Width - 6, r.Height - 4));
            }
            else
            {
                using (var b = new SolidBrush(Theme.Bg))
                    g.FillRectangle(b, r);
            }
        }

        private void FillRound(Graphics g, Brush b, Rectangle r)
        {
            using (var p = new GraphicsPath())
            {
                int rad = 6;
                p.AddArc(r.X, r.Y, rad * 2, rad * 2, 180, 90);
                p.AddArc(r.Right - rad * 2, r.Y, rad * 2, rad * 2, 270, 90);
                p.AddArc(r.Right - rad * 2, r.Bottom - rad * 2, rad * 2, rad * 2, 0, 90);
                p.AddArc(r.X, r.Bottom - rad * 2, rad * 2, rad * 2, 90, 90);
                p.CloseFigure();
                g.FillPath(b, p);
            }
        }

        protected override void OnRenderToolStripBackground(ToolStripRenderEventArgs e)
        {
            using (var b = new SolidBrush(Theme.Bg))
                e.Graphics.FillRectangle(b, e.AffectedBounds);
        }

        protected override void OnRenderToolStripBorder(ToolStripRenderEventArgs e)
        {
            using (var b = new SolidBrush(Color.FromArgb(60, 60, 76)))
                e.Graphics.DrawRectangle(Pens.Transparent, Rectangle.Empty); // 无边框
        }

        protected override void OnRenderSeparator(ToolStripSeparatorRenderEventArgs e)
        {
            using (var b = new SolidBrush(Theme.Bg))
                e.Graphics.FillRectangle(b, new Rectangle(Point.Empty, e.Item.Size));
            using (var pen = new Pen(Color.FromArgb(58, 58, 74)))
                e.Graphics.DrawLine(pen, 30, e.Item.Height / 2, e.Item.Width - 8, e.Item.Height / 2);
        }

        protected override void OnRenderItemText(ToolStripItemTextRenderEventArgs e)
        {
            e.TextColor = e.Item.Enabled ? Theme.Text : Theme.Dim;
            base.OnRenderItemText(e);
        }

        protected override void OnRenderImageMargin(ToolStripRenderEventArgs e)
        {
            using (var b = new SolidBrush(Theme.Bg))
                e.Graphics.FillRectangle(b, e.AffectedBounds);
        }
    }

    class GeekColors : ProfessionalColorTable
    {
        public override Color ImageMarginGradientBegin { get { return Theme.Bg; } }
        public override Color ImageMarginGradientMiddle { get { return Theme.Bg; } }
        public override Color ImageMarginGradientEnd { get { return Theme.Bg; } }
        public override Color MenuBorder { get { return Color.FromArgb(64, 64, 82); } }
        public override Color MenuItemBorder { get { return Color.Transparent; } }
        public override Color SeparatorDark { get { return Theme.Bg; } }
        public override Color SeparatorLight { get { return Theme.Bg; } }
    }

    // ─────────────────────────────── AI 助手 ───────────────────────────────
class AiConfig
{
    public string BaseUrl { get; set; }
    public string ApiKey { get; set; }
    public string Model { get; set; }
    public string SystemPrompt { get; set; }
    public bool Enabled { get; set; }
}

static class AiHelper
{
    public const string ConfigPath = @"F:\7.输入法\ai_config.json";
    public const string HistoryPath = @"F:\7.输入法\ai_history.log";
    private static DateTime _lastMtime;
    private static AiConfig _cache;

    static AiHelper()
    {
        ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
    }

    public static AiConfig Load()
    {
        try
        {
            if (!File.Exists(ConfigPath)) return new AiConfig { SystemPrompt = "你是快捷问答助手，简洁、直接地回答问题。" };
            var js = new JavaScriptSerializer();
            var c = js.Deserialize<AiConfig>(File.ReadAllText(ConfigPath, Encoding.UTF8));
            if (c.SystemPrompt == null) c.SystemPrompt = "你是快捷问答助手，简洁、直接地回答问题。";
            return c ?? new AiConfig();
        }
        catch { return new AiConfig(); }
    }

    // 低频轮询用的缓存版（每 3 秒最多重读一次文件）
    public static AiConfig Peek()
    {
        try
        {
            if (!File.Exists(ConfigPath)) return null;
            var mt = File.GetLastWriteTimeUtc(ConfigPath);
            if (_cache == null || mt != _lastMtime) { _cache = Load(); _lastMtime = mt; }
            return _cache;
        }
        catch { return null; }
    }

    public static void Save(AiConfig c)
    {
        var js = new JavaScriptSerializer();
        File.WriteAllText(ConfigPath, js.Serialize(c), new UTF8Encoding(false));
        _cache = c;
        _lastMtime = File.GetLastWriteTimeUtc(ConfigPath);
    }

    public static string BuildUrl(string baseUrl)
    {
        baseUrl = (baseUrl ?? "").Trim().TrimEnd('/');
        if (baseUrl.EndsWith("/chat/completions")) return baseUrl;
        if (baseUrl.EndsWith("/v1")) return baseUrl + "/chat/completions";
        return baseUrl + "/v1/chat/completions";
    }

    public static string MakeBody(AiConfig c, string question)
    {
        var js = new JavaScriptSerializer();
        var msgs = new object[]
        {
            new { role = "system", content = c.SystemPrompt ?? "" },
            new { role = "user", content = question }
        };
        var payload = new { model = c.Model, messages = msgs, temperature = 0.6 };
        return js.Serialize(payload);
    }

    public static string Call(string url, string key, string body, out string error)
    {
        error = null;
        try
        {
            using (var client = new HttpClient { Timeout = TimeSpan.FromMinutes(3) })
            {
                using (var req = new HttpRequestMessage(HttpMethod.Post, url))
                {
                    req.Content = new StringContent(body, Encoding.UTF8, "application/json");
                    req.Headers.TryAddWithoutValidation("Authorization", "Bearer " + key);
                    var resp = client.SendAsync(req).GetAwaiter().GetResult();
                    string text = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();
                    if (!resp.IsSuccessStatusCode)
                        return "[HTTP " + (int)resp.StatusCode + "] " + Trunc(text, 300);
                    var js = new JavaScriptSerializer { MaxJsonLength = 1 << 28 };
                    var obj = js.Deserialize<Dictionary<string, object>>(text);
                    var choices = obj["choices"] as object[];
                    var msg = ((Dictionary<string, object>)choices[0])["message"] as Dictionary<string, object>;
                    return (msg["content"] ?? "").ToString().Trim();
                }
            }
        }
        catch (Exception ex)
        {
            error = ex.Message;
            return null;
        }
    }

    private static string Trunc(string s, int n)
    {
        if (string.IsNullOrEmpty(s)) return "";
        return s.Length <= n ? s : s.Substring(0, n) + "…";
    }

    public static void LogHistory(string q, string a)
    {
        try
        {
            File.AppendAllText(HistoryPath,
                "──── " + DateTime.Now.ToString("MM-dd HH:mm:ss") + " ────" + Environment.NewLine +
                "[问] " + q.Replace("\r", "").Replace("\n", "\n") + Environment.NewLine +
                "[答] " + a + Environment.NewLine, new UTF8Encoding(false));
        }
        catch { }
    }
}

// ─────────────────────────────── 快速添加短语窗口 ───────────────────────────────
    class QuickPhraseForm : Form
    {
        public string Word { get { return txtWord.Text.Trim(); } }
        public string Code { get { return txtCode.Text.Trim(); } }
        public string Weight { get { return "1"; } }

        private TextBox txtWord, txtCode;

        public QuickPhraseForm()
        {
            FormBorderStyle = FormBorderStyle.FixedToolWindow;
            StartPosition = FormStartPosition.CenterScreen;
            Text = "快速添加自定义短语";
            Size = new Size(360, 190);
            BackColor = Theme.Bg;
            MaximizeBox = false; MinimizeBox = false;
            ShowInTaskbar = false;
            TopMost = true;

            var lblStyle = new Action<Label>(l => { l.ForeColor = Theme.Text; l.AutoSize = true; l.Font = new Font("Microsoft YaHei UI", 9f); });

            var l1 = new Label { Text = "词条（如：北京邮电大学）", Location = new Point(16, 14) }; lblStyle(l1);
            txtWord = DarkBox(); txtWord.Location = new Point(16, 36); txtWord.Width = 310;

            var l2 = new Label { Text = "编码（如：bydd，输入该码出现词条）", Location = new Point(16, 68) }; lblStyle(l2);
            txtCode = DarkBox(); txtCode.Location = new Point(16, 90); txtCode.Width = 310;

            var ok = new Button { Text = "保存", DialogResult = DialogResult.OK, BackColor = Theme.Accent, ForeColor = Color.White, FlatStyle = FlatStyle.Flat, Size = new Size(100, 32), Location = new Point(120, 130) };
            ok.FlatAppearance.BorderSize = 0;
            var cancel = new Button { Text = "取消", DialogResult = DialogResult.Cancel, BackColor = Color.FromArgb(52, 52, 66), ForeColor = Theme.Text, FlatStyle = FlatStyle.Flat, Size = new Size(100, 32), Location = new Point(228, 130) };
            cancel.FlatAppearance.BorderSize = 0;

            AcceptButton = ok; CancelButton = cancel;
            Controls.AddRange(new Control[] { l1, txtWord, l2, txtCode, ok, cancel });
            ActiveControl = txtWord;
        }

        private TextBox DarkBox()
        {
            return new TextBox
            {
                BackColor = Color.FromArgb(44, 44, 56),
                ForeColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Microsoft YaHei UI", 10f)
            };
        }
    }
}
