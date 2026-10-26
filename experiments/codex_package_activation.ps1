# Desktop platform adapter. Windows retains package identity; macOS uses its app bundle.
function Resolve-MacCodexExecutable {
    param([string]$Configured = "")
    $candidates = if ($Configured) { @($Configured) } else {
        @("/Applications/Codex.app", (Join-Path ([Environment]::GetFolderPath("UserProfile")) "Applications/Codex.app"))
    }
    foreach ($candidate in $candidates) {
        $candidate = [IO.Path]::GetFullPath($candidate)
        if ($candidate.EndsWith(".app", [StringComparison]::OrdinalIgnoreCase)) {
            $plist = Join-Path $candidate "Contents/Info.plist"
            if (-not (Test-Path -LiteralPath $plist -PathType Leaf)) { continue }
            $bundleExecutable = & /usr/libexec/PlistBuddy -c "Print :CFBundleExecutable" $plist
            if ($LASTEXITCODE -ne 0 -or -not $bundleExecutable) { throw "无法读取 Codex 应用包入口。" }
            $candidate = Join-Path $candidate ("Contents/MacOS/" + $bundleExecutable.Trim())
        }
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    throw "未找到 Codex.app，请安装到 Applications 或选择其应用包。"
}

function Get-MacCodexProcesses {
    param([string]$Expected = "")
    $executable = Resolve-MacCodexExecutable -Configured $Expected
    $rows = & /bin/ps -axo pid=,comm=
    if ($LASTEXITCODE -ne 0) { throw "无法检测 Codex 进程。" }
    foreach ($row in $rows) {
        if ($row -match '^\s*(\d+)\s+(.+)$' -and $Matches[2] -eq $executable) {
            [PSCustomObject]@{ ProcessId = [int]$Matches[1]; ExecutablePath = $Matches[2]; Name = "Codex" }
        }
    }
}

function Start-CodexDesktopApp {
    param([string]$Executable, [int]$Port)

    $arguments = "--remote-debugging-address=127.0.0.1 --remote-debugging-port=$Port"
    if ($IsMacOS) {
        $resolved = Resolve-MacCodexExecutable -Configured $Executable
        $bundleMarker = $resolved.IndexOf(".app/Contents/", [StringComparison]::OrdinalIgnoreCase)
        if ($bundleMarker -lt 0) { throw "Codex 桌面启动入口必须位于 .app 应用包内。" }
        $bundle = $resolved.Substring(0, $bundleMarker + 4)
        & /usr/bin/open -a $bundle --args "--remote-debugging-address=127.0.0.1" "--remote-debugging-port=$Port"
        if ($LASTEXITCODE -ne 0) { throw "无法打开 Codex.app。" }
        return "macOS"
    }
    $package = Get-AppxPackage -Name "OpenAI.Codex" -ErrorAction SilentlyContinue |
        Sort-Object Version -Descending | Select-Object -First 1
    if (-not $package) { throw "未找到 Codex 商店版安装包。" }
    $installedExecutable = Join-Path $package.InstallLocation "app\ChatGPT.exe"
    if (-not [string]::Equals(
        [IO.Path]::GetFullPath($Executable), [IO.Path]::GetFullPath($installedExecutable),
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "选择的程序不是当前已安装的 Codex 商店版主程序：$Executable"
    }

    if (-not ("CodexToolbox.PackageActivation" -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace CodexToolbox {
    [ComImport, Guid("2e941141-7f97-4756-ba1d-9decde894a3d"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IApplicationActivationManager {
        void ActivateApplication(
            [MarshalAs(UnmanagedType.LPWStr)] string appUserModelId,
            [MarshalAs(UnmanagedType.LPWStr)] string arguments,
            int options,
            out uint processId);
    }

    [ComImport, Guid("45BA127D-10A8-46EA-8AB7-56EA9078943C")]
    class ApplicationActivationManager { }

    public static class PackageActivation {
        public static uint Activate(string appUserModelId, string arguments) {
            var manager = (IApplicationActivationManager)new ApplicationActivationManager();
            try {
                uint processId;
                manager.ActivateApplication(appUserModelId, arguments, 0, out processId);
                return processId;
            } finally {
                Marshal.FinalReleaseComObject(manager);
            }
        }
    }
}
'@
    }
    return [CodexToolbox.PackageActivation]::Activate(
        $package.PackageFamilyName + "!App", $arguments
    )
}
