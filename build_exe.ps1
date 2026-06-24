param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

if (-not $SkipInstall) {
    python -m pip install -r "$Root\requirements.txt"
}

Remove-Item -LiteralPath "$Root\RemaskAnnotator.exe" -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "$Root\MotionMosaic.exe" -Force -ErrorAction SilentlyContinue

python -m PyInstaller `
    --noconfirm `
    --clean `
    --name MotionMosaic `
    --onefile `
    --windowed `
    --icon "$Root\assets\motion-mosaic.ico" `
    --distpath "$Root" `
    --workpath "$Root\build\pyinstaller" `
    --specpath "$Root\build\pyinstaller" `
    --add-data "$Root\annotator\index.html;annotator" `
    --add-data "$Root\assets;assets" `
    --collect-data webview `
    --collect-binaries webview `
    --copy-metadata pywebview `
    --exclude-module numpy `
    --exclude-module cv2 `
    --exclude-module tkinter `
    --exclude-module _tkinter `
    --exclude-module PIL._avif `
    --exclude-module PIL.AvifImagePlugin `
    --exclude-module PIL.ImageTk `
    --exclude-module PIL._imagingtk `
    --exclude-module cryptography `
    --exclude-module bcrypt `
    --exclude-module flask `
    --exclude-module werkzeug `
    --exclude-module jinja2 `
    --exclude-module mako `
    --exclude-module pygments `
    --exclude-module cheetah `
    --exclude-module paste `
    --exclude-module cherrypy `
    --exclude-module click `
    --exclude-module itsdangerous `
    --exclude-module markupsafe `
    --exclude-module blinker `
    --exclude-module setuptools `
    --exclude-module wheel `
    --hidden-import apply_thick_mosaic `
    --hidden-import bottle `
    --hidden-import webview `
    --hidden-import webview.platforms.winforms `
    --hidden-import webview.platforms.edgechromium `
    --hidden-import clr_loader `
    --hidden-import pythonnet `
    --hidden-import proxy_tools `
    --hidden-import waitress `
    "$Root\launch_motion_mosaic.py"

Write-Host "Built $Root\MotionMosaic.exe"
