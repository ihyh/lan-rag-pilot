# 本文件必须保存为「UTF-8 with BOM」。Windows PowerShell 5.1 对无 BOM 的文件按
# ANSI 解码，中文会变成乱码并直接导致语法错误。改动后请确认 BOM 仍在；
# tests/win_script_encoding_check.py 会在 CI 上守住这一点。
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

function Invoke-ProbeRequest {
    # 刻意不用 Invoke-WebRequest，主要原因是它对非 2xx 抛异常，
    # 而 /api/ready 的 503 响应体里正是失败原因（reason 与 checks.llm.message）。
    # 用 Invoke-WebRequest 只能拿到异常对象，最有用的那句诊断就丢了，
    # 失败时只剩“未返回 200”，等于让用户自己去翻日志。
    # 其次，这里显式设 Proxy = $null 让探针直连：.NET 的默认代理会遵守
    # ProxyOverride 绕过列表（回环通常已在其中），所以这更多是去掉对“目标机器
    # 代理配置恰好正确”的依赖，而不是修一个已发生的故障。模型服务那边的代理
    # 接管问题见 docs/IT_handover.md 的“代理接管”一节。
    param([string]$Url, [int]$TimeoutSec = 3)

    $req = [System.Net.WebRequest]::Create($Url)
    $req.Proxy = $null
    $req.Method = "GET"
    $req.Timeout = $TimeoutSec * 1000
    try {
        $resp = $req.GetResponse()
    } catch [System.Net.WebException] {
        # 4xx/5xx 走这里，但 Response 仍带着响应体。
        $resp = $_.Exception.Response
        if ($null -eq $resp) { throw }
    }
    try {
        $stream = $resp.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream)
        $body = $reader.ReadToEnd()
    } finally {
        if ($reader) { $reader.Close() }
    }
    return [pscustomobject]@{ StatusCode = [int]$resp.StatusCode; Body = $body }
}

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
    $lastReason = ""
    try {
        for ($i = 0; $i -lt 120; $i++) {
            Start-Sleep -Milliseconds 1000
            if ($proc.HasExited) { break }
            try {
                $health = Invoke-ProbeRequest "$probeBase/api/health"
                $readyResp = Invoke-ProbeRequest "$probeBase/api/ready"
                if ($health.StatusCode -eq 200 -and $readyResp.StatusCode -eq 200) {
                    $ready = $true
                    break
                }
                # 记住最近一次的就绪失败原因，最后一起打印。
                $lastReason = "HTTP $($readyResp.StatusCode) $($readyResp.Body)"
            } catch { }
        }
    } finally {
        if (-not $proc.HasExited) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }
    if (-not $ready) {
        Write-Host "健康检查未通过：$probeBase/api/health 或 /api/ready 未在 120 秒内返回 200" -ForegroundColor Red
        if ($lastReason) {
            # /api/ready 的 503 会说明断在哪条链路、该怎么修，直接给出比让用户翻日志快。
            Write-Host "最近一次就绪响应：$lastReason" -ForegroundColor Red
        }
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
