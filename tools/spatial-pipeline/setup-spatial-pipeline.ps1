# One-time setup for the spatial pipeline.
#   powershell -ExecutionPolicy Bypass -File setup-spatial-pipeline.ps1
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path "$here\venv")) {
    py -3 -m venv "$here\venv"
}
& "$here\venv\Scripts\python.exe" -m pip install --upgrade pip
& "$here\venv\Scripts\python.exe" -m pip install -r "$here\requirements.txt"
# SplatTransform CLI (splat SH-aware transforms, PLY<->SOG/SPZ) — used from P3 on.
npm install --prefix "$here" @playcanvas/splat-transform
Write-Output "spatial-pipeline ready. Run scripts with: $here\venv\Scripts\python.exe <script>"
