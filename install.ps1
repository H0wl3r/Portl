$ErrorActionPreference = "Stop"

$InstallDir = if ($env:PORTL_INSTALL_DIR) { $env:PORTL_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "Portl" }
$BinDir = if ($env:PORTL_BIN_DIR) { $env:PORTL_BIN_DIR } else { Join-Path $InstallDir "bin" }
$LauncherUrl = if ($env:PORTL_LAUNCHER_URL) { $env:PORTL_LAUNCHER_URL } else { "https://raw.githubusercontent.com/H0wl3r/portl/main/portl.py" }
$ComposeUrl = if ($env:PORTL_COMPOSE_URL) { $env:PORTL_COMPOSE_URL } else { "https://raw.githubusercontent.com/H0wl3r/portl/main/docker-compose.yml" }

Write-Host "INFO | Installing Portl launcher into $InstallDir"

$Python = Get-Command py -ErrorAction SilentlyContinue
if (-not $Python) {
    $Python = Get-Command python -ErrorAction SilentlyContinue
}
if (-not $Python) {
    throw "python is required to run the Portl launcher."
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

Invoke-WebRequest -Uri $LauncherUrl -OutFile (Join-Path $InstallDir "portl.py")
Invoke-WebRequest -Uri $ComposeUrl -OutFile (Join-Path $InstallDir "docker-compose.yml")

$CmdPath = Join-Path $BinDir "portl.cmd"
$LauncherPath = Join-Path $InstallDir "portl.py"
Set-Content -Path $CmdPath -Encoding ASCII -Value "@echo off`r`npython `"$LauncherPath`" %*`r`n"

Write-Host "INFO | Portl installed."
Write-Host "INFO | Run: portl doctor"
Write-Host "INFO | If portl is not found, add $BinDir to PATH."
