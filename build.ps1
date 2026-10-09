#Requires -Version 7.0
param([string]$PythonExecutable = "")
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = if ($PythonExecutable) { [IO.Path]::GetFullPath($PythonExecutable) } else { Join-Path $RepoRoot ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    if ($PythonExecutable) { throw "指定的 Python 不存在：$Python" }
    $Python = (Get-Command python -ErrorAction Stop).Source
}

Push-Location $RepoRoot
try {
    $Version = (& $Python -c "from version import __version__; print(__version__)").Trim()
    if (-not $Version) { throw "无法读取版本号。" }
    & $Python scripts\build_metadata.py version-info version_info.txt
    if ($LASTEXITCODE -ne 0) { throw "生成 Windows 版本资源失败。" }
    & $Python -m unittest discover -s tests -p "test_*.py"
    if ($LASTEXITCODE -ne 0) { throw "单元测试失败，已停止打包。" }
    $env:PYTHONIOENCODING = "utf-8"
    & $Python tests\test_proxy.py
    if ($LASTEXITCODE -ne 0) { throw "代理流程测试失败，已停止打包。" }
    & $Python scripts\check_secrets.py
    if ($LASTEXITCODE -ne 0) { throw "敏感信息扫描失败，已停止打包。" }
    & node scripts\check_ui_syntax.js
    if ($LASTEXITCODE -ne 0) { throw "界面 JavaScript 语法检查失败，已停止打包。" }
    & $Python -m PyInstaller --noconfirm --clean "Codex工具箱.spec"
    if ($LASTEXITCODE -ne 0) { throw "主程序打包失败。" }
    $WarningFile = Join-Path $RepoRoot "build\Codex工具箱\warn-Codex工具箱.txt"
    if ((Test-Path -LiteralPath $WarningFile) -and (Select-String -LiteralPath $WarningFile -SimpleMatch "missing module named tkinter" -Quiet)) {
        throw "主程序打包漏掉 tkinter，已停止生成发布包。"
    }
    & $Python scripts\check_single_file_auth.py (Join-Path $RepoRoot "dist\Codex工具箱.exe")
    if ($LASTEXITCODE -ne 0) { throw "单文件认证管道自检失败，已停止打包。" }

    $DistRoot = [IO.Path]::GetFullPath((Join-Path $RepoRoot "dist"))
    $LegacyBuildArtifact = [IO.Path]::GetFullPath((Join-Path $DistRoot "Codex工具箱认证助手.exe"))
    if ($LegacyBuildArtifact.StartsWith($DistRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath $LegacyBuildArtifact -PathType Leaf)) {
        Remove-Item -LiteralPath $LegacyBuildArtifact -Force
    }
    $PackageDir = [IO.Path]::GetFullPath((Join-Path $DistRoot "Codex工具箱-$Version-windows-x64"))
    if (-not $PackageDir.StartsWith($DistRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "发布目录越界，已停止打包。"
    }
    if (Test-Path -LiteralPath $PackageDir) {
        Remove-Item -LiteralPath $PackageDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $PackageDir | Out-Null
    Copy-Item -LiteralPath "dist\Codex工具箱.exe" -Destination $PackageDir
    Copy-Item -LiteralPath "README.md", "PRIVACY.md", "SECURITY.md", "LICENSE", "CHANGELOG.md" -Destination $PackageDir
    $DocsDir = Join-Path $PackageDir "docs"
    New-Item -ItemType Directory -Path $DocsDir | Out-Null
    Copy-Item -LiteralPath "docs\使用手册.md" -Destination $DocsDir
    $SelfTest = Join-Path $PackageDir "self-test.json"
    $SelfTestProcess = Start-Process -FilePath (Join-Path $PackageDir "Codex工具箱.exe") `
        -ArgumentList @("--self-test", $SelfTest) -WindowStyle Hidden -Wait -PassThru
    if ($SelfTestProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $SelfTest)) { throw "构建产物自检失败。" }
    $SelfTestResult = Get-Content -LiteralPath $SelfTest -Raw -Encoding utf8 | ConvertFrom-Json
    if (-not $SelfTestResult.ok -or $SelfTestResult.version -ne $Version) { throw "构建产物版本或安全自检不一致。" }
    & $Python scripts\build_metadata.py manifest (Join-Path $PackageDir "release-manifest.json")
    if ($LASTEXITCODE -ne 0) { throw "生成发布清单失败。" }

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
    Remove-Item -LiteralPath (Join-Path $RepoRoot "version_info.txt") -Force -ErrorAction SilentlyContinue
    Pop-Location
}
