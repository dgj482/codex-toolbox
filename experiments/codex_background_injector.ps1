<#
  Codex 背景注入实验（非官方功能）

  目标：只通过本机回环地址的 Chrome DevTools Protocol 临时注入 CSS。
  不修改 WindowsApps、app.asar、Codex 配置、账号或会话数据。

  使用前必须完全退出 Codex（包括托盘），否则商店版会复用旧实例，
  启动参数无法生效。脚本每次执行时仅影响本次启动的 Codex；关闭或更新后
  背景自然失效，需再次执行。
#>

[CmdletBinding()]
param(
    [string]$ImagePath = "",
    [string]$VideoPath = "",
    [string]$CodexExecutable = "",
    [ValidateRange(1024, 65535)]
    [int]$Port = 9229,
    [ValidateRange(1, 90)]
    [int]$AttachWaitSeconds = 2,
    [ValidateRange(0, 90)]
    [int]$BackgroundMask = 42,
    [ValidateRange(0, 100)]
    [int]$PanelOpacity = 58,
    [ValidateRange(0, 100)]
    [int]$SidebarOpacity = 42,
    [ValidateRange(0, 100)]
    [int]$ToolbarOpacity = 28,
    [ValidateRange(0, 100)]
    [int]$ThreadHeaderOpacity = 28,
    [ValidateRange(0, 100)]
    [int]$CardOpacity = 24,
    [ValidateRange(0, 20)]
    [int]$Blur = 6,
    [ValidateRange(10, 160)]
    [int]$BackgroundScale = 100,
    [ValidateRange(10, 160)]
    [int]$BackgroundHeight = 100,
    [ValidateRange(0, 100)]
    [int]$BackgroundPositionX = 50,
    [ValidateRange(0, 100)]
    [int]$BackgroundPositionY = 50,
    [ValidateRange(0, 100)]
    [int]$BorderOpacity = 22,
    [ValidateRange(0, 28)]
    [int]$CornerRadius = 12,
    [ValidateSet("cover", "contain")]
    [string]$MediaFit = "cover",
    [ValidateSet("whole", "content", "sidebar")]
    [string]$BackgroundScope = "whole",
    [ValidateSet("none", "rect", "circle", "line", "freehand")]
    [string]$ProjectionShape = "none",
    [string]$ProjectionPointsJson = "[]",
    [string]$ProjectionLayersJson = "[]",
    [ValidateSet("scale", "crop")]
    [string]$ProjectionMode = "scale",
    [bool]$GradientEnabled = $false,
    [ValidateSet("linear", "radial")]
    [string]$GradientType = "linear",
    [ValidatePattern("^#[0-9A-Fa-f]{6}$")]
    [string]$GradientColorStart = "#5868df",
    [ValidatePattern("^#[0-9A-Fa-f]{6}$")]
    [string]$GradientColorEnd = "#2bb984",
    [ValidateRange(0, 80)]
    [int]$GradientOpacity = 28,
    [ValidateRange(0, 360)]
    [int]$GradientAngle = 135,
    [ValidateSet("compact", "normal", "topbar", "hidden")]
    [string]$TitlebarMode = "compact",
    [bool]$CompactDiff = $true,
    [bool]$AllowAnimatedGif = $false,
    [bool]$AllowVideo = $false,
    [switch]$Restart,
    [switch]$Remove,
    [switch]$Check
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
# 工具箱会捕获本脚本的输出显示在界面中：关闭 ANSI 色彩并统一 UTF-8，
# 避免 Windows 终端控制字符或中文编码被直接显示成乱码。
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSStyle.OutputRendering = "PlainText"
}
$utf8 = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
. (Join-Path $PSScriptRoot "codex_package_activation.ps1")

function Get-CodexDesktopProcess {
    param([string]$ExpectedExecutable = "")
    $expected = if ([string]::IsNullOrWhiteSpace($ExpectedExecutable)) {
        ""
    } else {
        [IO.Path]::GetFullPath($ExpectedExecutable)
    }
    Get-CimInstance Win32_Process | Where-Object {
        if ($expected) {
            $_.ExecutablePath -and $_.ExecutablePath -ieq $expected
        } else {
            $_.Name -eq "ChatGPT.exe" -and $_.ExecutablePath -like "*OpenAI.Codex*"
        }
    }
}

function Get-CodexExecutable {
    if (-not [string]::IsNullOrWhiteSpace($CodexExecutable)) {
        $configured = [IO.Path]::GetFullPath($CodexExecutable)
        if (-not (Test-Path -LiteralPath $configured -PathType Leaf) -or
            [IO.Path]::GetExtension($configured).ToLowerInvariant() -ne ".exe") {
            throw "指定的 Codex 启动程序不存在或不是 .exe 文件：$configured"
        }
        return $configured
    }
    $package = Get-AppxPackage -Name "OpenAI.Codex" -ErrorAction SilentlyContinue |
        Sort-Object Version -Descending | Select-Object -First 1
    if (-not $package) {
        throw "未找到 Codex 商店版安装包。请先安装 Codex。"
    }
    $executable = Join-Path $package.InstallLocation "app\ChatGPT.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "未找到 Codex 主程序：$executable"
    }
    return $executable
}

