# ==============================================================================
# ARSE - Autonomous Road Systems Engineering
# Proprietary & Confidential - Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Script:      tools/sync_all.ps1
# Description: Raw-REPL USB serial flash synchronizer & executive launcher.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================
param(
    [string]$Port = "COM3",
    [switch]$StartMain = $false,
    [string]$OnlyFile = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

$serial = New-Object System.IO.Ports.SerialPort $Port, 115200, None, 8, One
$serial.ReadTimeout = 4000
$serial.WriteTimeout = 4000
$serial.DtrEnable = $true
$serial.RtsEnable = $true
$serial.Open()

function Invoke-RawRepl([string]$pyCode, [int]$waitMs = 500) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($pyCode)
    $serial.Write($bytes, 0, $bytes.Length)
    $serial.Write([byte[]]@(0x04), 0, 1) # Ctrl-D execute
    $deadline = [DateTime]::UtcNow.AddMilliseconds($waitMs)
    $out = ""
    $endMarker = [string][char]4 + ">"
    while ([DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 30
        $chunk = $serial.ReadExisting()
        if ($chunk) {
            $out += $chunk
            if ($out.Contains($endMarker)) { break }
        }
    }
    return $out
}

try {
    # Interrupt any running script (allow clean KeyboardInterrupt finally block) and enter Raw REPL (Ctrl-A)
    $serial.Write([byte[]]@(0x03, 0x03), 0, 2)
    Start-Sleep -Milliseconds 600
    $null = $serial.ReadExisting()
    $serial.Write([byte[]]@(0x01), 0, 1)
    Start-Sleep -Milliseconds 250
    $resp = $serial.ReadExisting()
    if ($resp -notmatch "raw REPL") {
        $serial.Write([byte[]]@(0x03, 0x01), 0, 2)
        Start-Sleep -Milliseconds 300
        $null = $serial.ReadExisting()
    }

    # Free any previous main.py allocations before syncing files
    if (-not $OnlyFile) {
        $cleanupCode = "import os, sys, gc`ntry:`n os.remove('main.py')`nexcept OSError:`n pass`ngc.collect()`n"
        $null = Invoke-RawRepl $cleanupCode 600
    }

    # Clear cached user modules & run GC WITHOUT Ctrl-D VM soft-reset
    # (on Yukon Wireless, RP2040 VM soft-reset calls mp_machine_i2s_deinit(),
    # which conflicts with CYW43 PIO SPI DMA if I2S was active)
    $mkdirCode = "import os, sys, ubinascii, gc`nfor k in list(sys.modules.keys()):`n if k.startswith('drivers') or k.startswith('security') or k in ('config', 'telemetry_server', 'main'):`n  del sys.modules[k]`ngc.collect()`nfor d in ('drivers', 'web', 'security'):`n try:`n  os.mkdir(d)`n except OSError:`n  pass`ngc.collect()`n"
    $null = Invoke-RawRepl $mkdirCode 800

    $filesToSync = @(
        @{ Local = "boot.py";                      Remote = "boot.py" },
        @{ Local = "config.py";                    Remote = "config.py" },
        @{ Local = "security\__init__.py";         Remote = "security/__init__.py" },
        @{ Local = "security\auth_manager.py";     Remote = "security/auth_manager.py" },
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
        @{ Local = "telemetry_server.py";          Remote = "telemetry_server.py" },
        @{ Local = "web\index.html";               Remote = "web/index.html" },
        @{ Local = "hardware_test.py";             Remote = "hardware_test.py" },
        @{ Local = "motor_test.py";                Remote = "motor_test.py" },
        @{ Local = "main.py";                      Remote = "main.py" }
    )

    $localSecretsPath = Join-Path $projectRoot "secrets.json"
    if ((Test-Path -LiteralPath $localSecretsPath) -and (-not $OnlyFile -or $OnlyFile -eq "secrets.json")) {
        $secBytes = [System.IO.File]::ReadAllBytes($localSecretsPath)
        $secB64 = [Convert]::ToBase64String($secBytes)
        Write-Host "Merging local secrets.json onto Yukon (preserving board Wi-Fi keys)..." -ForegroundColor Cyan
        $mergeCode = @"
import ujson, ubinascii
cur = {}
try:
 f = open('secrets.json', 'rb')
 d = ujson.loads(f.read().decode('utf-8'))
 f.close()
 if isinstance(d, dict): cur.update(d)
except Exception:
 pass
inc = ujson.loads(ubinascii.a2b_base64('$secB64').decode('utf-8'))
cur.update(inc)
f = open('secrets.json', 'wb')
f.write((ujson.dumps(cur) + '\n').encode('utf-8'))
f.close()
print('SECRETS_MERGED', len(cur))
"@
        $mRes = Invoke-RawRepl $mergeCode 800
        if ($mRes -match "SECRETS_MERGED") {
            Write-Host "  [OK] secrets.json merged cleanly" -ForegroundColor Green
        }
    }

    if ($OnlyFile) {
        $filesToSync = $filesToSync | Where-Object { $_.Local -eq $OnlyFile -or $_.Remote -eq $OnlyFile }
    }

    foreach ($item in $filesToSync) {
        $fullPath = Join-Path $projectRoot $item.Local
        $rawBytes = [System.IO.File]::ReadAllBytes($fullPath)
        $b64 = [Convert]::ToBase64String($rawBytes)
        Write-Host "Syncing $($item.Local) -> $($item.Remote) ($($rawBytes.Length) bytes)..." -ForegroundColor Cyan

        $null = Invoke-RawRepl "f = open('$($item.Remote)', 'wb')" 500
        $chunkSize = 512
        for ($i = 0; $i -lt $b64.Length; $i += $chunkSize) {
            $len = [Math]::Min($chunkSize, $b64.Length - $i)
            $chunk = $b64.Substring($i, $len)
            $null = Invoke-RawRepl "f.write(ubinascii.a2b_base64('$chunk'))" 500
        }
        $res = Invoke-RawRepl "f.close(); gc.collect(); print('SYNC_OK', os.stat('$($item.Remote)')[6])" 600
        if ($res -match "SYNC_OK\s+(\d+)") {
            Write-Host "  [OK] $($item.Remote) ($($Matches[1]) bytes on flash)" -ForegroundColor Green
        } else {
            Write-Host "  [WARN] Unexpected response for $($item.Remote): $res" -ForegroundColor Yellow
        }
    }

    Write-Host "`nAll Cone firmware, drivers & web UI synced to Yukon!" -ForegroundColor Green

    if ($StartMain) {
        Write-Host "`nTriggering clean RP2040 hardware reset into main.py..." -ForegroundColor Cyan
        $rstBytes = [System.Text.Encoding]::UTF8.GetBytes("import machine; machine.reset()")
        $serial.Write($rstBytes, 0, $rstBytes.Length)
        $serial.Write([byte[]]@(0x04), 0, 1)
        Start-Sleep -Milliseconds 50
    }
}
finally {
    if (-not $StartMain) {
        try { $serial.Write([byte[]]@(0x02), 0, 1); Start-Sleep -Milliseconds 100 } catch {}
    }
    try { $serial.Close() } catch {}
}
