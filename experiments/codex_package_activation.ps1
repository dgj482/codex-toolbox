# Launch the Store app through Windows activation so it receives its package identity.
function Start-CodexDesktopApp {
    param([string]$Executable, [int]$Port)

    $arguments = "--remote-debugging-address=127.0.0.1 --remote-debugging-port=$Port"
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
