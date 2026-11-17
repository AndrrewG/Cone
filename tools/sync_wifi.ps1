# ==============================================================================
# ARSE - Autonomous Road Systems Engineering
# Proprietary & Confidential - Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Script:      tools/sync_wifi.ps1
# Description: Wireless HTTP OTA firmware & web UI synchroniser (.mpy + assets).
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================
param(
    [string]$HostIp = "192.168.50.13",
    [string]$OnlyFile = "",
    [switch]$Reboot = $true,
    [string]$Username = "",
    [string]$PlainPassword = "",
    [string]$SessionToken = "",
    [switch]$NoMpy = $false
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$base = "http://$HostIp"

function Find-MpyCross {
    if ($NoMpy) { return $null }
    $bundled = Join-Path $PSScriptRoot "bin\mpy-cross.exe"
    if (Test-Path -LiteralPath $bundled) { return $bundled }
    $cmd = Get-Command "mpy-cross" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$mpyExe = Find-MpyCross
$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("arse_ota_mpy_" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
if ($mpyExe) {
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
    Write-Host "[ARSE-MPY] Bytecode compiler active: $mpyExe (-O2)" -ForegroundColor Magenta
}

function Build-MpyBytes([string]$srcPath, [string]$srcLabel) {
    $outMpy = Join-Path $tempDir (([Guid]::NewGuid().ToString("N")) + ".mpy")
    & $mpyExe -O2 -s $srcLabel -o $outMpy $srcPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $outMpy)) {
        throw "mpy-cross compilation failed for $srcLabel"
    }
    return [System.IO.File]::ReadAllBytes($outMpy)
}

function Send-OtaBytes([string]$remotePath, [byte[]]$rawBytes, [hashtable]$headers) {
    $chunkRawLen = 384
    for ($offset = 0; $offset -lt $rawBytes.Length; $offset += $chunkRawLen) {
        $take = [Math]::Min($chunkRawLen, $rawBytes.Length - $offset)
        $slice = New-Object byte[] $take
        [Array]::Copy($rawBytes, $offset, $slice, 0, $take)
        $b64 = [Convert]::ToBase64String($slice)
        $mode = if ($offset -eq 0) { "wb" } else { "ab" }
        $body = @{ path = $remotePath; mode = $mode; b64 = $b64 } | ConvertTo-Json -Compress
        $res = Invoke-RestMethod -Method Post -Uri "$base/api/ota/chunk" -Headers $headers -ContentType "application/json" -Body $body -DisableKeepAlive -TimeoutSec 8
        if (-not $res.ok) {
            throw "OTA write failed for ${remotePath}: $($res.error)"
        }
    }
    Write-Host "  [OK] $remotePath ($($rawBytes.Length) bytes)" -ForegroundColor Green
}

try {
    $filesToSync = @(
        @{ Local = "boot.py";                      Remote = "boot.py";                      Mpy = $false },
        @{ Local = "config.py";                    Remote = "config.py";                    Mpy = $false },
        @{ Local = "drivers\battery.py";           Remote = "drivers/battery.py";           Mpy = $true  },
        @{ Local = "drivers\wifi_manager.py";      Remote = "drivers/wifi_manager.py";      Mpy = $true  },
        @{ Local = "drivers\icm20948.py";          Remote = "drivers/icm20948.py";          Mpy = $true  },
        @{ Local = "drivers\pa1010d.py";           Remote = "drivers/pa1010d.py";           Mpy = $true  },
        @{ Local = "drivers\omni_kinematics.py";   Remote = "drivers/omni_kinematics.py";   Mpy = $true  },
        @{ Local = "drivers\lights.py";            Remote = "drivers/lights.py";            Mpy = $true  },
        @{ Local = "drivers\audio_sfx.py";         Remote = "drivers/audio_sfx.py";         Mpy = $true  },
        @{ Local = "meep.wav";                     Remote = "meep.wav";                     Mpy = $false },
        @{ Local = "drivers\drive_controller.py";  Remote = "drivers/drive_controller.py";  Mpy = $true  },
        @{ Local = "drivers\navigator.py";         Remote = "drivers/navigator.py";         Mpy = $true  },
        @{ Local = "drivers\sentry.py";            Remote = "drivers/sentry.py";            Mpy = $true  },
        @{ Local = "security\__init__.py";         Remote = "security/__init__.py";         Mpy = $true  },
        @{ Local = "security\auth_manager.py";     Remote = "security/auth_manager.py";     Mpy = $true  },
        @{ Local = "nav\__init__.py";              Remote = "nav/__init__.py";              Mpy = $true  },
        @{ Local = "nav\pattern_runner.py";        Remote = "nav/pattern_runner.py";        Mpy = $true  },
        @{ Local = "telemetry_server.py";          Remote = "telemetry_server.py";          Mpy = $true  },
        @{ Local = "web\auth.js";                  Remote = "web/auth.js";                  Mpy = $false },
        @{ Local = "web\index.html";               Remote = "web/index.html";               Mpy = $false },
        @{ Local = "main.py";                      Remote = "main.py";                      Mpy = $false; IsMain = $true }
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
        if ($item.IsMain -and $mpyExe) {
            $mpyBytes = Build-MpyBytes $fullPath "main.py"
            Write-Host "OTA Syncing main.py -> arse_app.mpy ($($mpyBytes.Length) bytes) + boot stub..." -ForegroundColor Cyan
            Send-OtaBytes "arse_app.mpy" $mpyBytes $authHeaders
            $stubBytes = [System.Text.Encoding]::UTF8.GetBytes("from arse_app import ConeApp`napp = ConeApp()`napp.start()`n")
            Send-OtaBytes "main.py" $stubBytes $authHeaders
        } elseif ($item.Mpy -and $mpyExe) {
            $remoteMpy = $item.Remote.Substring(0, $item.Remote.Length - 3) + ".mpy"
            $mpyBytes = Build-MpyBytes $fullPath $item.Remote
            Write-Host "OTA Syncing $($item.Local) -> $remoteMpy ($($mpyBytes.Length) bytes)..." -ForegroundColor Cyan
            Send-OtaBytes $remoteMpy $mpyBytes $authHeaders
        } else {
            $rawBytes = [System.IO.File]::ReadAllBytes($fullPath)
            Write-Host "OTA Syncing $($item.Local) -> $($item.Remote) ($($rawBytes.Length) bytes)..." -ForegroundColor Cyan
            Send-OtaBytes $item.Remote $rawBytes $authHeaders
        }
    }

    if ($Reboot) {
        Write-Host "`nTriggering clean wireless reboot..." -ForegroundColor Cyan
        $null = Invoke-RestMethod -Method Post -Uri "$base/api/ota/reboot" -Headers $authHeaders -ContentType "application/json" -Body "{}" -TimeoutSec 5
    }
}
finally {
    if ($tempDir -and (Test-Path -LiteralPath $tempDir)) {
        Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
