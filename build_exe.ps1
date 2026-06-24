param(
    [switch]$SkipInstall,
    [string]$FfmpegPath = $env:MOTION_MOSAIC_FFMPEG,
    [string]$WebpmuxPath = $env:MOTION_MOSAIC_WEBPMUX,
    [switch]$NoBundleTools
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

function Resolve-ToolPath {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$ExplicitPath,
        [switch]$Required
    )

    if ($ExplicitPath) {
        if (Test-Path -LiteralPath $ExplicitPath -PathType Leaf) {
            return (Resolve-Path -LiteralPath $ExplicitPath).Path
        }
        throw "$Name was not found at $ExplicitPath"
    }

    $ProjectTool = Join-Path $Root "bin\$Name.exe"
    if (Test-Path -LiteralPath $ProjectTool -PathType Leaf) {
        return (Resolve-Path -LiteralPath $ProjectTool).Path
    }

    $Scoop = Get-Command scoop -ErrorAction SilentlyContinue
    if ($Scoop) {
        $ScoopPath = (& scoop which $Name 2>$null | Select-Object -First 1)
        if ($ScoopPath -and (Test-Path -LiteralPath $ScoopPath -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $ScoopPath).Path
        }
    }

    $Command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue
    if ($Command -and $Command.Source -and (Test-Path -LiteralPath $Command.Source -PathType Leaf)) {
        if ($Command.Source -notmatch "\\shims\\") {
            return (Resolve-Path -LiteralPath $Command.Source).Path
        }
    }

    if ($Required) {
        throw "$Name.exe was not found. Install it locally, put it in .\bin, or pass -$($Name.Substring(0,1).ToUpper())$($Name.Substring(1))Path C:\path\to\$Name.exe."
    }
    return $null
}

if (-not $SkipInstall) {
    python -m pip install -r "$Root\requirements.txt"
}

$LegacyExe = Join-Path $Root "RemaskAnnotator.exe"
$TargetExe = Join-Path $Root "MotionMosaic.exe"
Remove-Item -LiteralPath $LegacyExe -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $TargetExe -PathType Leaf) {
    try {
        Remove-Item -LiteralPath $TargetExe -Force -ErrorAction Stop
    } catch {
        throw "Cannot replace MotionMosaic.exe. Close any running Motion Mosaic window and run .\build_exe.ps1 again. $($_.Exception.Message)"
    }
}

$AddDataArgs = @(
    "--add-data", "$Root\annotator\index.html;annotator",
    "--add-data", "$Root\assets;assets"
)

if (-not $NoBundleTools) {
    $BundledToolRoot = Join-Path $Root "build\vendor-tools"
    $BundledBin = Join-Path $BundledToolRoot "bin"
    Remove-Item -LiteralPath $BundledToolRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $BundledBin -Force | Out-Null

    $ResolvedFfmpeg = Resolve-ToolPath -Name "ffmpeg" -ExplicitPath $FfmpegPath -Required
    Copy-Item -LiteralPath $ResolvedFfmpeg -Destination (Join-Path $BundledBin "ffmpeg.exe") -Force
    Write-Host "Bundled ffmpeg: $ResolvedFfmpeg"

    $ResolvedWebpmux = Resolve-ToolPath -Name "webpmux" -ExplicitPath $WebpmuxPath
    if ($ResolvedWebpmux) {
        Copy-Item -LiteralPath $ResolvedWebpmux -Destination (Join-Path $BundledBin "webpmux.exe") -Force
        Write-Host "Bundled webpmux: $ResolvedWebpmux"
    } else {
        Write-Host "webpmux was not found; animated WebP export will still work, with Pillow duration fallback."
    }

    $AddDataArgs += @("--add-data", "$BundledBin;bin")
}
$PyInstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--name", "MotionMosaic",
    "--onefile",
    "--windowed",
    "--icon", "$Root\assets\motion-mosaic.ico",
    "--distpath", "$Root",
    "--workpath", "$Root\build\pyinstaller",
    "--specpath", "$Root\build\pyinstaller"
) + $AddDataArgs + @(
    "--collect-data", "webview",
    "--collect-binaries", "webview",
    "--copy-metadata", "pywebview",
    "--exclude-module", "numpy",
    "--exclude-module", "cv2",
    "--exclude-module", "tkinter",
    "--exclude-module", "_tkinter",
    "--exclude-module", "PIL._avif",
    "--exclude-module", "PIL.AvifImagePlugin",
    "--exclude-module", "PIL.ImageTk",
    "--exclude-module", "PIL._imagingtk",
    "--exclude-module", "cryptography",
    "--exclude-module", "bcrypt",
    "--exclude-module", "flask",
    "--exclude-module", "werkzeug",
    "--exclude-module", "jinja2",
    "--exclude-module", "mako",
    "--exclude-module", "pygments",
    "--exclude-module", "cheetah",
    "--exclude-module", "paste",
    "--exclude-module", "cherrypy",
    "--exclude-module", "click",
    "--exclude-module", "itsdangerous",
    "--exclude-module", "markupsafe",
    "--exclude-module", "blinker",
    "--exclude-module", "setuptools",
    "--exclude-module", "wheel",
    "--hidden-import", "apply_thick_mosaic",
    "--hidden-import", "bottle",
    "--hidden-import", "webview",
    "--hidden-import", "webview.platforms.winforms",
    "--hidden-import", "webview.platforms.edgechromium",
    "--hidden-import", "clr_loader",
    "--hidden-import", "pythonnet",
    "--hidden-import", "proxy_tools",
    "--hidden-import", "waitress",
    "$Root\launch_motion_mosaic.py"
)

python -m PyInstaller @PyInstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

Write-Host "Built $Root\MotionMosaic.exe"
