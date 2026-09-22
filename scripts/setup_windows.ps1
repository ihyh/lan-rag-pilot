param(
    [string]$Python = "",
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $project ".venv\Scripts\python.exe"
$envFile = Join-Path $project ".env"

function Get-PythonVersion {
    param([string]$Executable)

    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        $version = & $Executable -c "import sys; print('.'.join(map(str, sys.version_info[:2])))" 2>$null
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($exitCode -ne 0 -or -not $version) {
        return ""
    }
    return @($version)[-1].Trim()
}

function Find-Python312 {
    if ($Python) {
        if (-not (Test-Path -LiteralPath $Python)) {
            throw "Python was not found: $Python"
        }
        return (Resolve-Path -LiteralPath $Python).Path
    }

    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        $previousErrorAction = $ErrorActionPreference
        try {
            $ErrorActionPreference = "SilentlyContinue"
            $fromLauncher = & py.exe -3.12 -c "import sys; print(sys.executable)" 2>$null
            $launcherExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorAction
        }
        if ($launcherExitCode -eq 0 -and $fromLauncher) {
            return @($fromLauncher)[-1].Trim()
        }
    }

    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand -and (Get-PythonVersion $pythonCommand.Source) -eq "3.12") {
        return $pythonCommand.Source
    }

    $uvRoot = Join-Path $env:APPDATA "uv\python"
    $uvPython = Get-ChildItem -Path (Join-Path $uvRoot "cpython-3.12.*-windows-*\python.exe") -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($uvPython) {
        return $uvPython.FullName
    }

    $standardPaths = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python312\python.exe")
    )
    foreach ($standardPath in $standardPaths) {
        if (Test-Path -LiteralPath $standardPath) {
            return $standardPath
        }
    }

    return ""
}

function Invoke-Checked {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [string]$ErrorMessage
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw $ErrorMessage
    }
}

function Test-PortAvailable {
    param([int]$Port)

    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        $listener.Stop()
    }
}

function Install-Python312 {
    $pythonManager = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pythonManager) {
        $previousErrorAction = $ErrorActionPreference
        try {
            $ErrorActionPreference = "SilentlyContinue"
            $managerHelp = & $pythonManager.Source help install 2>$null
            $managerExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorAction
        }
        if ($managerExitCode -eq 0 -and ($managerHelp -match "Python installation manager")) {
            Write-Host "Python 3.12 was not found. Installing it with Python Installation Manager..."
            Invoke-Checked $pythonManager.Source @("install", "-y", "3.12") "Python Installation Manager could not install Python 3.12."
            return
        }
    }

    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python 3.12 was not found and winget is unavailable. Install Python 3.12 from https://www.python.org/downloads/ and run setup_windows.cmd again."
    }

    Write-Host "Python 3.12 was not found. Installing it with winget..."
    Invoke-Checked $winget.Source @(
        "install",
        "--id", "Python.Python.3.12",
        "--exact",
        "--source", "winget",
        "--scope", "user",
        "--silent",
        "--accept-package-agreements",
        "--accept-source-agreements"
    ) "winget could not install Python 3.12. Install it from https://www.python.org/downloads/ and run setup_windows.cmd again."
}

Write-Host "[1/6] Checking prerequisites..."
$basePython = Find-Python312
if (-not $basePython) {
    Install-Python312
    $basePython = Find-Python312
}
if (-not $basePython) {
    throw "Python 3.12 installation completed but Python could not be located. Close this window and run setup_windows.cmd again."
}
if ((Get-PythonVersion $basePython) -ne "3.12") {
    throw "Python 3.12 is required. Selected interpreter: $basePython"
}
$ollama = Get-Command ollama.exe -ErrorAction SilentlyContinue
if (-not $ollama) {
    throw "Ollama was not found. Install it from https://ollama.com/download/windows, reopen this window, and run setup_windows.cmd again."
}

Write-Host "[2/6] Preparing Python environment..."
if (-not (Test-Path -LiteralPath $venvPython)) {
    Invoke-Checked $basePython @("-m", "venv", (Join-Path $project ".venv")) "Could not create the Python environment."
}
if ((Get-PythonVersion $venvPython) -ne "3.12") {
    throw "The existing .venv does not use Python 3.12. Remove the .venv folder and run setup_windows.cmd again."
}
Invoke-Checked $venvPython @("-m", "pip", "install", "--timeout", "120", "--retries", "10", "torch", "--index-url", "https://download.pytorch.org/whl/cpu") "CPU 版 PyTorch 安装失败。检查网络后重新运行 setup_windows.cmd。"
Invoke-Checked $venvPython @("-m", "pip", "install", "--timeout", "120", "--retries", "10", "-r", (Join-Path $project "requirements.txt")) "Python dependency installation failed. Run setup_windows.cmd again; pip will reuse downloaded files."
Invoke-Checked $venvPython @("-m", "pip", "check") "Python dependency validation failed."

Write-Host "[3/6] Preparing Ollama model..."
$env:NO_PROXY = "127.0.0.1,localhost"
Invoke-Checked $ollama.Source @("pull", "qwen3:1.7b") "Ollama could not download qwen3:1.7b. Start Ollama, check its network access, and run setup_windows.cmd again."

