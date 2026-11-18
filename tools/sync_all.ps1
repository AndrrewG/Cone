# ==============================================================================
# ARSE - Autonomous Road Systems Engineering
# Proprietary & Confidential - Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Script:      tools/sync_all.ps1
# Description: Raw-REPL USB serial flash synchroniser (.mpy bytecode + assets).
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================
param(
    [string]$Port = "COM3",
    [switch]$StartMain = $false,
    [string]$OnlyFile = "",
    [switch]$NoMpy = $false
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

function Find-MpyCross {
    if ($NoMpy) { return $null }
    $bundled = Join-Path $PSScriptRoot "bin\mpy-cross.exe"
    if (Test-Path -LiteralPath $bundled) { return $bundled }
    $cmd = Get-Command "mpy-cross" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$mpyExe = Find-MpyCross
$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("arse_mpy_" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
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

$serial = New-Object System.IO.Ports.SerialPort $Port, 115200, None, 8, One
$serial.ReadTimeout = 4000
$serial.WriteTimeout = 4000
$serial.DtrEnable = $true
$serial.RtsEnable = $true
$serial.Open()

function Invoke-RawRepl([string]$pyCode, [int]$waitMs = 500) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($pyCode)
    $serial.Write($bytes, 0, $bytes.Length)
    $serial.Write([byte[]]@(0x04), 0, 1) # Ctrl-D execute inside Raw REPL
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

function Send-RemoteBytes([string]$remotePath, [byte[]]$rawBytes, [string]$staleAlt = "") {
    if ($staleAlt) {
        $null = Invoke-RawRepl "try:`n os.remove('$staleAlt')`nexcept OSError:`n pass`n" 350
    }
    $b64 = [Convert]::ToBase64String($rawBytes)
    $openRes = Invoke-RawRepl "gc.collect(); f = open('$remotePath', 'wb'); print('OPEN_OK')" 500
    if ($openRes -notmatch "OPEN_OK") {
        throw "Failed to open $remotePath on Yukon flash: $openRes"
    }
    $chunkSize = 512
    for ($i = 0; $i -lt $b64.Length; $i += $chunkSize) {
        $len = [Math]::Min($chunkSize, $b64.Length - $i)
        $chunk = $b64.Substring($i, $len)
        $null = Invoke-RawRepl "f.write(ubinascii.a2b_base64('$chunk'))" 500
    }
    $res = Invoke-RawRepl "f.close(); del f; gc.collect(); print('SYNC_OK', os.stat('$remotePath')[6])" 600
    if ($res -match "SYNC_OK\s+(\d+)") {
        Write-Host "  [OK] $remotePath ($($Matches[1]) bytes on flash)" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] Unexpected response for ${remotePath}: $res" -ForegroundColor Yellow
    }
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

    # Disarm any active I2S Python IRQ, close open WAV handle, reset asyncio loop & free user globals
    $null = Invoke-RawRepl "try:`n app.audio._shutdown_hardware()`nexcept Exception:`n pass`ntry:`n import asyncio; asyncio.new_event_loop()`nexcept Exception:`n pass`nfor _k in list(globals().keys()):`n if _k != '__builtins__' and not _k.startswith('__'):`n  globals().pop(_k, None)`nimport sys, gc`nsys.modules.clear()`ngc.collect()`n" 600

    if (-not $OnlyFile) {
        $cleanupCode = "import os, gc`nfor _f in ('main.py', 'arse_app.mpy'):`n try:`n  os.remove(_f)`n except OSError:`n  pass`ngc.collect()`nprint('FREE_BYTES', gc.mem_free())`n"
        $freeRes = Invoke-RawRepl $cleanupCode 600
        if ($freeRes -match "FREE_BYTES\s+(\d+)" -and ([int]$Matches[1] -lt 32768)) {
            Write-Host "  [HEAP] Defragmenting RP2040 heap via clean reset ($($Matches[1]) B free)..." -ForegroundColor Yellow
            $rstB = [System.Text.Encoding]::UTF8.GetBytes("import machine; machine.reset()")
            $serial.Write($rstB, 0, $rstB.Length)
            $serial.Write([byte[]]@(0x04), 0, 1)
            Start-Sleep -Milliseconds 100
            try { $serial.Close() } catch {}
            Start-Sleep -Milliseconds 2400
            $serial.Open()
            $serial.Write([byte[]]@(0x03, 0x01), 0, 2)
            Start-Sleep -Milliseconds 350
            $null = $serial.ReadExisting()
        }
    }

    # Clear cached user modules & run GC WITHOUT VM soft-reset
    $mkdirCode = "import os, sys, ubinascii, gc`nfor k in ('main','arse_app','telemetry_server','config','security.auth_manager','security','nav.pattern_runner','nav','drivers.navigator','drivers.sentry','drivers.icm20948','drivers.battery','drivers.pa1010d','drivers.drive_controller','drivers.omni_kinematics','drivers.lights','drivers.audio_sfx','drivers.wifi_manager'):`n sys.modules.pop(k, None)`ngc.collect()`nfor d in ('drivers', 'web', 'security', 'nav'):`n try:`n  os.mkdir(d)`n except OSError:`n  pass`ngc.collect()`n"
    $null = Invoke-RawRepl $mkdirCode 800

    $filesToSync = @(
        @{ Local = "boot.py";                      Remote = "boot.py";                      Mpy = $false },
        @{ Local = "config.py";                    Remote = "config.py";                    Mpy = $false },
        @{ Local = "security\__init__.py";         Remote = "security/__init__.py";         Mpy = $true  },
        @{ Local = "security\auth_manager.py";     Remote = "security/auth_manager.py";     Mpy = $true  },
        @{ Local = "nav\__init__.py";              Remote = "nav/__init__.py";              Mpy = $true  },
        @{ Local = "nav\pattern_runner.py";        Remote = "nav/pattern_runner.py";        Mpy = $true  },
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
        @{ Local = "telemetry_server.py";          Remote = "telemetry_server.py";          Mpy = $true  },
        @{ Local = "web\auth.js";                  Remote = "web/auth.js";                  Mpy = $false },
        @{ Local = "web\index.html";               Remote = "web/index.html";               Mpy = $false },
        @{ Local = "hardware_test.py";             Remote = "hardware_test.py";             Mpy = $false },
        @{ Local = "motor_test.py";                Remote = "motor_test.py";                Mpy = $false },
        @{ Local = "main.py";                      Remote = "main.py";                      Mpy = $false; IsMain = $true }
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
n_keys = len(cur)
del cur, inc, f
gc.collect()
print('SECRETS_MERGED', n_keys)
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
        if ($item.IsMain -and $mpyExe) {
            $mpyBytes = Build-MpyBytes $fullPath "main.py"
            Write-Host "Syncing main.py -> arse_app.mpy ($($mpyBytes.Length) bytes) + boot stub..." -ForegroundColor Cyan
            Send-RemoteBytes "arse_app.mpy" $mpyBytes "arse_app.py"
            $stubBytes = [System.Text.Encoding]::UTF8.GetBytes("from arse_app import ConeApp`napp = ConeApp()`napp.start()`n")
            Send-RemoteBytes "main.py" $stubBytes ""
        } elseif ($item.Mpy -and $mpyExe) {
            $remoteMpy = $item.Remote.Substring(0, $item.Remote.Length - 3) + ".mpy"
            $mpyBytes = Build-MpyBytes $fullPath $item.Remote
            Write-Host "Syncing $($item.Local) -> $remoteMpy ($($mpyBytes.Length) bytes)..." -ForegroundColor Cyan
            Send-RemoteBytes $remoteMpy $mpyBytes $item.Remote
        } else {
            $rawBytes = [System.IO.File]::ReadAllBytes($fullPath)
            $staleAlt = ""
            if ($item.IsMain) {
                $staleAlt = "arse_app.mpy"
            } elseif ($item.Remote.EndsWith(".py")) {
                $staleAlt = $item.Remote.Substring(0, $item.Remote.Length - 3) + ".mpy"
            }
            Write-Host "Syncing $($item.Local) -> $($item.Remote) ($($rawBytes.Length) bytes)..." -ForegroundColor Cyan
            Send-RemoteBytes $item.Remote $rawBytes $staleAlt
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
    if ($tempDir -and (Test-Path -LiteralPath $tempDir)) {
        Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
