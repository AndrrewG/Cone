<#
.SYNOPSIS
    Deploy and run hardware test on Pimoroni Yukon over USB Serial (MicroPython)
#>

param(
    [string]$Port = "",
    [switch]$RunTest = $true
)

function Find-YukonPort {
    $ports = [System.IO.Ports.SerialPort]::GetPortNames()
    if ($ports.Count -eq 0) {
        Write-Error "No COM ports detected! Please connect the Yukon board via USB-C and turn it on."
        exit 1
    }
    # Return first port that isn't COM1 if possible, else the only port
    $target = $ports | Where-Object { $_ -ne "COM1" } | Select-Object -First 1
    if (-not $target) { $target = $ports[0] }
    return $target
}

if (-not $Port) {
    $Port = Find-YukonPort
}

Write-Host "Connecting to Yukon on $Port..." -ForegroundColor Cyan

$serial = New-Object System.IO.Ports.SerialPort $Port, 115200, None, 8, One
$serial.ReadTimeout = 4000
$serial.WriteTimeout = 4000
$serial.DtrEnable = $true
$serial.RtsEnable = $true

try {
    $serial.Open()
} catch {
    Write-Error "Failed to open port $Port. It might be open in Thonny, another terminal, or the board is rebooting."
    exit 1
}

function Send-RawCommand([string]$code) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($code + "`r`n`x04")
    $serial.Write($bytes, 0, $bytes.Length)
    Start-Sleep -Milliseconds 150
}

function Upload-File([string]$localPath, [string]$remotePath) {
    Write-Host "  Uploading $localPath -> $remotePath..." -ForegroundColor Yellow
    $bytes = [System.IO.File]::ReadAllBytes($localPath)
    $b64 = [Convert]::ToBase64String($bytes)

    $py = @"
import ubinascii
data = ubinascii.a2b_base64('$b64')
with open('$remotePath', 'wb') as f:
    f.write(data)
print('OK:$remotePath')
"@
    Send-RawCommand $py
    Start-Sleep -Milliseconds 300
}

# Enter Raw REPL
Write-Host "Entering MicroPython Raw REPL..." -ForegroundColor Green
$serial.Write([byte[]]@(0x03, 0x03, 0x01), 0, 3)
Start-Sleep -Milliseconds 400

# Create drivers directory
Send-RawCommand "import os; try: os.mkdir('drivers'); except: pass; print('DIR_READY')"
Start-Sleep -Milliseconds 200

# Upload files
$baseDir = Split-Path -Parent $PSScriptRoot
Upload-File (Join-Path $baseDir "config.py") "config.py"
Upload-File (Join-Path $baseDir "drivers\icm20948.py") "drivers/icm20948.py"
Upload-File (Join-Path $baseDir "drivers\pa1010d.py") "drivers/pa1010d.py"
Upload-File (Join-Path $baseDir "drivers\omni_kinematics.py") "drivers/omni_kinematics.py"
Upload-File (Join-Path $baseDir "drivers\lights.py") "drivers/lights.py"
Upload-File (Join-Path $baseDir "hardware_test.py") "hardware_test.py"

Write-Host "`nAll files successfully synced to Yukon!" -ForegroundColor Green

if ($RunTest) {
    Write-Host "`nStarting hardware validation test output...`n" -ForegroundColor Cyan
    # Exit raw repl and execute hardware_test
    Send-RawCommand "import hardware_test; hardware_test.run_hardware_test()"
    
    $timeout = [System.Diagnostics.Stopwatch]::StartNew()
    while ($timeout.ElapsedMilliseconds -lt 25000) {
        try {
            $line = $serial.ReadLine()
            Write-Host $line
            if ($line -match "HARDWARE TEST COMPLETE") { break }
        } catch [System.TimeoutException] {
            # continue waiting
        }
    }
}

$serial.Close()
Write-Host "`nDone." -ForegroundColor Green
