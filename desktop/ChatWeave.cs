using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

static class Program {
    internal static string Root = AppDomain.CurrentDomain.BaseDirectory;
    internal static string Connect = "";
    internal static bool Background = false;
    internal static uint ActivateMessage;
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern uint RegisterWindowMessage(string name);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr hwnd,uint msg,IntPtr w,IntPtr l);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [STAThread] static void Main(string[] args) {
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        SetProcessDPIAware();
        try {
            if(args.Length==1 && args[0]=="--background")Background=true;
            else if(args.Length!=0) {
                if(args.Length!=2 || args[0]!="--connect") throw new Exception("无法识别启动参数。");
                Uri uri;
                if(!Uri.TryCreate(args[1],UriKind.Absolute,out uri) || uri.Scheme!="http" || uri.Host!="127.0.0.1" || uri.AbsolutePath!="/" || uri.Query!="" || uri.UserInfo!="" || uri.Fragment!="")
                    throw new Exception("审阅连接仅允许本机 http://127.0.0.1:端口。");
                Connect=uri.GetLeftPart(UriPartial.Authority);
            }
            string id;
            using(var sha=SHA256.Create())id=BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(Root.ToLowerInvariant()+Connect))).Replace("-","").Substring(0,24);
            ActivateMessage=RegisterWindowMessage("ChatWeave.Activate."+id);
            bool created;
            using(var mutex=new Mutex(true,"Local\\ChatWeave."+id,out created)) {
                if(!created){PostMessage(new IntPtr(0xffff),ActivateMessage,IntPtr.Zero,IntPtr.Zero);return;}
                Application.Run(new DesktopWindow());
            }
        } catch(Exception e) {MessageBox.Show("Tulpa 未能启动。\n\n"+e.Message+"\n\n请完整解压文件夹，再运行 Tulpa.exe。","Tulpa",MessageBoxButtons.OK,MessageBoxIcon.Information);}
    }
}

