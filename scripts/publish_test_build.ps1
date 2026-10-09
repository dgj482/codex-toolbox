# 将最新构建发布到固定的本机测试目录，并更新桌面快捷方式。
# 不创建带版本号的新目录；下一次运行会覆盖同一份测试版。
[CmdletBinding()]
param(
    [string]$SourceDir = "",
    [string]$TargetDir = (Join-Path $PSScriptRoot "..\test-release\Codex工具箱测试版"),
    [string]$ShortcutPath = (Join-Path ([Environment]::GetFolderPath("Desktop")) "Codex工具箱.lnk")
)

$ErrorActionPreference = "Stop"

if (-not $SourceDir) {
    $repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
    $python = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python -ErrorAction Stop).Source }
    $version = (& $python -c "from version import __version__; print(__version__)").Trim()
    $SourceDir = Join-Path $repoRoot "dist\Codex工具箱-$version-windows-x64"
}

$source = [IO.Path]::GetFullPath($SourceDir)
$target = [IO.Path]::GetFullPath($TargetDir)
$shortcut = [IO.Path]::GetFullPath($ShortcutPath)
$mainExe = Join-Path $source "Codex工具箱.exe"
$sourceZip = "$source.zip"
$sourceShortcutIcon = Join-Path $PSScriptRoot "..\assets\codex_toolbox.ico"

if (-not (Test-Path -LiteralPath $mainExe)) {
    throw "未找到构建产物：$mainExe。请先运行 build.ps1。"
}

# 避免覆盖正在运行的同一路径 EXE；其他历史版本不受影响。
$targetExe = Join-Path $target "Codex工具箱.exe"
$runningTarget = Get-Process -Name "Codex工具箱" -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $targetExe }
if ($runningTarget) {
    throw "请先从托盘退出当前桌面工具箱，再覆盖测试版。"
}

New-Item -ItemType Directory -Path $target -Force | Out-Null
Get-ChildItem -LiteralPath $source -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $target -Force
}

if (Test-Path -LiteralPath $sourceZip) {
    $targetZip = Join-Path $target "Codex工具箱_Windows_x64.zip"
    Copy-Item -LiteralPath $sourceZip -Destination $targetZip -Force
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $targetZip).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$targetZip.sha256" -Value "$hash  $(Split-Path -Leaf $targetZip)" -Encoding utf8NoBOM
}

$targetShortcutIcon = Join-Path $target "Codex工具箱图标.ico"
$legacyTargetIcon = Join-Path $target "Codex工具箱.ico"
$legacyShortcutIcon = Join-Path $target "Codex工具箱快捷方式.ico"
if (Test-Path -LiteralPath $legacyTargetIcon) {
    Remove-Item -LiteralPath $legacyTargetIcon -Force
}
if (Test-Path -LiteralPath $legacyShortcutIcon) {
    Remove-Item -LiteralPath $legacyShortcutIcon -Force
}
if (Test-Path -LiteralPath $sourceShortcutIcon) {
    Copy-Item -LiteralPath $sourceShortcutIcon -Destination $targetShortcutIcon -Force
}

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut)
$link.TargetPath = $targetExe
$link.WorkingDirectory = $target
$link.IconLocation = if (Test-Path -LiteralPath $targetShortcutIcon) {
    "$targetShortcutIcon,0"
} else {
    "$targetExe,0"
}
$link.Save()

Write-Host "已覆盖本机测试版：$target"
Write-Host "已更新桌面快捷方式：$shortcut"
