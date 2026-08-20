#Requires -Version 7.0
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = (Get-Command python -ErrorAction Stop).Source
}

Push-Location $RepoRoot
try {
    & $Python -m unittest discover -s tests -p "test_*.py"
    $env:PYTHONIOENCODING = "utf-8"
    & $Python tests\test_proxy.py
    & $Python scripts\check_secrets.py
    & $Python -m PyInstaller --noconfirm --clean "Codex工具箱.spec"
    & $Python -m PyInstaller --noconfirm --clean "Codex工具箱认证助手.spec"

    $PackageDir = Join-Path $RepoRoot "dist\Codex工具箱-0.1.0-beta-windows-x64"
    if (Test-Path -LiteralPath $PackageDir) {
        Remove-Item -LiteralPath $PackageDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $PackageDir | Out-Null
    Copy-Item -LiteralPath "dist\Codex工具箱.exe" -Destination $PackageDir
    Copy-Item -LiteralPath "dist\Codex工具箱认证助手.exe" -Destination $PackageDir
    Copy-Item -LiteralPath "README.md", "PRIVACY.md", "SECURITY.md", "LICENSE" -Destination $PackageDir

    $Zip = "$PackageDir.zip"
    if (Test-Path -LiteralPath $Zip) {
        Remove-Item -LiteralPath $Zip -Force
    }
    Compress-Archive -Path "$PackageDir\*" -DestinationPath $Zip
    $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Zip).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$Zip.sha256" -Value "$Hash  $(Split-Path -Leaf $Zip)" -Encoding utf8NoBOM
    Write-Host "构建完成：$Zip"
}
finally {
    Pop-Location
}
