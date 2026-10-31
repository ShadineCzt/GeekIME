# build_tray.ps1 - compile GeekTray and restart tray + web console (ASCII only)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$exe  = Join-Path $root 'GeekTray.exe'
$src  = Join-Path $PSScriptRoot 'GeekTray.cs'

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

$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $exe; CurrentDirectory = $root }
Write-Output ('TRAY START rc={0}' -f $r.ReturnValue)

Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*app.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 800
$r2 = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = 'python -X utf8 app.py'; CurrentDirectory = $PSScriptRoot }
Write-Output ('WEB START rc={0}' -f $r2.ReturnValue)

$ok = $false
foreach ($i in 1..20) {
    $c = New-Object Net.Sockets.TcpClient
    try { $ok = $c.ConnectAsync('127.0.0.1', 8765).Wait(300) } catch {}
    $c.Close()
    if ($ok) { break }
    Start-Sleep -Milliseconds 500
}
Write-Output ('PORT8765={0}' -f $ok)
