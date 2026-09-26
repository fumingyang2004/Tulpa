using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// Optional troubleshooting helper; not started by Tulpa. Never installs a
// service/firewall rule. Its child serves one redacted report for ten minutes.
class SupportWindow : Form {
    sealed class AddressOption {
        public string Address,Label;
        public override string ToString(){return Label;}
    }
    readonly string root=AppDomain.CurrentDomain.BaseDirectory;
    readonly ComboBox addresses=new ComboBox { DropDownStyle=ComboBoxStyle.DropDownList,Width=230,DropDownWidth=420 };
    readonly TextBox info=new TextBox { Multiline=true,ReadOnly=true,ScrollBars=ScrollBars.Vertical,Dock=DockStyle.Fill };
    readonly Button connect=new Button { Text="开启临时只读连接",AutoSize=true };
    Process server;
    string ready;
    bool closing;
    readonly System.Windows.Forms.Timer timer=new System.Windows.Forms.Timer { Interval=1000 };
    DateTime expires;

    public SupportWindow() {
        Text="Tulpa · 诊断助手";Size=new Size(700,410);MinimumSize=new Size(640,360);
        StartPosition=FormStartPosition.CenterScreen;Font=new Font("Microsoft YaHei UI",10);
        BackColor=Color.FromArgb(250,249,245);
        Icon=Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        var layout=new TableLayoutPanel { Dock=DockStyle.Fill,Padding=new Padding(20),ColumnCount=1,RowCount=4 };
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute,85));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,50));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent,100));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,48));
        layout.Controls.Add(new Label { Dock=DockStyle.Fill,Text="先导出诊断，或开启临时连接供开发排查。\n只检查版本、运行权限和错误类别；不包含聊天、账号、密钥、原始日志。\n临时连接十分钟后结束，关闭本窗口也会立即结束。" });
        var actions=new FlowLayoutPanel { Dock=DockStyle.Fill };
        var export=new Button { Text="导出诊断文件",AutoSize=true };actions.Controls.Add(export);actions.Controls.Add(addresses);actions.Controls.Add(connect);layout.Controls.Add(actions);
        layout.Controls.Add(info);
        var bottom=new FlowLayoutPanel { Dock=DockStyle.Fill,FlowDirection=FlowDirection.RightToLeft };
        var stop=new Button { Text="关闭并断开",AutoSize=true };var copy=new Button { Text="复制连接信息",AutoSize=true };
        bottom.Controls.Add(stop);bottom.Controls.Add(copy);layout.Controls.Add(bottom);Controls.Add(layout);
        foreach(var nic in NetworkInterface.GetAllNetworkInterfaces().Where(n=>n.OperationalStatus==OperationalStatus.Up && n.NetworkInterfaceType!=NetworkInterfaceType.Loopback)
            .OrderBy(n=>n.NetworkInterfaceType==NetworkInterfaceType.Wireless80211?0:n.GetIPProperties().GatewayAddresses.Any(g=>g.Address.AddressFamily==AddressFamily.InterNetwork && !g.Address.Equals(IPAddress.Any))?1:2))
            foreach(var ip in nic.GetIPProperties().UnicastAddresses.Select(a=>a.Address).Where(a=>a.AddressFamily==AddressFamily.InterNetwork && Private(a)))
                if(!addresses.Items.Cast<AddressOption>().Any(a=>a.Address==ip.ToString()))addresses.Items.Add(new AddressOption {Address=ip.ToString(),Label=nic.Name+" · "+ip});
        if(addresses.Items.Count>0)addresses.SelectedIndex=0;else connect.Enabled=false;
        info.Text="本工具需放在 Tulpa.exe 旁运行。\r\n若 Windows 提示防火墙访问，请只允许你的私人网络；无需管理员权限安装任何服务。\r\n连接不通时直接导出诊断即可。";
        export.Click+=async delegate { export.Enabled=false;try { await Export(); }catch(Exception e){info.Text=e.Message;}finally{if(!closing)export.Enabled=true;} };
        connect.Click+=async delegate { connect.Enabled=false;try{await Start();}catch(Exception e){Stop();info.Text=e.Message;connect.Enabled=true;} };
        stop.Click+=delegate { Close(); };copy.Click+=delegate { if(server!=null && !server.HasExited)Clipboard.SetText(info.Text); };
        timer.Tick+=delegate { if(server!=null && (server.HasExited || DateTime.UtcNow>=expires)){Stop();info.Text="临时连接已结束。可再次开启。";connect.Enabled=true;} };
        FormClosing+=delegate { closing=true;Stop(); };
    }
    static bool Private(IPAddress ip) { var b=ip.GetAddressBytes();return b[0]==10 || (b[0]==172 && b[1]>=16 && b[1]<=31) || (b[0]==192 && b[1]==168) || (b[0]==100 && b[1]>=64 && b[1]<=127); }
    Process Launch(string args) {
        string python=Path.Combine(root,"runtime","python.exe"),script=Path.Combine(root,"desktop","diagnose.py");
        if(!File.Exists(python)||!File.Exists(script))throw new Exception("请把诊断助手放在 Tulpa.exe 所在文件夹，完整覆盖修复包后再运行。");
        Directory.CreateDirectory(Path.Combine(root,".tmp"));
        return Process.Start(new ProcessStartInfo(python,"\""+script+"\" "+args) { WorkingDirectory=root,UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden });
    }
    async Task Export() {
        using(var dialog=new SaveFileDialog { Filter="诊断 JSON|*.json",FileName="Tulpa-diagnostics.json" }) {
            if(dialog.ShowDialog(this)!=DialogResult.OK)return;
            string temp=Path.Combine(root,".tmp","support-export-"+Guid.NewGuid().ToString("N")+".json");
            try {
                using(var p=Launch("--output \""+temp+"\"")) {
                    bool done=await Task.Run(()=>p.WaitForExit(40000));
                    if(!done){p.Kill();throw new Exception("诊断超时，请重新尝试。");}
                    if(p.ExitCode!=0||!File.Exists(temp))throw new Exception("诊断未完成，请保留原有 data-qq-error.log / data-wechat-error.log。");
                }
                if(closing)return;
                File.Copy(temp,dialog.FileName,true);info.Text="诊断已保存，可将这个 JSON 文件交给开发者：\r\n"+dialog.FileName;
            } finally { if(File.Exists(temp))File.Delete(temp); }
        }
    }
    async Task Start() {
        Stop();ready=Path.Combine(root,".tmp","support-"+Guid.NewGuid().ToString("N")+".json");
        server=Launch("--serve "+((AddressOption)addresses.SelectedItem).Address+" --ready \""+ready+"\"");
        info.Text="正在检查版本与运行时…";
        for(int i=0;i<400&&!File.Exists(ready);i++){if(closing)return;if(server.HasExited)throw new Exception("无法开启临时连接，请改用导出诊断文件。");await Task.Delay(100);}
        if(closing)return;
        if(!File.Exists(ready))throw new Exception("诊断启动超时，请改用导出诊断。");
        var data=new JavaScriptSerializer().Deserialize<System.Collections.Generic.Dictionary<string,string>>(File.ReadAllText(ready));
        expires=DateTime.UtcNow.AddMinutes(10);timer.Start();
        info.Text="临时只读诊断（十分钟有效）\r\n地址："+data["url"]+"\r\n配对码："+data["token"]+"\r\n\r\n将以上信息发给当前协助你的开发者即可。关闭本窗口就会断开。\r\n只提供诊断摘要，无法远程执行命令、浏览文件或读取聊天。";
    }
    void Stop() {
        timer.Stop();if(server!=null){try{if(!server.HasExited){server.Kill();server.WaitForExit(1500);}}catch(InvalidOperationException){}finally{server.Dispose();server=null;}}
        if(!String.IsNullOrEmpty(ready))try{File.Delete(ready);}catch(IOException){}
    }
    [STAThread] static void Main() { Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);Application.Run(new SupportWindow()); }
}
