<#
.SYNOPSIS
  Back up pinned release artifacts (model files, eval manifests, release.yaml)
  to a destination directory and write a hash manifest. Never deletes anything.

.DESCRIPTION
  Backup mode (default): resolves the artifact list from configs/release.yaml
  (model_versions[].path with sha256 pins, evaluation_manifests[].manifest
  with manifest_sha256 pins) plus the release config itself, copies each file
  to the destination preserving relative paths, and writes hashes.json with
  SHA-256 hashes, sizes, timestamp and the release config hash.

  Verify mode (-Verify): recomputes hashes of a previous backup, compares them
  to hashes.json, and compares manifest hashes against the release.yaml pins
  where available. Exit 0 = all match, 1 = any mismatch/missing.

  -Files overrides the resolved list (relative paths under -SourceRoot);
  used by tests with temporary files. Pinned-hash comparison is skipped for
  -Files entries that have no pin.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/backup_release_artifacts.ps1 -Destination D:\tb-backup\2026-10-15
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/backup_release_artifacts.ps1 -Destination D:\tb-backup\2026-10-15 -Verify
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [string]$ReleaseConfig = "configs/release.yaml",
    [string]$SourceRoot = "",
    [string[]]$Files,
    [switch]$Verify
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

if (-not $SourceRoot) {
    if ($PSCommandPath) { $SourceRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath) }
    else { $SourceRoot = (Get-Location).Path }
}

function Get-PinnedEntries {
    param([string]$ConfigPath)
    # Minimal purpose-built parser for configs/release.yaml: collects
    # model_versions[].path (+ sha256) and evaluation_manifests[].manifest
    # (+ manifest_sha256). Returns @( @{ rel=...; pin=... } ).
    $entries = @()
    $section = ""
    $pendingRel = $null
    $lines = Get-Content -Path $ConfigPath -Encoding UTF8
    foreach ($raw in $lines) {
        if ($raw -match '^(?<sec>\S[^:]*):\s*(#.*)?$') {
            $section = $Matches["sec"]
            $pendingRel = $null
            continue
        }
        $val = $null
        $key = $null
        if ($raw -match '^\s+(?<k>path|manifest|sha256|manifest_sha256):\s*(?<v>\S+)\s*(#.*)?$') {
            $key = $Matches["k"]
            $val = $Matches["v"].Trim('"', "'")
        }
        if (($section -eq "model_versions" -and $key -eq "path") -or
            ($section -eq "evaluation_manifests" -and $key -eq "manifest")) {
            if ($pendingRel -ne $null) { $entries += @{ rel = $pendingRel; pin = $null } }
            $pendingRel = $val
        }
        elseif ((($section -eq "model_versions" -and $key -eq "sha256") -or
                 ($section -eq "evaluation_manifests" -and $key -eq "manifest_sha256")) -and
                $pendingRel -ne $null -and $val -match '^[0-9a-fA-F]{64}$') {
            $entries += @{ rel = $pendingRel; pin = $val.ToLower() }
            $pendingRel = $null
        }
    }
    if ($pendingRel -ne $null) { $entries += @{ rel = $pendingRel; pin = $null } }
    return $entries
}

function Get-Sha256Hex {
    param([string]$Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            $bytes = $sha.ComputeHash($stream)
        } finally {
            $stream.Close()
        }
    } finally {
        $sha.Dispose()
    }
    return ([System.BitConverter]::ToString($bytes)).Replace("-", "").ToLower()
}

$stamp = [DateTime]::UtcNow.ToString("o")

if ($Verify) {
    $manifestPath = Join-Path $Destination "hashes.json"
    if (-not (Test-Path $manifestPath)) { Write-Output "VERIFY FAIL: no hashes.json in $Destination"; exit 1 }
    $manifest = Get-Content -Path $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $fail = 0
    foreach ($f in $manifest.files) {
        $p = Join-Path $Destination $f.rel
        if (-not (Test-Path $p)) { Write-Output "MISSING: $($f.rel)"; $fail++; continue }
        $actual = Get-Sha256Hex -Path $p
        if ($actual -ne $f.sha256.ToLower()) { Write-Output "HASH MISMATCH: $($f.rel)"; $fail++ }
        else { Write-Output "OK: $($f.rel)" }
    }
    if ($fail -gt 0) { Write-Output "VERIFY FAIL: $fail problem(s)"; exit 1 }
    Write-Output "VERIFY PASS: $($manifest.files.Count) files match hashes.json"
    exit 0
}

