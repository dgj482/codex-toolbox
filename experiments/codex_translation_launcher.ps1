<# 为表层翻译准备当前 Codex 的临时 CDP 页面；不修改安装文件或设置。 #>
[CmdletBinding()]
param(
    [string]$CodexExecutable = "",
    [ValidateRange(1024, 65535)][int]$Port = 9229,
    [switch]$Restart
)
$ErrorActionPreference = "Stop"
$utf8 = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
. (Join-Path $PSScriptRoot "codex_package_activation.ps1")

function Get-CodexExecutable {
    if ($IsMacOS) { return Resolve-MacCodexExecutable -Configured $CodexExecutable }
    if ($CodexExecutable) {
        $path = [IO.Path]::GetFullPath($CodexExecutable)
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "指定的 Codex 程序不存在：$path" }
        return $path
    }
    $package = Get-AppxPackage -Name "OpenAI.Codex" -ErrorAction SilentlyContinue |
        Sort-Object Version -Descending | Select-Object -First 1
    if (-not $package) { throw "未找到 Codex 商店版安装包。" }
    $path = Join-Path $package.InstallLocation "app\ChatGPT.exe"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "未找到 Codex 主程序：$path" }
    return $path
}

function Get-CodexProcesses([string]$Expected) {
    if ($IsMacOS) { return Get-MacCodexProcesses -Expected $Expected }
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "ChatGPT.exe" -and $_.ExecutablePath -and $_.ExecutablePath -ieq $Expected
    }
}

function Get-Target([int]$TargetPort, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:$TargetPort/json/list" -TimeoutSec 2
            $target = $null
            foreach ($item in $response) {
                if ($item.type -eq "page" -and $item.url -eq "app://-/index.html" -and $item.webSocketDebuggerUrl) {
                    $target = $item
                    break
                }
            }
            if (-not $target) {
                foreach ($item in $response) {
                    if ($item.type -eq "page" -and $item.url -like "app://*" -and
                        $item.url -notlike "*initialRoute=*avatar-overlay*" -and $item.webSocketDebuggerUrl) {
                        $target = $item
                        break
                    }
                }
            }
            if ($target) { return $target }
        } catch {}
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $deadline)
    return $null
}

$executable = Get-CodexExecutable
$target = Get-Target -TargetPort $Port -Seconds 2
if (-not $target) {
    $running = @(Get-CodexProcesses -Expected $executable)
    if ($running -and -not $Restart) {
        throw 'Codex 正在普通模式运行。请点击第一个按钮重新打开 Codex，或者先完全退出 Codex。'
    }
    if ($running) {
        foreach ($process in $running) { Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue }
        $deadline = (Get-Date).AddSeconds(15)
        do { Start-Sleep -Milliseconds 300; $running = @(Get-CodexProcesses -Expected $executable) }
        while ($running.Count -gt 0 -and (Get-Date) -lt $deadline)
        if ($running.Count -gt 0) { throw "Codex 未能完全退出，已取消翻译模式启动。" }
    }
    # Codex must not inherit this frozen toolbox's private PyInstaller extraction state.
    Get-ChildItem Env: | Where-Object { $_.Name -like '_PYI_*' } |
        ForEach-Object { Remove-Item -LiteralPath ("Env:" + $_.Name) -ErrorAction SilentlyContinue }
    Remove-Item Env:PYINSTALLER_RESET_ENVIRONMENT -ErrorAction SilentlyContinue
    Start-CodexDesktopApp -Executable $executable -Port $Port | Out-Null
    $target = Get-Target -TargetPort $Port -Seconds 75
}
if (-not $target) { throw "Codex 未开放临时调试端口，表层翻译没有启动。" }
$target = @($target)[0]
Write-Output ("CODEX_TOOLBOX_TARGET=" + ($target | ConvertTo-Json -Compress -Depth 6))
