param(
    [string]$Python = "$env:TEMP\rag-pilot-test-venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $project ".env"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python 环境不存在：$Python"
}
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "缺少 $envFile，请先复制并填写 .env.example。"
}

Get-Content -LiteralPath $envFile | Where-Object {
    $_ -match '^[A-Za-z_][A-Za-z0-9_]*='
} | ForEach-Object {
    $name, $value = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
}

$listenAddress = if ($env:RAG_HOST) { $env:RAG_HOST } else { "0.0.0.0" }
$listenPort = if ($env:RAG_PORT) { $env:RAG_PORT } else { "8088" }

Push-Location $project
try {
    & $Python -m uvicorn app.main:app --host $listenAddress --port $listenPort
} finally {
    Pop-Location
}
