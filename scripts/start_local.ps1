param(
    [string]$Python = "",
    [switch]$Check
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $project ".env"

# 默认指向项目自己的 .venv；旧默认值 %TEMP%\rag-pilot-test-venv 是历史测试环境路径，
# 直接运行会误导用户。
if (-not $Python) {
    $Python = Join-Path $project ".venv\Scripts\python.exe"
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python 环境不存在：$Python（请先运行 setup_windows.cmd 创建 .venv）"
}
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "缺少 $envFile，请先运行 setup_windows.cmd 生成配置。"
}

Get-Content -LiteralPath $envFile | Where-Object {
    $_ -match '^[A-Za-z_][A-Za-z0-9_]*='
} | ForEach-Object {
    $name, $value = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
}

$listenAddress = if ($env:RAG_HOST) { $env:RAG_HOST } else { "127.0.0.1" }
# 健康检查始终走回环，即使 RAG_HOST 是 0.0.0.0 也能命中。
$listenPort = if ($env:RAG_PORT) { $env:RAG_PORT } else { "8088" }
$probeBase = "http://127.0.0.1:$listenPort"

function Invoke-HealthCheck {
    param([string]$Executable)

    $stamp = [guid]::NewGuid().ToString("N")
    $outLog = Join-Path $env:TEMP "rag-check-$stamp.out.log"
    $errLog = Join-Path $env:TEMP "rag-check-$stamp.err.log"
    $proc = Start-Process -FilePath $Executable `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--host", $listenAddress, "--port", $listenPort `
        -WorkingDirectory $project -WindowStyle Hidden `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
    $ready = $false
    try {
        for ($i = 0; $i -lt 120; $i++) {
            Start-Sleep -Milliseconds 1000
            if ($proc.HasExited) { break }
            try {
                $health = Invoke-WebRequest -UseBasicParsing "$probeBase/api/health" -TimeoutSec 3
                $readyResp = Invoke-WebRequest -UseBasicParsing "$probeBase/api/ready" -TimeoutSec 3
                if ($health.StatusCode -eq 200 -and $readyResp.StatusCode -eq 200) {
                    $ready = $true
                    break
                }
            } catch { }
        }
    } finally {
        if (-not $proc.HasExited) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }
    if (-not $ready) {
        Write-Host "健康检查未通过：$probeBase/api/health 或 /api/ready 未在 120 秒内返回 200" -ForegroundColor Red
        Get-Content $outLog -Tail 40 -ErrorAction SilentlyContinue
        Get-Content $errLog -Tail 40 -ErrorAction SilentlyContinue
    }
    Remove-Item $outLog, $errLog -ErrorAction SilentlyContinue
    return $ready
}

Push-Location $project
try {
    if ($Check) {
        # 供 setup_windows.ps1 在进入前台交互式启动前做真实验收。
        if (Invoke-HealthCheck -Executable $Python) {
            Write-Host "健康检查通过：$probeBase/api/health 与 /api/ready 均返回 200"
            exit 0
        }
        exit 1
    }

    & $Python -m uvicorn app.main:app --host $listenAddress --port $listenPort
} finally {
    Pop-Location
}
