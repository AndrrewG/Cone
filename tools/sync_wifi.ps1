# ==============================================================================
# ARSE - Autonomous Road Systems Engineering
# Proprietary & Confidential - Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Script:      tools/sync_wifi.ps1
# Description: Wireless HTTP OTA firmware & web UI synchroniser (no USB needed).
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================
param(
    [string]$HostIp = "192.168.50.13",
    [string]$OnlyFile = "",
    [switch]$Reboot = $true,
    [string]$Username = "",
    [string]$PlainPassword = "",
    [string]$SessionToken = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$base = "http://$HostIp"

$filesToSync = @(
    @{ Local = "boot.py";                      Remote = "boot.py" },
    @{ Local = "config.py";                    Remote = "config.py" },
    @{ Local = "drivers\battery.py";           Remote = "drivers/battery.py" },
    @{ Local = "drivers\wifi_manager.py";      Remote = "drivers/wifi_manager.py" },
    @{ Local = "drivers\icm20948.py";          Remote = "drivers/icm20948.py" },
    @{ Local = "drivers\pa1010d.py";           Remote = "drivers/pa1010d.py" },
    @{ Local = "drivers\omni_kinematics.py";   Remote = "drivers/omni_kinematics.py" },
    @{ Local = "drivers\lights.py";            Remote = "drivers/lights.py" },
    @{ Local = "drivers\audio_sfx.py";         Remote = "drivers/audio_sfx.py" },
    @{ Local = "meep.wav";                     Remote = "meep.wav" },
    @{ Local = "drivers\drive_controller.py";  Remote = "drivers/drive_controller.py" },
    @{ Local = "drivers\navigator.py";         Remote = "drivers/navigator.py" },
    @{ Local = "drivers\sentry.py";            Remote = "drivers/sentry.py" },
    @{ Local = "security\__init__.py";         Remote = "security/__init__.py" },
    @{ Local = "security\auth_manager.py";     Remote = "security/auth_manager.py" },
    @{ Local = "telemetry_server.py";          Remote = "telemetry_server.py" },
    @{ Local = "web\index.html";               Remote = "web/index.html" },
    @{ Local = "main.py";                      Remote = "main.py" }
)

if ($OnlyFile) {
    $filesToSync = $filesToSync | Where-Object { $_.Local -eq $OnlyFile -or $_.Remote -eq $OnlyFile }
}

$authHeaders = @{}
$requiresAuth = $false

try {
    $health = Invoke-RestMethod -Method Get -Uri "$base/api/health" -DisableKeepAlive -TimeoutSec 5
    if ($null -ne $health -and $null -ne $health.online) {
        $requiresAuth = $true
    }
} catch {
    $statusCode = $null
    if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
        $statusCode = [int]$_.Exception.Response.StatusCode
    }
    if ($statusCode -eq 404) {
        Write-Host "[ARSE-AUTH] Legacy firmware detected (/api/health -> 404); syncing without Bearer header." -ForegroundColor Yellow
        $requiresAuth = $false
    } else {
        throw
    }
}

if ($requiresAuth -or $SessionToken) {
    $token = $SessionToken.Trim()
    if ($token -match "^(?i)bearer\s+(.+)$") {
        $token = $Matches[1].Trim()
    }

    if (-not $token) {
        $loginUser = $Username.Trim()
        if (-not $loginUser) {
            $promptUser = Read-Host "Cone Operator Username [admin]"
            $loginUser = if ($promptUser -and $promptUser.Trim()) { $promptUser.Trim() } else { "admin" }
        }

        $loginPass = $PlainPassword
        if (-not $loginPass) {
            $secPass = Read-Host "Cone Operator Password" -AsSecureString
            $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secPass)
            try {
                $loginPass = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
            } finally {
                [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
            }
        }

        $loginBody = @{ username = $loginUser; password = $loginPass } | ConvertTo-Json -Compress
        $loginRes = Invoke-RestMethod -Method Post -Uri "$base/api/auth/login" -ContentType "application/json" -Body $loginBody -DisableKeepAlive -TimeoutSec 8
        if (-not $loginRes.authenticated -or -not $loginRes.session_token) {
            throw "Operator login failed on $base/api/auth/login"
        }
        $token = [string]$loginRes.session_token
        Write-Host "[ARSE-AUTH] Authenticated as '$($loginRes.username)' (session TTL: $($loginRes.expires_in_s) s)" -ForegroundColor Green
    }

    $authHeaders = @{ Authorization = "Bearer $token" }
}

foreach ($item in $filesToSync) {
    $fullPath = Join-Path $projectRoot $item.Local
    $rawBytes = [System.IO.File]::ReadAllBytes($fullPath)
    Write-Host "OTA Syncing $($item.Local) -> $($item.Remote) ($($rawBytes.Length) bytes)..." -ForegroundColor Cyan

    $chunkRawLen = 384
    for ($offset = 0; $offset -lt $rawBytes.Length; $offset += $chunkRawLen) {
        $take = [Math]::Min($chunkRawLen, $rawBytes.Length - $offset)
        $slice = New-Object byte[] $take
        [Array]::Copy($rawBytes, $offset, $slice, 0, $take)
        $b64 = [Convert]::ToBase64String($slice)
        $mode = if ($offset -eq 0) { "wb" } else { "ab" }
        $body = @{ path = $item.Remote; mode = $mode; b64 = $b64 } | ConvertTo-Json -Compress
        $res = Invoke-RestMethod -Method Post -Uri "$base/api/ota/chunk" -Headers $authHeaders -ContentType "application/json" -Body $body -DisableKeepAlive -TimeoutSec 8
        if (-not $res.ok) {
            throw "OTA write failed for $($item.Remote): $($res.error)"
        }
    }
    Write-Host "  [OK] $($item.Remote)" -ForegroundColor Green
}

if ($Reboot) {
    Write-Host "`nTriggering clean wireless reboot..." -ForegroundColor Cyan
    $null = Invoke-RestMethod -Method Post -Uri "$base/api/ota/reboot" -Headers $authHeaders -ContentType "application/json" -Body "{}" -TimeoutSec 5
}
