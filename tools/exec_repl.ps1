# ==============================================================================
# ARSE - Autonomous Road Systems Engineering
# Proprietary & Confidential - Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Script:      tools/exec_repl.ps1
# Description: One-shot Raw-REPL diagnostic command utility over USB Serial.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================
param(
    [string]$Port = "COM3",
    [string]$Code = "import os; print('FILES:', os.listdir())",
    [int]$TimeoutMs = 6000
)

$ErrorActionPreference = "Stop"
$serial = New-Object System.IO.Ports.SerialPort $Port, 115200, None, 8, One
$serial.ReadTimeout = 4000
$serial.WriteTimeout = 4000
$serial.DtrEnable = $true
$serial.RtsEnable = $true
$serial.Open()

try {
    # 1. Interrupt running program (Ctrl-C twice)
    $serial.Write([byte[]]@(0x03, 0x03), 0, 2)
    Start-Sleep -Milliseconds 250
    $null = $serial.ReadExisting()

    # 2. Enter Raw REPL (Ctrl-A)
    $serial.Write([byte[]]@(0x01), 0, 1)
    Start-Sleep -Milliseconds 250
    $resp = $serial.ReadExisting()

    if ($resp -notmatch "raw REPL") {
        $serial.Write([byte[]]@(0x03, 0x01), 0, 2)
        Start-Sleep -Milliseconds 300
        $null = $serial.ReadExisting()
    }

    # 3. Send code and execute with Ctrl-D (0x04)
    $codeBytes = [System.Text.Encoding]::UTF8.GetBytes($Code + "`r`n")
    $serial.Write($codeBytes, 0, $codeBytes.Length)
    $serial.Write([byte[]]@(0x04), 0, 1)

    # 4. Read response until raw REPL prompt ([char]4 + ">")
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $result = ""
    $endMarker = [string][char]4 + ">"
    while ($sw.ElapsedMilliseconds -lt $TimeoutMs) {
        Start-Sleep -Milliseconds 50
        $chunk = $serial.ReadExisting()
        if ($chunk) {
            $result += $chunk
            if ($result.Contains($endMarker)) { break }
        }
    }

    # Parse Raw REPL frame: OK<stdout>\x04<stderr>\x04>
    $okIdx = $result.IndexOf("OK")
    if ($okIdx -ge 0) {
        $result = $result.Substring($okIdx + 2)
    }
    $parts = $result.Split([char]4)
    if ($parts.Length -ge 1 -and $parts[0]) {
        Write-Host $parts[0].TrimEnd()
    }
    if ($parts.Length -ge 2 -and $parts[1].Trim()) {
        Write-Host "STDERR: $($parts[1].Trim())" -ForegroundColor Red
    }
}
finally {
    try { $serial.Write([byte[]]@(0x02), 0, 1); Start-Sleep -Milliseconds 50 } catch {}
    $serial.Close()
}
