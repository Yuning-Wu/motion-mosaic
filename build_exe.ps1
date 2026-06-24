param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

if (-not $SkipInstall) {
    python -m pip install -r "$Root\requirements.txt"
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --name RemaskAnnotator `
    --onefile `
    --windowed `
    --distpath "$Root" `
    --workpath "$Root\build\pyinstaller" `
    --specpath "$Root\build\pyinstaller" `
    --add-data "$Root\annotator\index.html;annotator" `
    --exclude-module numpy `
    --exclude-module cv2 `
    --exclude-module tkinter `
    --exclude-module _tkinter `
    --exclude-module PIL._avif `
    --exclude-module PIL.AvifImagePlugin `
    --exclude-module PIL.ImageTk `
    --exclude-module PIL._imagingtk `
    --exclude-module ssl `
    --exclude-module _ssl `
    --exclude-module _hashlib `
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
    --hidden-import waitress `
    "$Root\launch_remask_annotator.py"

Write-Host "Built $Root\RemaskAnnotator.exe"