function Wait-DebugEndpoint {
    param(
        [int]$TargetPort,
        [int]$TimeoutSeconds = 75
    )
    # /json/list 只返回可注入的页面目标；端口刚开放但页面尚未建立时继续等待，
    # 避免把“端口已启动、主页面还未就绪”误报为背景启动失败。
    $url = "http://127.0.0.1:$TargetPort/json/list"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $targets = @(Invoke-RestMethod -Uri $url -TimeoutSec 2)
            $mainPage = @($targets | Where-Object {
                $_.type -eq "page" -and $_.url -like "app://*" -and $_.webSocketDebuggerUrl
            })
            if ($mainPage.Count -gt 0) {
                return $targets
            }
        } catch {
            # CDP 服务在启动期间短暂拒绝连接属于正常现象，继续轮询即可。
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $null
}

function Get-BackgroundDataUri {
    param(
        [string]$Path,
        [bool]$AllowAnimation = $false
    )

    $extension = [IO.Path]::GetExtension($Path).ToLowerInvariant()
    $mimeType = switch ($extension) {
        ".png" { "image/png" }
        ".jpg" { "image/jpeg" }
        ".jpeg" { "image/jpeg" }
        ".webp" { "image/webp" }
        ".gif" { "image/gif" }
        default { throw "不支持的背景图片格式：$extension" }
    }
    $bytes = [IO.File]::ReadAllBytes($Path)
    # 动态背景仍只作为 CSS 的 background-image 注入，不创建视频元素也不开放任意脚本。
    # GIF 必须保留原始帧才能播放，因此设定较小体积上限以避免 CDP 消息过大。
    if ($extension -eq ".gif") {
        if (-not $AllowAnimation) {
            throw "动态 GIF 默认关闭，请在工具箱中明确启用后再应用。"
        }
        if ($bytes.Length -gt 3MB) {
            throw "动态 GIF 超过 3 MB，请压缩后再使用。"
        }
        return "data:$mimeType;base64,$([Convert]::ToBase64String($bytes))"
    }
    # DevTools 单条消息有大小限制，原图过大时压缩为只用于本次注入的 JPEG。
    if ($bytes.Length -le 500000) {
        return "data:$mimeType;base64,$([Convert]::ToBase64String($bytes))"
    }

    Add-Type -AssemblyName System.Drawing
    $source = [Drawing.Image]::FromFile($Path)
    try {
        $width = [Math]::Min(1280, $source.Width)
        $height = [int][Math]::Round($source.Height * $width / $source.Width)
        $bitmap = [Drawing.Bitmap]::new($width, $height)
        $graphics = [Drawing.Graphics]::FromImage($bitmap)
        $stream = [IO.MemoryStream]::new()
        try {
            $graphics.DrawImage($source, 0, 0, $width, $height)
            $jpeg = [Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() |
                Where-Object { $_.MimeType -eq "image/jpeg" }
            $parameters = [Drawing.Imaging.EncoderParameters]::new(1)
            $parameters.Param[0] = [Drawing.Imaging.EncoderParameter]::new(
                [Drawing.Imaging.Encoder]::Quality, [long]78
            )
            $bitmap.Save($stream, $jpeg, $parameters)
            return "data:image/jpeg;base64,$([Convert]::ToBase64String($stream.ToArray()))"
        }
        finally {
            $stream.Dispose()
            $graphics.Dispose()
            $bitmap.Dispose()
        }
    }
    finally {
        $source.Dispose()
    }
}

function Send-CdpCommand {
    param(
        [uri]$WebSocketUrl,
        [hashtable]$Request
    )
    $socket = [System.Net.WebSockets.ClientWebSocket]::new()
    try {
        $socket.ConnectAsync($WebSocketUrl, [Threading.CancellationToken]::None).GetAwaiter().GetResult() | Out-Null
        $json = $Request | ConvertTo-Json -Depth 8 -Compress
        $bytes = [Text.Encoding]::UTF8.GetBytes($json)
        $segment = [ArraySegment[byte]]::new($bytes)
        $socket.SendAsync(
            $segment,
            [System.Net.WebSockets.WebSocketMessageType]::Text,
            $true,
            [Threading.CancellationToken]::None
        ).GetAwaiter().GetResult() | Out-Null

        $deadline = (Get-Date).AddSeconds(10)
        do {
            $buffer = New-Object byte[] 65536
            $received = $socket.ReceiveAsync(
                [ArraySegment[byte]]::new($buffer),
                [Threading.CancellationToken]::None
            ).GetAwaiter().GetResult()
            $response = [Text.Encoding]::UTF8.GetString($buffer, 0, $received.Count) | ConvertFrom-Json
            # CDP 会先推送页面事件；只能接受与本次请求 id 相同的正式响应。
            if ($response.id -eq $Request.id) {
                return $response
            }
        } while ((Get-Date) -lt $deadline)
        throw "等待 Codex 调试接口响应超时。"
    }
    finally {
        if ($socket.State -eq [System.Net.WebSockets.WebSocketState]::Open) {
            $socket.CloseAsync(
                [System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure,
                "done",
                [Threading.CancellationToken]::None
            ).GetAwaiter().GetResult() | Out-Null
        }
        $socket.Dispose()
    }
}

if ($Check) {
    $running = @(Get-CodexDesktopProcess -ExpectedExecutable $CodexExecutable)
    Write-Host "Codex 主程序：$(Get-CodexExecutable)"
    Write-Host "Codex 是否运行：$([bool]$running)"
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$Port/json/version" | Out-Null
        Write-Host "调试端口 $Port：可连接"
    } catch {
        Write-Host "调试端口 $Port：未开放"
    }
    exit 0
}

if (-not $Remove -and [string]::IsNullOrWhiteSpace($VideoPath)) {
    $ImagePath = [IO.Path]::GetFullPath($ImagePath)
    if (-not (Test-Path -LiteralPath $ImagePath -PathType Leaf)) {
        throw "背景图片不存在：$ImagePath"
    }
}
if (-not $Remove -and -not [string]::IsNullOrWhiteSpace($VideoPath)) {
    if (-not $AllowVideo) {
        throw "本地 MP4 背景默认关闭，请在工具箱中明确启用后再应用。"
    }
    $VideoPath = [IO.Path]::GetFullPath($VideoPath)
    if (-not (Test-Path -LiteralPath $VideoPath -PathType Leaf)) {
        throw "MP4 文件不存在：$VideoPath"
    }
    if ([IO.Path]::GetExtension($VideoPath).ToLowerInvariant() -ne ".mp4") {
        throw "动态背景仅支持 MP4 文件。"
    }
    if ((Get-Item -LiteralPath $VideoPath).Length -gt 30MB) {
        throw "MP4 超过 30 MB，无法安全载入当前 Codex 窗口。"
    }
}

$targets = $null
$runningCodex = @(Get-CodexDesktopProcess -ExpectedExecutable $CodexExecutable)
if ($runningCodex) {
    # 允许启动较慢时再次执行脚本，直接附加到此前已打开的本机调试端口。
    $targets = Wait-DebugEndpoint -TargetPort $Port -TimeoutSeconds $AttachWaitSeconds
    if (-not $targets) {
        if ($Remove) {
            Write-Host "当前 Codex 没有可连接的背景页面，无需恢复。"
            exit 0
        }
        if (-not $Restart) {
            throw "Codex 当前正在运行，但不是通过背景实验启动的，未开放本机调试端口。请完全退出 Codex 后再运行此脚本。"
        }
        Write-Host "检测到普通启动的 Codex，正在关闭并以背景模式重启 …"
        # 关闭主进程时 Chromium 子进程会自行连带退出；遇到已退出的 PID 继续即可。
        foreach ($process in $runningCodex) {
            # 进程可能已被 Codex 主进程或其他实例连带关闭；先重新查询 PID，
            # 避免 Stop-Process 对已不存在的 PID 抛出误导性错误。
            $currentProcess = Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
            if ($currentProcess) {
                Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
        $shutdownDeadline = (Get-Date).AddSeconds(15)
        do {
            Start-Sleep -Milliseconds 300
            $runningCodex = @(Get-CodexDesktopProcess -ExpectedExecutable $CodexExecutable)
        } while ($runningCodex.Count -gt 0 -and (Get-Date) -lt $shutdownDeadline)
        if ($runningCodex.Count -gt 0) {
            throw "Codex 未能在 15 秒内完全退出，已取消背景重启。"
        }
    }
    if ($targets) {
        Write-Host "检测到已开放的本机调试端口，直接附加到当前 Codex …"
    }
}
if ($Remove -and -not $targets) {
    Write-Host "当前没有可连接的背景页面，无需恢复。"
    exit 0
}
if (-not $targets) {
    $codexExecutable = Get-CodexExecutable
    # Keep a future toolbox launch from reusing this frozen process's temporary Python runtime.
    Get-ChildItem Env: | Where-Object { $_.Name -like '_PYI_*' } |
        ForEach-Object { Remove-Item -LiteralPath ("Env:" + $_.Name) -ErrorAction SilentlyContinue }
    Remove-Item Env:PYINSTALLER_RESET_ENVIRONMENT -ErrorAction SilentlyContinue
    $appProcessId = Start-CodexDesktopApp -Executable $codexExecutable -Port $Port
    Write-Host "已通过 Windows 包激活 Codex（PID：$appProcessId），最多等待 75 秒开启本机调试端口 $Port …"
    $targets = Wait-DebugEndpoint -TargetPort $Port
}

if (-not $targets) {
    throw @"
Codex 已启动，但没有开放调试端口。没有执行任何注入，也没有修改官方文件。
这说明当前商店版拒绝了 Chromium 调试参数；本次临时注入方案在该版本不可用。
"@
}

$targets = @($targets | Where-Object {
    $_.type -eq "page" -and $_.url -like "app://*" -and $_.webSocketDebuggerUrl
})
if (-not $targets) {
    throw "调试端口已开放，但没有找到可注入的 Codex 页面。"
}
# 只将大媒体层注入真正的主界面。头像覆盖层等辅助页面不需要背景，
# 重复加载同一个 MP4 会显著增加内存占用，还可能让主页面无法完成解码。
$mainTargets = @($targets | Where-Object { $_.url -eq "app://-/index.html" })
if ($mainTargets) {
    $targets = $mainTargets
}

$styleId = "codex-toolbox-background-experiment"
$videoId = "codex-toolbox-background-video"
if ($Remove) {
    $javascript = "document.getElementById('$styleId')?.remove(); document.getElementById('$videoId')?.remove(); '背景样式已移除';"
} else {
    $culture = [Globalization.CultureInfo]::InvariantCulture
    $maskAlpha = ($BackgroundMask / 100).ToString("0.00", $culture)
    $panelAlpha = ($PanelOpacity / 100).ToString("0.00", $culture)
    $sidebarAlpha = ($SidebarOpacity / 100).ToString("0.00", $culture)
    $toolbarAlpha = ($ToolbarOpacity / 100).ToString("0.00", $culture)
    $threadHeaderAlpha = ($ThreadHeaderOpacity / 100).ToString("0.00", $culture)
    $cardAlpha = ($CardOpacity / 100).ToString("0.00", $culture)
    $borderAlpha = ($BorderOpacity / 100).ToString("0.00", $culture)
    $useVideo = -not [string]::IsNullOrWhiteSpace($VideoPath)
    $backgroundClip = switch ($BackgroundScope) {
        "content" { "inset(0 0 0 20%)" }
        "sidebar" { "inset(0 80% 0 0)" }
        default { "none" }
    }
    # 投影图形只由工具箱传递的 0-1 坐标生成 CSS clip-path，不接受任意 CSS 或脚本。
    $projectionPoints = @()
    try {
        $parsedPoints = @($ProjectionPointsJson | ConvertFrom-Json -ErrorAction Stop)
        foreach ($point in $parsedPoints | Select-Object -First 80) {
            $x = [double]$point.x
            $y = [double]$point.y
            if ($x -lt 0 -or $x -gt 1 -or $y -lt 0 -or $y -gt 1) { throw "投影坐标超出范围" }
            $projectionPoints += [PSCustomObject]@{ x = $x; y = $y }
        }
    } catch {
        throw "投影形状参数无效。"
    }
    $projectionClip = "none"
    if ($ProjectionShape -eq "rect" -and $projectionPoints.Count -ge 2) {
        $a, $b = $projectionPoints[0], $projectionPoints[1]
        $left = [Math]::Min($a.x, $b.x) * 100
        $top = [Math]::Min($a.y, $b.y) * 100
        $width = [Math]::Max(2, [Math]::Abs($a.x - $b.x) * 100)
        $height = [Math]::Max(2, [Math]::Abs($a.y - $b.y) * 100)
        $projectionClip = "inset($($top.ToString('0.##', $culture))% $((100 - $left - $width).ToString('0.##', $culture))% $((100 - $top - $height).ToString('0.##', $culture))% $($left.ToString('0.##', $culture))%)"
    } elseif ($ProjectionShape -eq "circle" -and $projectionPoints.Count -ge 2) {
        $center, $edge = $projectionPoints[0], $projectionPoints[1]
        $radiusX = [Math]::Max(2, [Math]::Abs($edge.x - $center.x) * 100)
        $radiusY = [Math]::Max(2, [Math]::Abs($edge.y - $center.y) * 100)
        $projectionClip = "ellipse($($radiusX.ToString('0.##', $culture))% $($radiusY.ToString('0.##', $culture))% at $((100 * $center.x).ToString('0.##', $culture))% $((100 * $center.y).ToString('0.##', $culture))%)"
    } elseif ($ProjectionShape -eq "line" -and $projectionPoints.Count -ge 2) {
        $a, $b = $projectionPoints[0], $projectionPoints[1]
        $dx, $dy = $b.x - $a.x, $b.y - $a.y
        $length = [Math]::Sqrt($dx * $dx + $dy * $dy)
        if ($length -lt 0.01) { throw "直线投影长度过短。" }
        $width = 0.025
        $offsetX, $offsetY = -$dy / $length * $width, $dx / $length * $width
        $linePoints = @(
            "$((100 * ($a.x + $offsetX)).ToString('0.##', $culture))% $((100 * ($a.y + $offsetY)).ToString('0.##', $culture))%",
            "$((100 * ($b.x + $offsetX)).ToString('0.##', $culture))% $((100 * ($b.y + $offsetY)).ToString('0.##', $culture))%",
            "$((100 * ($b.x - $offsetX)).ToString('0.##', $culture))% $((100 * ($b.y - $offsetY)).ToString('0.##', $culture))%",
            "$((100 * ($a.x - $offsetX)).ToString('0.##', $culture))% $((100 * ($a.y - $offsetY)).ToString('0.##', $culture))%"
        )
        $projectionClip = "polygon($($linePoints -join ', '))"
    } elseif ($ProjectionShape -eq "freehand" -and $projectionPoints.Count -ge 2) {
        # 将自由笔迹扩展为一条圆角感的带状多边形，而不是把首尾闭合成区域。
        # 这样曲线投影不会被错误地显示为一个大方框。
        $strokeWidth = 0.025
        $leftPoints, $rightPoints = @(), @()
        for ($index = 0; $index -lt $projectionPoints.Count; $index++) {
            $previous = $projectionPoints[[Math]::Max(0, $index - 1)]
            $next = $projectionPoints[[Math]::Min($projectionPoints.Count - 1, $index + 1)]
            $dx, $dy = $next.x - $previous.x, $next.y - $previous.y
            $length = [Math]::Sqrt($dx * $dx + $dy * $dy)
            if ($length -lt 0.001) { continue }
            $offsetX, $offsetY = -$dy / $length * $strokeWidth, $dx / $length * $strokeWidth
            $point = $projectionPoints[$index]
            $leftPoints += [PSCustomObject]@{ x = [Math]::Max(0, [Math]::Min(1, $point.x + $offsetX)); y = [Math]::Max(0, [Math]::Min(1, $point.y + $offsetY)) }
            $rightPoints += [PSCustomObject]@{ x = [Math]::Max(0, [Math]::Min(1, $point.x - $offsetX)); y = [Math]::Max(0, [Math]::Min(1, $point.y - $offsetY)) }
        }
        if ($leftPoints.Count -ge 2 -and $rightPoints.Count -ge 2) {
            $curvePoints = @($leftPoints)
            for ($index = $rightPoints.Count - 1; $index -ge 0; $index--) { $curvePoints += $rightPoints[$index] }
            $polygonPoints = @($curvePoints | ForEach-Object {
                "$((100 * $_.x).ToString('0.##', $culture))% $((100 * $_.y).ToString('0.##', $culture))%"
            })
            $projectionClip = "polygon($($polygonPoints -join ', '))"
        }
    }
    # CSS clip-path 不能可靠地叠加两个裁剪；绘制图形时优先保证图形本身。
    if ($projectionClip -ne "none") { $backgroundClip = $projectionClip }
    # 多图层投影使用受限坐标生成 SVG 蒙版，只允许圆形、直线和自由笔三种图元。
    $projectionMaskCss = ""
    $projectionPlacementCss = "inset: 0;"
    $projectionActive = $false
    $projectionFit = $MediaFit
    try {
        $maskShapes = @()
        $projectionMinX, $projectionMinY = 1000.0, 1000.0
        $projectionMaxX, $projectionMaxY = 0.0, 0.0
        $rawLayers = @($ProjectionLayersJson | ConvertFrom-Json -ErrorAction Stop)
        foreach ($layer in $rawLayers | Select-Object -First 20) {
            if ($layer.type -notin @("rect", "circle", "line", "freehand")) { continue }
            # 未勾选的候选区域只保留在本地画布中，不进入最终背景蒙版。
            if (($layer.PSObject.Properties.Name -contains "enabled") -and -not [bool]$layer.enabled) { continue }
            $layerPoints = @()
            foreach ($point in @($layer.points) | Select-Object -First 80) {
                $x, $y = [double]$point.x, [double]$point.y
                if ($x -lt 0 -or $x -gt 1 -or $y -lt 0 -or $y -gt 1) { throw "投影坐标超出范围" }
                $layerPoints += [PSCustomObject]@{ x = $x * 1000; y = $y * 1000 }
            }
            if ($layerPoints.Count -lt 2) { continue }
            $layerWidth = 0.09
            if ($layer.PSObject.Properties.Name -contains "width") {
                $layerWidth = [Math]::Max(0.02, [Math]::Min(0.24, [double]$layer.width))
            }
            $strokeWidth = [Math]::Max(20, $layerWidth * 1000)
            if ($layer.type -eq "rect") {
                $a, $b = $layerPoints[0], $layerPoints[1]
                $left = [Math]::Min($a.x, $b.x)
                $top = [Math]::Min($a.y, $b.y)
                $width = [Math]::Max(20, [Math]::Abs($a.x - $b.x))
                $height = [Math]::Max(20, [Math]::Abs($a.y - $b.y))
                $maskShapes += "<rect x='$($left.ToString('0.##', $culture))' y='$($top.ToString('0.##', $culture))' width='$($width.ToString('0.##', $culture))' height='$($height.ToString('0.##', $culture))' fill='white'/>"
                $projectionMinX = [Math]::Min($projectionMinX, $left)
                $projectionMaxX = [Math]::Max($projectionMaxX, $left + $width)
                $projectionMinY = [Math]::Min($projectionMinY, $top)
                $projectionMaxY = [Math]::Max($projectionMaxY, $top + $height)
            } elseif ($layer.type -eq "circle") {
                $center, $edge = $layerPoints[0], $layerPoints[1]
                $rx = [Math]::Max(20, [Math]::Abs($edge.x - $center.x))
                $ry = [Math]::Max(20, [Math]::Abs($edge.y - $center.y))
                $maskShapes += "<ellipse cx='$($center.x.ToString('0.##', $culture))' cy='$($center.y.ToString('0.##', $culture))' rx='$($rx.ToString('0.##', $culture))' ry='$($ry.ToString('0.##', $culture))' fill='white'/>"
                $projectionMinX = [Math]::Min($projectionMinX, $center.x - $rx)
                $projectionMaxX = [Math]::Max($projectionMaxX, $center.x + $rx)
                $projectionMinY = [Math]::Min($projectionMinY, $center.y - $ry)
                $projectionMaxY = [Math]::Max($projectionMaxY, $center.y + $ry)
            } elseif ($layer.type -eq "line") {
                $maskShapes += "<line x1='$($layerPoints[0].x.ToString('0.##', $culture))' y1='$($layerPoints[0].y.ToString('0.##', $culture))' x2='$($layerPoints[1].x.ToString('0.##', $culture))' y2='$($layerPoints[1].y.ToString('0.##', $culture))' stroke='white' stroke-width='$($strokeWidth.ToString('0.##', $culture))' stroke-linecap='round'/>"
            } else {
                $pointsText = @($layerPoints | ForEach-Object { "$($_.x.ToString('0.##', $culture)),$($_.y.ToString('0.##', $culture))" }) -join " "
                $maskShapes += "<polyline points='$pointsText' fill='none' stroke='white' stroke-width='$($strokeWidth.ToString('0.##', $culture))' stroke-linecap='round' stroke-linejoin='round'/>"
            }
            if ($layer.type -notin @("circle", "rect")) {
                $padding = $strokeWidth / 2
                foreach ($point in $layerPoints) {
                    $projectionMinX = [Math]::Min($projectionMinX, $point.x - $padding)
                    $projectionMaxX = [Math]::Max($projectionMaxX, $point.x + $padding)
                    $projectionMinY = [Math]::Min($projectionMinY, $point.y - $padding)
                    $projectionMaxY = [Math]::Max($projectionMaxY, $point.y + $padding)
                }
            }
        }
        if ($maskShapes.Count -gt 0) {
            $projectionMinX = [Math]::Max(0, [Math]::Min(980, $projectionMinX))
            $projectionMinY = [Math]::Max(0, [Math]::Min(980, $projectionMinY))
            $projectionMaxX = [Math]::Max($projectionMinX + 20, [Math]::Min(1000, $projectionMaxX))
            $projectionMaxY = [Math]::Max($projectionMinY + 20, [Math]::Min(1000, $projectionMaxY))
            $projectionWidth, $projectionHeight = $projectionMaxX - $projectionMinX, $projectionMaxY - $projectionMinY
            $viewBox = "$($projectionMinX.ToString('0.##', $culture)) $($projectionMinY.ToString('0.##', $culture)) $($projectionWidth.ToString('0.##', $culture)) $($projectionHeight.ToString('0.##', $culture))"
            $svgMask = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='$viewBox'>$($maskShapes -join '')</svg>"
            $maskData = "data:image/svg+xml;base64,$([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($svgMask)))"
            $projectionMaskCss = "-webkit-mask-image:url('$maskData') !important; mask-image:url('$maskData') !important; -webkit-mask-size:100% 100% !important; mask-size:100% 100% !important; -webkit-mask-repeat:no-repeat !important; mask-repeat:no-repeat !important;"
            $left = (100 * $projectionMinX / 1000).ToString("0.##", $culture)
            $top = (100 * $projectionMinY / 1000).ToString("0.##", $culture)
            $width = (100 * $projectionWidth / 1000).ToString("0.##", $culture)
            $height = (100 * $projectionHeight / 1000).ToString("0.##", $culture)
            $projectionPlacementCss = "left: ${left}vw; top: ${top}vh; width: ${width}vw; height: ${height}vh;"
            $projectionFit = if ($ProjectionMode -eq "crop") { "cover" } else { "contain" }
            $projectionActive = $true
            $backgroundClip = "none"
        }
    } catch {
        throw "投影图层参数无效。"
    }
    $gradientAlpha = ($GradientOpacity / 100).ToString("0.00", $culture)
    $startR = [Convert]::ToInt32($GradientColorStart.Substring(1, 2), 16)
    $startG = [Convert]::ToInt32($GradientColorStart.Substring(3, 2), 16)
    $startB = [Convert]::ToInt32($GradientColorStart.Substring(5, 2), 16)
    $endR = [Convert]::ToInt32($GradientColorEnd.Substring(1, 2), 16)
    $endG = [Convert]::ToInt32($GradientColorEnd.Substring(3, 2), 16)
    $endB = [Convert]::ToInt32($GradientColorEnd.Substring(5, 2), 16)
    $gradientLayer = if (-not $GradientEnabled) { "" } elseif ($GradientType -eq "radial") {
        "radial-gradient(circle at center, rgba($startR,$startG,$startB,$gradientAlpha), rgba($endR,$endG,$endB,$gradientAlpha))"
    } else {
        "linear-gradient(${GradientAngle}deg, rgba($startR,$startG,$startB,$gradientAlpha), rgba($endR,$endG,$endB,$gradientAlpha))"
    }
    $overlayLayers = @("linear-gradient(rgba(246, 250, 255, $maskAlpha), rgba(246, 250, 255, $maskAlpha))")
    if ($gradientLayer) { $overlayLayers += $gradientLayer }
    if ($useVideo) {
        $backgroundLayer = $overlayLayers -join ", "
        # Codex 的 app:// 页面会拒绝 http://127.0.0.1 媒体。仅对用户选中的小型 MP4
        # 生成 data: URL，在当前页面内存中播放；不开放网络地址或任意脚本输入。
        $videoDataUri = "data:video/mp4;base64,$([Convert]::ToBase64String([IO.File]::ReadAllBytes($VideoPath)))"
        $videoUrlJson = $videoDataUri | ConvertTo-Json -Compress
        $videoPlacementCss = if ($projectionActive) { "$projectionPlacementCss transform: none !important;" } else {
            "left: ${BackgroundPositionX}% !important; top: ${BackgroundPositionY}% !important; width: ${BackgroundScale}vw !important; height: ${BackgroundHeight}vh !important; transform: translate(-${BackgroundPositionX}%, -${BackgroundPositionY}%) !important;"
        }
        $videoCss = @"
#$videoId {
  position: fixed !important;
  $videoPlacementCss
  max-width: none !important;
  max-height: none !important;
  object-fit: $projectionFit !important;
  object-position: ${BackgroundPositionX}% ${BackgroundPositionY}% !important;
  filter: none !important;
  clip-path: $backgroundClip !important;
  $projectionMaskCss
  z-index: 0 !important;
  pointer-events: none !important;
}
"@
    } else {
        # app:// 页面不能直接访问 file:///，因此仅把图片编码到本次内存注入中。
        $imageUri = Get-BackgroundDataUri -Path $ImagePath -AllowAnimation:$AllowAnimatedGif
        # 全覆盖模式使用 CSS cover，确保图片同时覆盖窗口宽高；普通模式使用
        # 用户设置的宽度和高度，令画布预览与实际注入保持一致。
        $imageSize = if ($projectionActive) {
            $projectionFit
        } elseif ($MediaFit -eq "cover" -and $BackgroundScale -eq 100 -and $BackgroundHeight -eq 100) {
            "cover"
        } else {
            "${BackgroundScale}vw ${BackgroundHeight}vh"
        }
        $backgroundLayer = "$($overlayLayers -join ', '), url('$imageUri') ${BackgroundPositionX}% ${BackgroundPositionY}% / $imageSize no-repeat"
        $videoUrlJson = "null"
        $videoCss = ""
    }
    $compactDiffCss = if ($CompactDiff) {
@"
/* 文件修改结果默认仅保留一行摘要和操作按钮，文件清单不再遮挡对话内容。 */
#root div:has(> [class~="group/turn-diff-header"]) {
  margin-bottom: 0.25rem !important;
}
#root div:has(> [class~="group/turn-diff-header"]) > [class~="group/turn-diff-header"] > span {
  padding-top: 0.3rem !important;
  padding-bottom: 0.3rem !important;
}
#root div:has(> [class~="group/turn-diff-header"]) > :not([class~="group/turn-diff-header"]) {
  display: none !important;
}
"@
    } else { "" }
    $titlebarCss = switch ($TitlebarMode) {
        "hidden" {
@"
/* 隐藏模式只隐藏固定会话标题条，不影响侧栏或正文。 */
#root main > header.pointer-events-none.fixed.z-30 { display: none !important; }
"@
        }
        "normal" { "" }
        "topbar" {
@"
/* 顶部栏占位模式：让会话标题栏回到页面布局流，不覆盖正文内容。 */
#root main > header.pointer-events-none.fixed.z-30 {
  position: static !important;
  inset: auto !important;
  width: 100% !important;
  height: 30px !important;
  min-height: 30px !important;
  transform: none !important;
  z-index: 2 !important;
  display: flex !important;
  background: rgba(250, 252, 255, $threadHeaderAlpha) !important;
  background-image: none !important;
  backdrop-filter: blur(${Blur}px) !important;
  border: 0 !important;
  opacity: 1;
  pointer-events: auto !important;
}
"@
        }
        default {
@"
/* 会话固定标题栏：保持可操作，但不再以整块半透明面板遮挡正文和背景。 */
#root main > header.pointer-events-none.fixed.z-30 {
  top: 0 !important;
  height: 30px !important;
  background: transparent !important;
  background-image: none !important;
  backdrop-filter: none !important;
  border: 0 !important;
  opacity: 0.72;
  transition: opacity 160ms ease, background-color 160ms ease;
}
#root main > header.pointer-events-none.fixed.z-30:hover,
#root main > header.pointer-events-none.fixed.z-30:focus-within {
  background: rgba(250, 252, 255, $cardAlpha) !important;
  backdrop-filter: blur(${Blur}px) !important;
  opacity: 1;
}
"@
        }
    }
    $css = @"
