param(
    [string]$Version = (Get-Date -Format "yyyyMMdd-HHmm")
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$ExePath = Join-Path $Root "MotionMosaic.exe"
$GuidePath = Join-Path $Root "同事使用说明.md"
$PackageDir = Join-Path $Root "build\packages"
$ZipPath = Join-Path $PackageDir "MotionMosaic-$Version.zip"

if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
    throw "MotionMosaic.exe was not found. Run .\build_exe.ps1 first."
}

if (-not (Test-Path -LiteralPath $GuidePath -PathType Leaf)) {
    throw "同事使用说明.md was not found."
}

New-Item -ItemType Directory -Path $PackageDir -Force | Out-Null
Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
Compress-Archive -LiteralPath @($ExePath, $GuidePath) -DestinationPath $ZipPath

Write-Host "Packaged $ZipPath"
