$ErrorActionPreference = "Stop"

function Add-PortlPathEntry {
    param([string]$ExistingPath, [string]$Directory)

    $TargetDirectory = $Directory.TrimEnd('\', '/')
    foreach ($Entry in ($ExistingPath -split ';')) {
        $ExpandedEntry = [Environment]::ExpandEnvironmentVariables($Entry.Trim().Trim('"')).TrimEnd('\', '/')
        if ($ExpandedEntry -ieq $TargetDirectory) {
            return $ExistingPath
        }
    }
    if ([string]::IsNullOrWhiteSpace($ExistingPath)) {
        return $Directory
    }
    return $ExistingPath.TrimEnd(';') + ';' + $Directory
}

$InstallDir = if ($env:PORTL_INSTALL_DIR) { $env:PORTL_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "Portl" }
$BinDir = if ($env:PORTL_BIN_DIR) { $env:PORTL_BIN_DIR } else { Join-Path $InstallDir "bin" }
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$BinDir = [IO.Path]::GetFullPath($BinDir)
$LauncherUrl = if ($env:PORTL_LAUNCHER_URL) { $env:PORTL_LAUNCHER_URL } else { "https://raw.githubusercontent.com/H0wl3r/portl/main/portl.py" }
$ComposeUrl = if ($env:PORTL_COMPOSE_URL) { $env:PORTL_COMPOSE_URL } else { "https://raw.githubusercontent.com/H0wl3r/portl/main/docker-compose.yml" }

Write-Host "INFO | Installing Portl launcher into $InstallDir"

$Python = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $Python) {
    $Python = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $Python) {
    throw "python is required to run the Portl launcher."
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

Invoke-WebRequest -UseBasicParsing -Uri $LauncherUrl -OutFile (Join-Path $InstallDir "portl.py")
Invoke-WebRequest -UseBasicParsing -Uri $ComposeUrl -OutFile (Join-Path $InstallDir "docker-compose.yml")

$CmdPath = Join-Path $BinDir "portl.cmd"
$LauncherPath = Join-Path $InstallDir "portl.py"
$PythonPath = $Python.Source.Replace('%', '%%')
$BatchLauncherPath = $LauncherPath.Replace('%', '%%')
$PythonArgs = if ($Python.Name -ieq 'py.exe') { ' -3' } else { '' }
Set-Content -Path $CmdPath -Encoding ASCII -Value "@echo off`r`n`"$PythonPath`"$PythonArgs `"$BatchLauncherPath`" %*`r`n"

# The quick-install command invokes portl in this same PowerShell session.
$env:Path = Add-PortlPathEntry -ExistingPath $env:Path -Directory $BinDir
$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$UpdatedUserPath = Add-PortlPathEntry -ExistingPath $UserPath -Directory $BinDir
if ($UpdatedUserPath -ne $UserPath) {
    [Environment]::SetEnvironmentVariable('Path', $UpdatedUserPath, 'User')
}

Write-Host "INFO | Portl installed."
Write-Host "INFO | Run: portl doctor"
Write-Host "INFO | Portl is available in this terminal. Restart existing terminal apps to refresh their PATH."
