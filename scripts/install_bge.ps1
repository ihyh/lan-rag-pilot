param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot

if (-not $Python) {
    $Python = Join-Path $project ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found: $Python"
}

$modelDir = Join-Path $project "models\bge-small-zh-v1.5"
$zipPath = Join-Path $env:TEMP "bge-small-zh-v1.5-7999e1d.zip"
$url = "https://github.com/ihyh/lan-rag-pilot/releases/download/bge-small-zh-v1.5-7999e1d/bge-small-zh-v1.5-7999e1d.zip"
$expectedSha256 = "0edacc059c0d792466da7b83569c0406aef88b334f6b297d11f5ee5bbf4499c2"

function Test-BgeModel {
    if (-not (Test-Path -LiteralPath (Join-Path $modelDir "model.safetensors"))) {
        return $false
    }
    $result = & $Python -W "ignore::FutureWarning" -c "import sys; from sentence_transformers import SentenceTransformer; m=SentenceTransformer(sys.argv[1], local_files_only=True, device='cpu'); print(m.get_sentence_embedding_dimension())" $modelDir
    if ($LASTEXITCODE -ne 0 -or -not $result) {
        return $false
    }
    return @($result)[-1].Trim() -eq "512"
}

if (Test-BgeModel) {
    Write-Host "BGE model is ready (512 dimensions): $modelDir"
    exit 0
}

Write-Host "Downloading BGE model from GitHub Release..."
Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zipPath
$actualSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
if ($actualSha256 -ne $expectedSha256) {
    Remove-Item -LiteralPath $zipPath -Force
    throw "BGE package checksum failed. Run the script again."
}

New-Item -ItemType Directory -Path (Join-Path $project "models") -Force | Out-Null
Expand-Archive -LiteralPath $zipPath -DestinationPath (Join-Path $project "models") -Force
if (-not (Test-BgeModel)) {
    throw "BGE model validation failed after extraction."
}

Remove-Item -LiteralPath $zipPath -Force
Write-Host "BGE model is ready (512 dimensions): $modelDir"