:root, html, body { background: transparent !important; }
body { isolation: isolate !important; }
body::before {
  content: "";
  position: fixed;
  $projectionPlacementCss
  z-index: 1;
  pointer-events: none;
  background: $backgroundLayer;
  clip-path: $backgroundClip;
  $projectionMaskCss
}
#root {
  position: relative;
  z-index: 2;
  background: transparent !important;
}
$videoCss
/* 当前 Codex 的中间正文实际使用 MainContentSurface；只由“对话主区域透明度”控制。 */
#root main[class*="MainContentSurface"] {
  background: rgba(250, 252, 255, $panelAlpha) !important;
  background-image: none !important;
  backdrop-filter: blur(${Blur}px) !important;
}
/* 左侧导航和会话列表。 */
#root aside.app-shell-left-panel {
  background: rgba(250, 252, 255, $sidebarAlpha) !important;
  background-image: none !important;
  backdrop-filter: blur(${Blur}px) !important;
}
/* 窗口级表面、最上方菜单栏、工具栏和正文上方的会话标题栏。 */
#root main.bg-surface.no-drag,
#root [class*="ApplicationMenuTopBar"],
#root header.flex.h-toolbar {
  background: rgba(250, 252, 255, $toolbarAlpha) !important;
  background-image: none !important;
  backdrop-filter: blur(${Blur}px) !important;
}
/* 会话标题栏位于主内容表面之上，使用独立参数而非窗口顶部栏。 */
#root main[class*="MainContentSurface"] > header.flex.h-toolbar {
  background: rgba(250, 252, 255, $threadHeaderAlpha) !important;
  background-image: none !important;
  backdrop-filter: blur(${Blur}px) !important;
}
/* 结果卡、菜单和其他浮层，单独由“浮层与结果卡透明度”控制。 */
#root [class*="bg-raised"], #root [class*="bg-elevated"],
#root [class*="bg-card"], #root [class*="bg-popover"],
#root [class*="bg-surface-elevated"] {
  background: rgba(250, 252, 255, $cardAlpha) !important;
  background-image: none !important;
  backdrop-filter: blur(${Blur}px) !important;
  border-color: rgba(92, 108, 132, $borderAlpha) !important;
  border-radius: ${CornerRadius}px !important;
}
/* 兼容同一版本中使用 token 工具类的局部浮层，不覆盖主内容或左侧导航。 */
#root [class*="bg-token-main-surface"], #root [class*="bg-token-bg-primary"],
#root [class*="bg-token-bg-elevated"], #root [class*="bg-white/"] {
  background: rgba(250, 252, 255, $cardAlpha) !important;
  background-image: none !important;
  backdrop-filter: blur(${Blur}px) !important;
}
$compactDiffCss
$titlebarCss
"@
    $cssJson = $css | ConvertTo-Json -Compress
    $javascript = @"