# --- backup mode ---
$entries = @()
if ($Files -and $Files.Count -gt 0) {
    $flat = @()
    foreach ($r in $Files) { foreach ($part in ($r -split ",")) { if ($part.Trim() -ne "") { $flat += $part.Trim() } } }
    foreach ($r in $flat) { $entries += @{ rel = $r; pin = $null } }
} else {
    $cfgPath = $ReleaseConfig
    if (-not [System.IO.Path]::IsPathRooted($cfgPath)) { $cfgPath = Join-Path $SourceRoot $cfgPath }
    $entries = @(Get-PinnedEntries -ConfigPath $cfgPath)
    if ([System.IO.Path]::IsPathRooted($ReleaseConfig)) {
        $cfgRel = Split-Path -Leaf $ReleaseConfig  # test override: store basename at root
    } else {
        $cfgRel = $ReleaseConfig.Replace("\", "/")
    }
}
$missing = @()
foreach ($e in $entries) {
    $src = Join-Path $SourceRoot ($e.rel -replace "/", [System.IO.Path]::DirectorySeparatorChar)
    if (-not (Test-Path $src)) { $missing += $e.rel }
}
if ($missing.Count -gt 0) {
    Write-Output "BACKUP ABORTED: missing source files:"
    foreach ($m in $missing) { Write-Output "  MISSING: $m" }
    exit 1
}
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
$rows = @()
foreach ($e in $entries) {
    $src = Join-Path $SourceRoot ($e.rel -replace "/", [System.IO.Path]::DirectorySeparatorChar)
    $dst = Join-Path $Destination ($e.rel -replace "/", [System.IO.Path]::DirectorySeparatorChar)
    $dir = Split-Path -Parent $dst
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Copy-Item -Path $src -Destination $dst -Force
    $h = Get-Sha256Hex -Path $dst
    if ($e.pin -and ($h -ne $e.pin)) {
        Write-Output "PIN MISMATCH (source does not match release.yaml pin): $($e.rel)"
        exit 1
    }
    $row = New-Object PSObject -Property @{ rel = [string]$e.rel; sha256 = [string]$h;
                                             bytes = [long](Get-Item $dst).Length; pin = $e.pin }
    $rows += $row
    Write-Output "OK: $($e.rel)"
}
if (-not ($Files -and $Files.Count -gt 0)) {
    # The release config itself travels with the backup; it resolves against
    # its own location (repo-relative in production, absolute in tests).
    $cfgAbs = $ReleaseConfig
    if (-not [System.IO.Path]::IsPathRooted($cfgAbs)) { $cfgAbs = Join-Path $SourceRoot $cfgAbs }
    if ([System.IO.Path]::IsPathRooted($ReleaseConfig)) { $cfgRel = Split-Path -Leaf $ReleaseConfig }
    else { $cfgRel = $ReleaseConfig.Replace("\", "/") }
    if (-not (Test-Path $cfgAbs)) {
        Write-Output "BACKUP ABORTED: release config not found: $cfgAbs"
        exit 1
    }
    $cfgDst = Join-Path $Destination ($cfgRel -replace "/", [System.IO.Path]::DirectorySeparatorChar)
    $cfgDir = Split-Path -Parent $cfgDst
    if ($cfgDir -and -not (Test-Path $cfgDir)) { New-Item -ItemType Directory -Path $cfgDir -Force | Out-Null }
    Copy-Item -Path $cfgAbs -Destination $cfgDst -Force
    $cfgHash = Get-Sha256Hex -Path $cfgDst
    $rows += New-Object PSObject -Property @{ rel = [string]$cfgRel; sha256 = [string]$cfgHash;
                                              bytes = [long](Get-Item $cfgDst).Length; pin = $null }
    Write-Output "OK: $cfgRel"
    $cfgLabel = $cfgRel
} else {
    $cfgHash = "n/a (explicit -Files list)"
    $cfgLabel = $ReleaseConfig.Replace("\", "/")
}
$manifest = @{
    generated_utc        = $stamp
    source_root          = $SourceRoot
    release_config       = $cfgLabel
    release_config_sha256 = $cfgHash
    files                = $rows
}
$manifestJson = ($manifest | ConvertTo-Json -Depth 5)
# BOM-less UTF-8: Set-Content -Encoding UTF8 writes a BOM on Windows PowerShell
# 5.1, which strict JSON parsers reject.
[System.IO.File]::WriteAllText((Join-Path $Destination "hashes.json"), $manifestJson,
                               [System.Text.UTF8Encoding]::new($false))
Write-Output "BACKUP COMPLETE: $($rows.Count) files -> $Destination"
exit 0