sealed class DesktopWindow:Form {
    readonly WebView2 view=new WebView2();
    readonly PictureBox logo=new PictureBox();
    readonly Label title=new Label(),hint=new Label();
    readonly System.Windows.Forms.Timer health=new System.Windows.Forms.Timer();
    readonly JavaScriptSerializer json=new JavaScriptSerializer();
    readonly NotifyIcon tray=new NotifyIcon();
    Process backend;
    Job owner;
    string service="",token="",ready="";
    bool closing=false,loaded=false,exitRequested=false,closePending=false,trayNotice=false,cleaned=false;
    public DesktopWindow() {
        Text="Tulpa";StartPosition=FormStartPosition.CenterScreen;
        using(var graphics=CreateGraphics()) {
            double scale=graphics.DpiX/96.0;var screen=Screen.FromControl(this).WorkingArea;
            ClientSize=new Size((int)Math.Min(1320*scale,screen.Width*.90),(int)Math.Min(860*scale,screen.Height*.88));
        }
        MinimumSize=new Size(900,650);
        BackColor=Color.FromArgb(250,249,245);Font=new Font("Segoe UI",10);
        Icon=Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        tray.Icon=Icon;tray.Text="Tulpa · 本机资料服务";
        var menu=new ContextMenuStrip();
        menu.Items.Add("打开 Tulpa",null,delegate{ShowWindow();});
        if(Program.Connect=="") {
            var startup=new ToolStripMenuItem("开机启动（当前文件夹）");startup.CheckOnClick=true;
            using(var key=Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run"))
                startup.Checked=key!=null && (string)key.GetValue("Tulpa","")==StartupCommand();
            startup.Click+=delegate {
                try {using(var key=Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run")) {
                    if(startup.Checked)key.SetValue("Tulpa",StartupCommand());
                    else if((string)key.GetValue("Tulpa","")==StartupCommand())key.DeleteValue("Tulpa",false);
                }}catch {startup.Checked=!startup.Checked;MessageBox.Show(this,"无法保存开机启动设置。","Tulpa");}
            };
            menu.Items.Add(startup);
        }
        menu.Items.Add("彻底退出",null,delegate{exitRequested=true;Close();});
        tray.ContextMenuStrip=menu;tray.DoubleClick+=delegate{ShowWindow();};tray.Visible=true;
        using(var stream=typeof(DesktopWindow).Assembly.GetManifestResourceStream("Tulpa.Logo.png"))
        using(var image=Image.FromStream(stream))logo.Image=new Bitmap(image);
        logo.Size=new Size(96,96);logo.SizeMode=PictureBoxSizeMode.Zoom;logo.BackColor=Color.Black;
        title.Text="Tulpa";title.Font=new Font("Segoe UI",30,FontStyle.Bold);title.ForeColor=Color.FromArgb(23,23,20);title.AutoSize=true;
        hint.Text="正在展开你的通信工作空间…";hint.AutoSize=true;hint.ForeColor=Color.FromArgb(115,114,108);
        Controls.Add(logo);Controls.Add(title);Controls.Add(hint);Resize+=delegate{CenterSplash();};CenterSplash();
        Shown+=async delegate{await Start();};FormClosing+=OnClosing;
        health.Interval=4000;health.Tick+=delegate {
            if(!closing&&loaded&&backend!=null&&backend.HasExited){health.Stop();ShowWindow();tray.Text="Tulpa · 本机服务已停止";MessageBox.Show(this,"本机服务已停止。已保存的记录保留，请退出并重新打开 Tulpa。","Tulpa",MessageBoxButtons.OK,MessageBoxIcon.Information);}
        };
    }
    void CenterSplash(){logo.Left=(ClientSize.Width-logo.Width)/2;logo.Top=ClientSize.Height/2-175;title.Left=(ClientSize.Width-title.Width)/2;title.Top=ClientSize.Height/2-55;hint.Left=(ClientSize.Width-hint.Width)/2;hint.Top=ClientSize.Height/2+15;}
    string StartupCommand(){return "\""+Application.ExecutablePath+"\" --background";}
    void ShowWindow(){Show();ShowInTaskbar=true;if(WindowState==FormWindowState.Minimized)WindowState=FormWindowState.Normal;Activate();}
    void HideToTray(){Hide();ShowInTaskbar=false;if(!trayNotice){trayNotice=true;tray.ShowBalloonTip(4000,"Tulpa 仍在运行","MCP 和实时读取继续运行。双击托盘图标打开，右键可彻底退出。",ToolTipIcon.Info);}}
    protected override void WndProc(ref Message m){if(m.Msg==Program.ActivateMessage)ShowWindow();base.WndProc(ref m);}
    object GetJson(string path){var req=(HttpWebRequest)WebRequest.Create(service+path);req.Proxy=null;req.Timeout=2000;using(var r=req.GetResponse())using(var sr=new StreamReader(r.GetResponseStream()))return json.DeserializeObject(sr.ReadToEnd());}
    async Task Start() {
        try {
            Directory.CreateDirectory(Path.Combine(Program.Root,".tmp"));
            Directory.CreateDirectory(Path.Combine(Program.Root,"data"));
            // Fail early on a read-only archive/Program Files location.
            string probe=Path.Combine(Program.Root,".tmp","write-probe");File.WriteAllText(probe,"");File.Delete(probe);
            if(Program.Connect!="")service=Program.Connect;
            else {
                hint.Text="正在启动…";CenterSplash();
                token=Guid.NewGuid().ToString("N")+Guid.NewGuid().ToString("N");
                ready=Path.Combine(Program.Root,".tmp","desktop-ready-"+Guid.NewGuid().ToString("N")+".json");
                var start=new ProcessStartInfo(Path.Combine(Program.Root,"runtime","python.exe"),"-X utf8 -B desktop/serve.py "+Path.GetFileName(ready));
                start.WorkingDirectory=Program.Root;start.UseShellExecute=false;start.CreateNoWindow=true;start.RedirectStandardOutput=true;start.RedirectStandardError=true;
                foreach(string key in new[]{"PYTHONHOME","PYTHONPATH","API_KEY","API_BASE","MODEL","REPLY_ONEBOT_URL","REPLY_ONEBOT_TOKEN","DSH_HOME","DEEPSEEK_HARNESS_RUNTIME_MODE"})start.EnvironmentVariables.Remove(key);
                start.EnvironmentVariables["CHATWEAVE_SESSION_TOKEN"]=token;
                start.EnvironmentVariables["PYTHONUTF8"]="1";
                start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"]="1";
                owner=new Job();backend=new Process();backend.StartInfo=start;
                backend.OutputDataReceived+=delegate{};
                backend.ErrorDataReceived+=delegate(object sender,DataReceivedEventArgs e){if(e.Data!=null)try {var log=Path.Combine(Program.Root,".tmp","desktop-service.log");if(!File.Exists(log)||new FileInfo(log).Length<1048576)File.AppendAllText(log,e.Data+Environment.NewLine,Encoding.UTF8);}catch{}};
                backend.Start();owner.Add(backend);backend.BeginOutputReadLine();backend.BeginErrorReadLine();
                for(int i=0;i<600&&!File.Exists(ready);i++){if(closing)return;if(backend.HasExited)throw new Exception("本机服务启动失败。诊断保存在 .tmp/desktop-service.log。");await Task.Delay(150);}
                if(!File.Exists(ready))throw new Exception("本机服务启动超时，请检查是否完整解压。");
                var data=json.Deserialize<System.Collections.Generic.Dictionary<string,object>>(File.ReadAllText(ready));service=(string)data["url"];
            }
            var status=await Task.Run(()=>GetJson("/api/desktop/status"));
            var statusData=(System.Collections.Generic.Dictionary<string,object>)status;
            if((string)statusData["app"]!="ChatWeave" && (string)statusData["app"]!="Tulpa")throw new Exception("目标服务不是兼容的 Tulpa 工作台。");
            hint.Text="正在载入界面…";CenterSplash();
            string browser=Path.Combine(Program.Root,"_internal","WebView2");
            if(!File.Exists(Path.Combine(browser,"msedgewebview2.exe")))throw new Exception("缺少随包提供的界面运行组件，请完整解压 ZIP。");
            var options=new CoreWebView2EnvironmentOptions();
#if QA
            // Test build only: exercise the actual WebView2 DOM/rendering with
            // Playwright. The distributed executable has no debugging endpoint.
            int qaPort;
            if(Int32.TryParse(Environment.GetEnvironmentVariable("CHATWEAVE_QA_PORT"),out qaPort)&&qaPort>=1024&&qaPort<=65535)
                options.AdditionalBrowserArguments="--remote-debugging-port="+qaPort;
#endif
            var env=await CoreWebView2Environment.CreateAsync(browser,Path.Combine(Program.Root,"data","desktop-webview"),options);
            if(closing)return;
            view.Dock=DockStyle.Fill;view.DefaultBackgroundColor=BackColor;Controls.Add(view);view.BringToFront();
            await view.EnsureCoreWebView2Async(env);
            view.CoreWebView2.Settings.AreDevToolsEnabled=false;
            view.CoreWebView2.Settings.AreBrowserAcceleratorKeysEnabled=false;
            view.CoreWebView2.Settings.IsStatusBarEnabled=false;
            view.CoreWebView2.Settings.AreDefaultContextMenusEnabled=false;
            view.CoreWebView2.Settings.IsPasswordAutosaveEnabled=false;
            view.CoreWebView2.Settings.IsGeneralAutofillEnabled=false;
            view.CoreWebView2.NavigationStarting+=(s,e)=>{
                Uri uri;if(!Uri.TryCreate(e.Uri,UriKind.Absolute,out uri)) {e.Cancel=true;return;}
                if(uri.GetLeftPart(UriPartial.Authority)!=service){e.Cancel=true;}
            };
            view.CoreWebView2.NewWindowRequested+=(s,e)=>{e.Handled=true;Uri uri;if(Uri.TryCreate(e.Uri,UriKind.Absolute,out uri)&&(uri.Scheme=="https"||uri.Scheme=="http"))Process.Start(new ProcessStartInfo(uri.AbsoluteUri){UseShellExecute=true});};
            view.CoreWebView2.PermissionRequested+=(s,e)=>{e.State=CoreWebView2PermissionState.Deny;};
#if QA
            view.CoreWebView2.WebMessageReceived+=(s,e)=>{if(e.Source.StartsWith(service+"/")){var command=e.TryGetWebMessageAsString();if(command=="chatweave-qa-close"){exitRequested=true;Close();}else if(command=="tulpa-qa-hide")Close();else if(command=="tulpa-qa-show")ShowWindow();}};
#endif
            view.CoreWebView2.Navigate(service+"/?desktop=1");
            loaded=true;health.Start();
            if(Program.Background)HideToTray();
        } catch(Exception e){if(!closing){MessageBox.Show(this,e.Message,"Tulpa 启动提示",MessageBoxButtons.OK,MessageBoxIcon.Information);closing=true;Close();}}
    }
    async void OnClosing(object sender,FormClosingEventArgs e) {
        if(closing){Cleanup();return;}
        if(e.CloseReason==CloseReason.WindowsShutDown||e.CloseReason==CloseReason.TaskManagerClosing){closing=true;Cleanup();return;}
        e.Cancel=true;
        if(closePending)return;closePending=true;
        if(loaded&&backend!=null&&!backend.HasExited){
            try {
                if(!exitRequested){
                    var prefs=(System.Collections.Generic.Dictionary<string,object>)await Task.Run(()=>GetJson("/api/desktop/preferences"));
                    if((bool)prefs["background"]){closePending=false;HideToTray();return;}
                }
                var status=(System.Collections.Generic.Dictionary<string,object>)await Task.Run(()=>GetJson("/api/desktop/status"));
                if((bool)status["busy"]&&MessageBox.Show(this,"还有任务正在执行。退出会停止当前任务，已保存的内容会保留。\n\n现在退出？","Tulpa",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes){closePending=false;exitRequested=false;return;}
            }catch{}
        }
        closing=true;health.Stop();Hide();
        if(backend!=null&&!backend.HasExited&&service!="") {
            await Task.Run(()=>{try {var req=(HttpWebRequest)WebRequest.Create(service+"/api/desktop/quit");req.Proxy=null;req.Method="POST";req.ContentLength=0;req.Timeout=2000;req.Headers["Authorization"]="Bearer "+token;using(var response=req.GetResponse()){}backend.WaitForExit(6000);}catch{}});
        }
        Cleanup();Close();
    }
    void Cleanup(){if(cleaned)return;cleaned=true;health.Stop();tray.Visible=false;tray.Dispose();view.Dispose();if(logo.Image!=null){logo.Image.Dispose();logo.Image=null;}if(owner!=null){owner.Dispose();owner=null;}if(backend!=null){backend.Dispose();backend=null;}if(ready!="")try{File.Delete(ready);}catch{}}
}

sealed class Job:IDisposable {
    IntPtr handle;
    [StructLayout(LayoutKind.Sequential)]struct Basic {public long UserTime,JobTime;public uint Flags;public UIntPtr Min,Max;public uint Active;public UIntPtr Affinity;public uint Priority,Scheduling;}
    [StructLayout(LayoutKind.Sequential)]struct IO {public ulong ReadOps,WriteOps,OtherOps,ReadBytes,WriteBytes,OtherBytes;}
    [StructLayout(LayoutKind.Sequential)]struct Extended {public Basic Basic;public IO IO;public UIntPtr ProcessMemory,JobMemory,PeakProcess,PeakJob;}
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern IntPtr CreateJobObject(IntPtr attr,string name);
    [DllImport("kernel32.dll",SetLastError=true)]static extern bool SetInformationJobObject(IntPtr job,int type,ref Extended info,uint length);
    [DllImport("kernel32.dll",SetLastError=true)]static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
    [DllImport("kernel32.dll")]static extern bool CloseHandle(IntPtr handle);
    public Job(){handle=CreateJobObject(IntPtr.Zero,null);var info=new Extended();info.Basic.Flags=0x2000;if(handle==IntPtr.Zero||!SetInformationJobObject(handle,9,ref info,(uint)Marshal.SizeOf(info)))throw new Exception("无法建立本机服务进程管理。");}
    public void Add(Process p){if(!AssignProcessToJobObject(handle,p.Handle)){try{p.Kill();}catch{}throw new Exception("无法托管本机服务进程。");}}
    public void Dispose(){if(handle!=IntPtr.Zero){CloseHandle(handle);handle=IntPtr.Zero;}}
}
