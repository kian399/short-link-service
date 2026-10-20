#Requires -Version 5.1
<#
  short-link-service 一键启停
  用法:
    .\scripts\dev.ps1 start    启动（后台运行，0.0.0.0:8000，热重载）
    .\scripts\dev.ps1 stop     停止
    .\scripts\dev.ps1 status   查看状态
#>
[CmdletBinding()]
param(
    [ValidateSet('start','stop','status')]
    [string]$Action = 'start',
    [int]$Port = 8000,
    [string]$LogDir = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Uvicorn     = Join-Path $ProjectRoot '.venv\Scripts\uvicorn.exe'
if (-not $LogDir) { $LogDir = Join-Path $env:LOCALAPPDATA 'short-link-service' }
$LogOut      = Join-Path $LogDir 'uvicorn.out.log'
$LogErr      = Join-Path $LogDir 'uvicorn.err.log'
$PidFile     = Join-Path $LogDir 'uvicorn.pid'

function Get-LanIPv4 {
    [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
        Where-Object { $_.OperationalStatus -eq 'Up' -and $_.NetworkInterfaceType -ne 'Loopback' } |
        ForEach-Object { $_.GetIPProperties().UnicastAddresses } |
        Where-Object { $_.Address.AddressFamily -eq 'InterNetwork' -and $_.Address.ToString() -notlike '169.254.*' } |
        ForEach-Object { $_.Address.ToString() } |
        Sort-Object -Unique
}

function Get-PortPids([int]$p) {
    $pat = ':' + $p + '\s'
    netstat -ano | Where-Object { $_ -match $pat -and $_ -match 'LISTENING' } | ForEach-Object {
        if ($_ -match 'LISTENING\s+(\d+)\s*$') { [int]$Matches[1] }
    } | Sort-Object -Unique
}

function Test-Health([int]$p) {
    try {
        $r = Invoke-WebRequest -Uri ('http://127.0.0.1:' + $p + '/healthz') -TimeoutSec 3 -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Show-Urls([int]$p) {
    Write-Host ('  本机   : http://127.0.0.1:' + $p + '/healthz')
    foreach ($ip in Get-LanIPv4) { Write-Host ('  局域网 : http://' + $ip + ':' + $p + '/healthz') }
}

switch ($Action) {
    'start' {
        if (-not (Test-Path $Uvicorn)) { throw ('找不到 ' + $Uvicorn + '，请先在项目根目录运行: uv sync') }
        $busy = @(Get-PortPids $Port)
        if ($busy.Count -gt 0) {
            Write-Host ('端口 ' + $Port + ' 已在监听 (PID ' + ($busy -join ',') + ')，如需重启用 stop 再 start') -ForegroundColor Yellow
            Show-Urls $Port
            break
        }
        New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
        $uvArgs = @('app.main:app','--host','0.0.0.0','--port',[string]$Port,'--reload')
        $proc = Start-Process -FilePath $Uvicorn -ArgumentList $uvArgs -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $LogOut -RedirectStandardError $LogErr -PassThru
        Set-Content -LiteralPath $PidFile -Value $proc.Id -Encoding ASCII
        $ok = $false
        for ($i = 0; $i -lt 30; $i++) { Start-Sleep -Milliseconds 500; if (Test-Health $Port) { $ok = $true; break } }
        if (-not $ok) { Write-Host ('启动超时，请看日志: ' + $LogErr) -ForegroundColor Red; break }
        Write-Host '已启动' -ForegroundColor Green
        Show-Urls $Port
        Write-Host ('  日志   : ' + $LogOut)
    }
    'stop' {
        $targets = @()
        if (Test-Path $PidFile) {
            $rootPid = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($rootPid) { $targets += [int]$rootPid }
            Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        }
        $targets += @(Get-PortPids $Port)
        $targets = @($targets | Sort-Object -Unique)
        if ($targets.Count -eq 0) { Write-Host '没有正在运行的服务' ; break }
        foreach ($procId in $targets) {
            try { & taskkill /PID $procId /T /F 2>&1 | Out-Null } catch { }
            try { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue } catch { }
        }
        Start-Sleep -Milliseconds 700
        if (@(Get-PortPids $Port).Count -gt 0) { Write-Host ('仍有进程占用端口 ' + $Port + '，请手动结束') -ForegroundColor Red }
        else { Write-Host '已停止' -ForegroundColor Green }
    }
    'status' {
        $busy = @(Get-PortPids $Port)
        if ($busy.Count -gt 0) { Write-Host ('运行中  PID: ' + ($busy -join ',')) -ForegroundColor Green; Show-Urls $Port }
        else { Write-Host ('未运行（端口 ' + $Port + ' 空闲）') }
        if (Test-Path $LogErr) { Write-Host ('  最近错误日志: ' + $LogErr); Get-Content -LiteralPath $LogErr -Tail 5 -ErrorAction SilentlyContinue }
    }
}