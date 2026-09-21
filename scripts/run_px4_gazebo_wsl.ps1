param(
    [string]$Px4Dir = "~/PX4-Autopilot",
    [ValidateRange(1, 3)]
    [int]$Vehicles = 3,
    [switch]$Headless,
    [switch]$NoAgent
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "WSL is not installed. Install Ubuntu/WSL before starting PX4 Gazebo."
}

$distributions = & wsl.exe --list --quiet 2>$null
if ($LASTEXITCODE -ne 0 -or -not ($distributions | Where-Object { $_.Trim() })) {
    throw "No WSL distribution is installed. Install Ubuntu before starting PX4 Gazebo."
}

$scriptPath = (Resolve-Path "$PSScriptRoot\run_px4_gazebo_wsl.sh").Path
$linuxScript = (& wsl.exe wslpath -a "$scriptPath").Trim()
if ($LASTEXITCODE -ne 0 -or -not $linuxScript) {
    throw "Could not translate the launcher path into WSL."
}

$arguments = @(
    "bash",
    $linuxScript,
    "--px4-dir",
    $Px4Dir,
    "--vehicles",
    $Vehicles
)
if ($Headless) {
    $arguments += "--headless"
}
if ($NoAgent) {
    $arguments += "--no-agent"
}

& wsl.exe @arguments
exit $LASTEXITCODE