(() => {
  document.getElementById('$styleId')?.remove();
  document.getElementById('$videoId')?.remove();
  const style = document.createElement('style');
  style.id = '$styleId';
  style.textContent = $cssJson;
  document.documentElement.appendChild(style);
  const mediaUrl = $videoUrlJson;
  if (mediaUrl) {
    const mountVideo = () => {
      document.getElementById('$videoId')?.remove();
      if (!document.body) {
        document.addEventListener('DOMContentLoaded', mountVideo, { once: true });
        return;
      }
      const video = document.createElement('video');
      video.id = '$videoId';
      video.src = mediaUrl;
      video.autoplay = true;
      video.loop = true;
      video.muted = true;
      video.playsInline = true;
      video.setAttribute('aria-hidden', 'true');
      video.setAttribute('tabindex', '-1');
      document.body.prepend(video);
      const playing = video.play();
      if (playing && typeof playing.catch === 'function') playing.catch(() => {});
    };
    mountVideo();
  }
  return '背景样式已注入';
})()
"@
}

$injected = 0
foreach ($target in $targets) {
    $current = Send-CdpCommand -WebSocketUrl $target.webSocketDebuggerUrl -Request @{
        id = 1; method = "Runtime.evaluate"; params = @{ expression = $javascript; returnByValue = $true }
    }
    if ($current.PSObject.Properties.Name -contains "error") {
        throw "Codex 页面拒绝背景注入：$($current.error.message)"
    }
    if ($current.PSObject.Properties.Name -notcontains "result") {
        throw "Codex 页面返回了未知的注入响应：$($current | ConvertTo-Json -Compress -Depth 5)"
    }
    $evaluation = $current.result.result
    if (($evaluation.PSObject.Properties.Name -contains "subtype") -and $evaluation.subtype -eq "error") {
        throw "Codex 页面执行背景样式失败：$($evaluation.description)"
    }
    # 不注册 CDP 的页面重载脚本：外观只能作用于当前已打开页面。
    # 这样导航、刷新或重启后会自然失效，避免把样式作为重载脚本长期保留。
    $injected++
}
if (-not $injected) {
    throw "找到 Codex 页面，但 CSS 注入没有成功。"
}
Write-Host "完成：已在 $injected 个 Codex 页面应用背景样式（遮罩 $BackgroundMask%，宽度 $BackgroundScale%，高度 $BackgroundHeight%，范围 $BackgroundScope）。"
Write-Host "提示：这是临时外观实验。关闭 Codex 或更新 Codex 后会失效；没有修改官方安装文件。"
