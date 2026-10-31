# Pure-ASCII redeploy tool for the rime workspace located in this script's folder.
# Usage: powershell -ExecutionPolicy Bypass -File do_build.ps1   (run with WeaselServer stopped)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public static class RimeBuild {
  const string DLL = @"F:\12.Rime\weasel-0.17.4\rime.dll";

  [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
  delegate void NotifyHandler(IntPtr ctx, UIntPtr sessionId, IntPtr type, IntPtr value);
  static NotifyHandler _handler;
  static bool _failed = false;

  [DllImport(DLL, CallingConvention = CallingConvention.Cdecl)]
  static extern void RimeSetNotificationHandler(NotifyHandler handler, IntPtr ctx);
  [DllImport(DLL, CallingConvention = CallingConvention.Cdecl)]
  static extern int RimeInitialize(IntPtr traits);
  [DllImport(DLL, CallingConvention = CallingConvention.Cdecl)]
  static extern void RimeFinalize();
  [DllImport(DLL, CallingConvention = CallingConvention.Cdecl)]
  static extern int RimeStartMaintenance(int fullCheck);
  [DllImport(DLL, CallingConvention = CallingConvention.Cdecl)]
  static extern int RimeIsMaintenancing();
  [DllImport(DLL, CallingConvention = CallingConvention.Cdecl)]
  static extern void RimeJoinMaintenanceThread();
  [DllImport(DLL, CallingConvention = CallingConvention.Cdecl)]
  static extern int RimeDeployConfigFile(string configName, string versionKey);

  static IntPtr Utf8(string s) {
    if (s == null) return IntPtr.Zero;
    byte[] b = Encoding.UTF8.GetBytes(s);
    IntPtr p = Marshal.AllocHGlobal(b.Length + 1);
    Marshal.Copy(b, 0, p, b.Length);
    Marshal.WriteByte(p, b.Length, 0);
    return p;
  }
  static string PtrToUtf8(IntPtr p) {
    int len = 0; while (Marshal.ReadByte(p, len) != 0) len++;
    byte[] b = new byte[len]; Marshal.Copy(p, b, 0, len);
    return Encoding.UTF8.GetString(b);
  }
  static void OnNotify(IntPtr ctx, UIntPtr sid, IntPtr type, IntPtr val) {
    string t = type != IntPtr.Zero ? PtrToUtf8(type) : "";
    string v = val != IntPtr.Zero ? PtrToUtf8(val) : "";
    Console.WriteLine("notify: " + t + " = " + v);
    if (t == "deploy" && v == "failure") _failed = true;
  }

  public static int Run(string shared, string user, string staging, string logDir, int timeoutSec) {
    // RimeTraits x64 (librime 1.13): data_size(0) shared(8) user(16) dist(24)
    // code(32) ver(40) app(48) modules(56) min_log_level(64) log_dir(72)
    // prebuilt(80) staging(88) => sizeof = 96
    IntPtr traits = Marshal.AllocHGlobal(96);
    for (int i = 0; i < 96; i++) Marshal.WriteByte(traits, i, 0);
    Marshal.WriteInt32(traits, 0, 96);
    IntPtr pShared = Utf8(shared);
    IntPtr pUser = Utf8(user);
    IntPtr pDist = Utf8("rime-weasel");
    IntPtr pCode = Utf8("weasel");
    IntPtr pVer = Utf8("0.17.4");
    IntPtr pApp = Utf8("rime.weasel");
    IntPtr pLog = Utf8(logDir);
    IntPtr pStaging = Utf8(staging);
    Marshal.WriteIntPtr(traits, 8, pShared);
    Marshal.WriteIntPtr(traits, 16, pUser);
    Marshal.WriteIntPtr(traits, 24, pDist);
    Marshal.WriteIntPtr(traits, 32, pCode);
    Marshal.WriteIntPtr(traits, 40, pVer);
    Marshal.WriteIntPtr(traits, 48, pApp);
    Marshal.WriteInt32(traits, 64, 0);
    Marshal.WriteIntPtr(traits, 72, pLog);
    Marshal.WriteIntPtr(traits, 80, pShared); // prebuilt = shared (as weasel does)
    Marshal.WriteIntPtr(traits, 88, pStaging);
    _handler = OnNotify;
    RimeSetNotificationHandler(_handler, IntPtr.Zero);
    Console.WriteLine("initializing rime...");
    if (RimeInitialize(traits) == 0) { Console.WriteLine("RimeInitialize failed"); return 3; }
    Console.WriteLine("start maintenance (full_check=true)...");
    if (RimeStartMaintenance(1) == 0) { Console.WriteLine("RimeStartMaintenance failed"); RimeFinalize(); return 4; }
    DateTime start = DateTime.Now;
    while (RimeIsMaintenancing() != 0) {
      Thread.Sleep(1000);
      if ((DateTime.Now - start).TotalSeconds > timeoutSec) {
        Console.WriteLine("TIMEOUT"); RimeFinalize(); return 2;
      }
    }
    RimeJoinMaintenanceThread();
    Console.WriteLine("deploy weasel.yaml...");
    RimeDeployConfigFile("weasel.yaml", "config_version");
    Console.WriteLine(_failed ? "RESULT: deploy failure" : "RESULT: deploy success");
    RimeFinalize();
    return _failed ? 1 : 0;
  }
}
'@
$root = $PSScriptRoot
$logDir = Join-Path $root '.buildlog'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$staging = Join-Path $root 'build'
$code = [RimeBuild]::Run('F:\12.Rime\weasel-0.17.4\data', $root, $staging, $logDir, 600)
Write-Output "exit: $code"
