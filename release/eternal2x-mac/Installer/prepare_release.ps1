param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [switch]$SkipVersionFile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-RepoSlug {
    $remote = (git config --get remote.origin.url).Trim()
    if (-not $remote) {
        throw "Could not read remote.origin.url. Set git remote first."
    }

    if ($remote -match "github\.com[:/](.+?)/(.+?)(\.git)?$") {
        return "$($Matches[1])/$($Matches[2])"
    }

    throw "Unsupported remote URL format: $remote"
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [object]$Data
    )
    $json = $Data | ConvertTo-Json -Depth 10
    # ConvertTo-Json uses 2-space indentation by default.
    [System.IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
}

if ($Version -notmatch "^\d+\.\d+\.\d+$") {
    throw "Version must be SemVer like 0.1.2 (no leading 'v')."
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$releaseRoot = Join-Path $repoRoot "release"
$winPayload = Join-Path $releaseRoot "eternal2x-win"
$macPayload = Join-Path $releaseRoot "eternal2x-mac"
$winZip = Join-Path $repoRoot "eternal2x-win.zip"
$macZip = Join-Path $repoRoot "eternal2x-mac.zip"
$latestJson = Join-Path $repoRoot "update/latest.json"
$versionFile = Join-Path $repoRoot "VERSION"

Write-Host "Preparing release $Version in $repoRoot"

if (Test-Path $releaseRoot) {
    Remove-Item -Recurse -Force $releaseRoot
}
if (Test-Path $winZip) {
    Remove-Item -Force $winZip
}
if (Test-Path $macZip) {
    Remove-Item -Force $macZip
}

New-Item -ItemType Directory -Force -Path $winPayload | Out-Null
New-Item -ItemType Directory -Force -Path $macPayload | Out-Null

$payloadItems = @("Installer", "Pipeline", "Stages", "README.md", "VERSION", "requirements.txt")
foreach ($item in $payloadItems) {
    $src = Join-Path $repoRoot $item
    if (-not (Test-Path $src)) {
        throw "Missing required payload item: $item"
    }
    Copy-Item $src -Destination $winPayload -Recurse -Force
    Copy-Item $src -Destination $macPayload -Recurse -Force
}

if (-not $SkipVersionFile) {
    [System.IO.File]::WriteAllText($versionFile, $Version + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
    Copy-Item $versionFile -Destination (Join-Path $winPayload "VERSION") -Force
    Copy-Item $versionFile -Destination (Join-Path $macPayload "VERSION") -Force
}

$winInstaller = Join-Path (Join-Path $repoRoot "dist") "Eternal2xInstaller.exe"
if (Test-Path $winInstaller) {
    Copy-Item $winInstaller -Destination (Join-Path $winPayload "Eternal2xInstaller.exe") -Force
    Write-Host "Included Windows installer exe."
} else {
    Write-Host "WARNING: dist/Eternal2xInstaller.exe not found. Build it first with:"
    Write-Host "  pip install pyinstaller"
    Write-Host "  python Installer/build_installer.py"
}

Compress-Archive -Path (Join-Path $winPayload "*") -DestinationPath $winZip -Force
Compress-Archive -Path (Join-Path $macPayload "*") -DestinationPath $macZip -Force

$winSha = ((Get-FileHash $winZip -Algorithm SHA256).Hash).ToLowerInvariant()
$macSha = ((Get-FileHash $macZip -Algorithm SHA256).Hash).ToLowerInvariant()

$slug = Get-RepoSlug
$baseUrl = "https://github.com/$slug/releases/download/v$Version"

$latest = [ordered]@{
    version = $Version
    windows = [ordered]@{
        url = "$baseUrl/eternal2x-win.zip"
        sha256 = $winSha
    }
    macos = [ordered]@{
        url = "$baseUrl/eternal2x-mac.zip"
        sha256 = $macSha
    }
}
Write-JsonFile -Path $latestJson -Data $latest

Write-Host ""
Write-Host "Done."
Write-Host "Built: $winZip"
Write-Host "Built: $macZip"
Write-Host "Updated: $latestJson"
if (-not $SkipVersionFile) {
    Write-Host "Updated: $versionFile"
}
Write-Host ""
Write-Host "Next:"
Write-Host "1) git add VERSION update/latest.json"
Write-Host "2) git commit -m ""Prepare release v$Version"""
Write-Host "3) git push origin main"
Write-Host "4) Create GitHub release tag v$Version and upload both zip files as release assets:"
Write-Host "     $winZip"
Write-Host "     $macZip"
Write-Host ""
Write-Host "Do NOT commit the .zip files to git -- upload them as GitHub Release assets only."
