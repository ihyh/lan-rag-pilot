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
Invoke-Checked $venvPython @("-m", "pip", "install", "--timeout", "120", "--retries", "10", "-r", (Join-Path $project "requirements.txt")) "Python dependency installation failed. Run setup_windows.cmd again; pip will reuse downloaded files."
Invoke-Checked $venvPython @("-m", "pip", "check") "Python dependency validation failed."

Write-Host "[3/6] Preparing Ollama model..."
$env:NO_PROXY = "127.0.0.1,localhost"
Invoke-Checked $ollama.Source @("pull", "qwen3:1.7b") "Ollama could not download qwen3:1.7b. Start Ollama, check its network access, and run setup_windows.cmd again."

Write-Host "[4/6] Preparing BGE model..."
$installBge = Join-Path $PSScriptRoot "install_bge.ps1"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installBge -Python $venvPython
if ($LASTEXITCODE -ne 0) {
    throw "BGE model installation failed. Check GitHub access and run setup_windows.cmd again."
}

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

if ($NoStart) {
    Write-Host "[6/6] Setup validation completed."
    exit 0
}

$configuredPort = "8088"
Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^RAG_PORT=' } | Select-Object -First 1 | ForEach-Object {
    $configuredPort = ($_ -split '=', 2)[1]
}
Write-Host "[6/6] Starting LAN RAG Pilot..."
Write-Host "Open http://127.0.0.1:$configuredPort after startup. Press Ctrl+C here to stop."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "start_local.ps1") -Python $venvPython
if ($LASTEXITCODE -ne 0) {
    throw "The application stopped with an error. Read the message above and run setup_windows.cmd again."
}