Write-Host "[4/6] Preparing BGE model..."
$installBge = Join-Path $PSScriptRoot "install_bge.py"
Invoke-Checked $venvPython @($installBge, "--project", $project) "BGE model installation failed. Check GitHub access and run setup_windows.cmd again."

Write-Host "[5/6] Preparing local configuration..."
$createdPassword = ""
if (-not (Test-Path -LiteralPath $envFile)) {
    $port = @(8088, 18088, 18089, 18090) | Where-Object { Test-PortAvailable $_ } | Select-Object -First 1
    if (-not $port) {
        throw "Ports 8088 and 18088-18090 are in use. Free one of them and run setup_windows.cmd again."
    }

    $secret = & $venvPython -c "import secrets; print(secrets.token_urlsafe(48))"
    $createdPassword = & $venvPython -c "import secrets; print(secrets.token_urlsafe(24))"
    $lines = @(
        "DEEPSEEK_API_KEY=ollama",
        "DEEPSEEK_BASE_URL=http://127.0.0.1:11434/v1",
        "DEEPSEEK_MODEL=qwen3:1.7b",
        "RAG_EMBED_MODEL=models/bge-small-zh-v1.5",
        "RAG_SECRET_KEY=$secret",
        "RAG_ROOT_PASSWORD=$createdPassword",
        "RAG_COOKIE_SECURE=false",
        "RAG_HOST=127.0.0.1",
        "RAG_PORT=$port",
        "RAG_PUBLIC_ORIGIN=http://127.0.0.1:$port",
        "NO_PROXY=127.0.0.1,localhost",
        "HF_HUB_OFFLINE=1",
        "TRANSFORMERS_OFFLINE=1"
    )
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($envFile, ([string]::Join([Environment]::NewLine, $lines) + [Environment]::NewLine), $utf8NoBom)
    Write-Host "Created .env. Initial account: root"
    Write-Host "Initial password: $createdPassword"
} else {
    Write-Host "Existing .env was preserved."
}

# ---------- 真实校验：不再只打印一句 "Setup validation completed." ----------
function Get-EnvFileValue {
    param([string]$Key, [string]$Default = "")

    $line = Get-Content -LiteralPath $envFile -ErrorAction SilentlyContinue |
        Where-Object { $_ -match "^$Key=" } | Select-Object -First 1
    if (-not $line) { return $Default }
    return ($line -split '=', 2)[1]
}

function Test-Setup {
    $failed = $false

    Push-Location $project
    try {
        & $venvPython -c "import app.main" *> $null
        $importExit = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($importExit -eq 0) {
        Write-Host "  [OK]   Python 依赖与应用模块可导入"
    } else {
        Write-Host "  [FAIL] 无法导入 app.main，依赖未装全或 .venv 损坏" -ForegroundColor Red
        $failed = $true
    }

    $embedModel = Get-EnvFileValue "RAG_EMBED_MODEL" "models/bge-small-zh-v1.5"
    if (Test-Path -LiteralPath (Join-Path $project $embedModel)) {
        Write-Host "  [OK]   嵌入模型目录存在：$embedModel"
    } else {
        Write-Host "  [FAIL] 嵌入模型目录不存在：$(Join-Path $project $embedModel)" -ForegroundColor Red
        $failed = $true
    }

    $ollamaModel = Get-EnvFileValue "DEEPSEEK_MODEL" "qwen3:1.7b"
    $ollamaCmd = Get-Command ollama.exe -ErrorAction SilentlyContinue
    $listed = if ($ollamaCmd) { & $ollamaCmd.Source list 2>$null | Out-String } else { "" }
    if ($listed -match [regex]::Escape($ollamaModel)) {
        Write-Host "  [OK]   Ollama 已存在模型：$ollamaModel"
    } else {
        Write-Host "  [FAIL] Ollama 中没有模型 $ollamaModel（先启动 Ollama 并完成 pull）" -ForegroundColor Red
        $failed = $true
    }

    return (-not $failed)
}

if ($NoStart) {
    Write-Host "[6/6] Verifying setup (not starting the service)..."
    if (Test-Setup) {
        Write-Host "配置校验通过。未启动服务；运行 setup_windows.cmd（不带 -NoStart）即可启动。"
        exit 0
    }
    throw "配置校验未通过，请按上面的 [FAIL] 处理后重试。"
}

$configuredPort = Get-EnvFileValue "RAG_PORT" "8088"
Write-Host "[6/6] Starting LAN RAG Pilot..."
# 先用 -Check 做一次真实健康校验（后台启动 → 轮询 /api/health 与 /api/ready → 停止），
# 确认配置真的可用后再进入前台交互式启动。
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "start_local.ps1") -Python $venvPython -Check
if ($LASTEXITCODE -ne 0) {
    throw "启动前健康检查未通过，未进入前台启动。请按上面的日志处理后重试。"
}
Write-Host "Open http://127.0.0.1:$configuredPort after startup. Press Ctrl+C here to stop."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "start_local.ps1") -Python $venvPython
if ($LASTEXITCODE -ne 0) {
    throw "The application stopped with an error. Read the message above and run setup_windows.cmd again."
}
