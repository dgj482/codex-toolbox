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
    if ($LASTEXITCODE -ne 0) { throw "单元测试失败，已停止打包。" }
    $env:PYTHONIOENCODING = "utf-8"
    & $Python tests\test_proxy.py
    if ($LASTEXITCODE -ne 0) { throw "代理流程测试失败，已停止打包。" }
    & $Python scripts\check_secrets.py
    if ($LASTEXITCODE -ne 0) { throw "敏感信息扫描失败，已停止打包。" }
    & $Python -m PyInstaller --noconfirm --clean "Codex工具箱.spec"
    if ($LASTEXITCODE -ne 0) { throw "主程序打包失败。" }
    $WarningFile = Join-Path $RepoRoot "build\Codex工具箱\warn-Codex工具箱.txt"
    if ((Test-Path -LiteralPath $WarningFile) -and (Select-String -LiteralPath $WarningFile -SimpleMatch "missing module named tkinter" -Quiet)) {
        throw "主程序打包漏掉 tkinter，已停止生成发布包。"
    }
    & $Python -m PyInstaller --noconfirm --clean "Codex工具箱认证助手.spec"
    if ($LASTEXITCODE -ne 0) { throw "认证助手打包失败。" }

    $PackageDir = Join-Path $RepoRoot "dist\Codex工具箱-0.1.0-beta-windows-x64"
    if (Test-Path -LiteralPath $PackageDir) {
        Remove-Item -LiteralPath $PackageDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $PackageDir | Out-Null
    Copy-Item -LiteralPath "dist\Codex工具箱.exe" -Destination $PackageDir
    Copy-Item -LiteralPath "dist\Codex工具箱认证助手.exe" -Destination $PackageDir
    Copy-Item -LiteralPath "README.md", "PRIVACY.md", "SECURITY.md", "LICENSE" -Destination $PackageDir
    $DocsDir = Join-Path $PackageDir "docs"
    New-Item -ItemType Directory -Path $DocsDir | Out-Null
    Copy-Item -LiteralPath "docs\使用手册.md" -Destination $DocsDir

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
