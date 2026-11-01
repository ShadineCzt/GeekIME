# build_tray.ps1 - compile GeekTray and restart tray + web console (ASCII only)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$exe  = Join-Path $root 'GeekTray.exe'
$src  = Join-Path $PSScriptRoot 'GeekTray.cs'
$pf   = Join-Path $PSScriptRoot 'web_port.txt'   # app.py 启动后写入实际端口

Get-Process GeekTray -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 800

$cscArgs = @(
    '/nologo', '/target:winexe', '/codepage:65001',
    '/r:System.Web.Extensions.dll', '/r:System.Net.Http.dll',
    ('/out:{0}' -f $exe), $src
)
$p = Start-Process -FilePath 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe' -ArgumentList $cscArgs -Wait -PassThru -WindowStyle Hidden
if ($p.ExitCode -ne 0) { Write-Output ('COMPILE FAIL ({0})' -f $p.ExitCode); exit 1 }
Write-Output 'COMPILE OK'

Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*app.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 800
Remove-Item $pf -ErrorAction SilentlyContinue
$r2 = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = 'pythonw.exe -X utf8 app.py'; CurrentDirectory = $PSScriptRoot }
Write-Output ('WEB START rc={0}' -f $r2.ReturnValue)

$ok = $false
$port = 8765
foreach ($i in 1..20) {
    if (Test-Path $pf) {
        $t = (Get-Content $pf -Raw).Trim()
        if ($t -match '^\d+$') { $port = [int]$t }
    }
    $c = New-Object Net.Sockets.TcpClient
    try { $ok = $c.ConnectAsync('127.0.0.1', $port).Wait(300) } catch {}
    $c.Close()
    if ($ok) { break }
    Start-Sleep -Milliseconds 500
}
Write-Output ('WEB PORT={0} REACHABLE={1}' -f $port, $ok)

# Web 服务就绪后再拉起托盘，避免托盘启动时重复拉起第二个 Web 进程
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $exe; CurrentDirectory = $root }
Write-Output ('TRAY START rc={0}' -f $r.ReturnValue)
