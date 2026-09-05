param(
    [string]$Python = "$env:TEMP\rag-pilot-test-venv\Scripts\python.exe",
    [int]$AppPort = 8090,
    [int]$MockPort = 8099,
    [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$project = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $env:TEMP ("rag-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $runDir | Out-Null

$env:RAG_EMBED_BACKEND = "mock"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:RAG_DATA_DIR = $runDir
$env:RAG_DB_PATH = Join-Path $runDir "rag.db"
$env:RAG_UPLOAD_DIR = Join-Path $runDir "uploads"
$env:RAG_MODELS_DIR = Join-Path $runDir "models"
$env:RAG_ROOT_PASSWORD = "fcd123"
$env:RAG_SECRET_KEY = "smoke-secret-key-0123456789abcdef"
$env:RAG_QUERIES_PER_MINUTE = "10"
$env:RAG_MAX_CONCURRENT_LLM = "3"
$env:RAG_TOP_K = "5"
$env:DEEPSEEK_API_KEY = "mock-key"
$env:DEEPSEEK_BASE_URL = "http://127.0.0.1:$MockPort"
$env:DEEPSEEK_MODEL = "mock-model"
$env:DEEPSEEK_TIMEOUT_S = "1"
$env:RAG_SMOKE_URL = "http://127.0.0.1:$AppPort"

$mock = Start-Process -FilePath $Python -ArgumentList "-m", "uvicorn", "tests.mock_deepseek:app", "--host", "127.0.0.1", "--port", "$MockPort" -WorkingDirectory $project -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runDir "mock.out.log") -RedirectStandardError (Join-Path $runDir "mock.err.log") -PassThru
$app = Start-Process -FilePath $Python -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$AppPort" -WorkingDirectory $project -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runDir "app.out.log") -RedirectStandardError (Join-Path $runDir "app.err.log") -PassThru
$code = 0

try {
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $appHealth = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$AppPort/api/health" -TimeoutSec 2
            $mockHealth = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$MockPort/healthz" -TimeoutSec 2
            if ($appHealth.StatusCode -eq 200 -and $mockHealth.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch { }
    }
    if (-not $ready) {
        Write-Host "应用未就绪。错误日志："
        Get-Content (Join-Path $runDir "app.err.log") -ErrorAction SilentlyContinue
        $code = 2
    } else {
        $smokeLog = Join-Path $runDir "smoke.log"
        & $Python (Join-Path $project "tests\api_smoke.py") *> $smokeLog
        $code = $LASTEXITCODE
        Get-Content $smokeLog -Encoding utf8
        if ($code -eq 0) {
            Stop-Process -Id $app.Id -ErrorAction SilentlyContinue
            $app.WaitForExit()
            $app = Start-Process -FilePath $Python -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$AppPort" -WorkingDirectory $project -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runDir "app-restart.out.log") -RedirectStandardError (Join-Path $runDir "app-restart.err.log") -PassThru
            $restartReady = $false
            for ($i = 0; $i -lt 30; $i++) {
                Start-Sleep -Milliseconds 500
                try {
                    $health = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$AppPort/api/health" -TimeoutSec 2
                    if ($health.StatusCode -eq 200) { $restartReady = $true; break }
                } catch { }
            }
            if ($restartReady) {
                $persistenceLog = Join-Path $runDir "persistence.log"
                & $Python (Join-Path $project "tests\persistence_check.py") *> $persistenceLog
                $code = $LASTEXITCODE
                Get-Content $persistenceLog -Encoding utf8
            } else {
                Write-Host "[FAIL] 重启后应用未就绪"
                Get-Content (Join-Path $runDir "app-restart.err.log") -ErrorAction SilentlyContinue
                $code = 2
            }
        }
        if ($code -ne 0) {
            Get-Content (Join-Path $runDir "app.err.log") -Tail 80 -ErrorAction SilentlyContinue
        } elseif ($KeepRunning) {
            Write-Host "UI_READY=http://127.0.0.1:$AppPort"
            Write-Host "Press Ctrl+C to stop the test servers."
            while ($true) { Start-Sleep -Seconds 5 }
        }
    }
} finally {
    Stop-Process -Id $mock.Id, $app.Id -ErrorAction SilentlyContinue
}
Write-Host "SMOKE_EXIT=$code"
Write-Host "RUN_DIR=$runDir"
exit $code
