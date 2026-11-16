$p = New-Object System.IO.Ports.SerialPort "COM3", 115200, None, 8, One
$p.ReadTimeout = 5000
$p.WriteTimeout = 5000
$p.DtrEnable = $true
$p.RtsEnable = $true

try {
    $p.Open()
} catch {
    Write-Host "Could not open COM3. Please ensure the Yukon board is connected via USB and powered on." -ForegroundColor Yellow
    exit 1
}

# Interrupt any previous loop
$p.Write([byte[]]@(0x03, 0x03), 0, 2)
Start-Sleep -Milliseconds 300
$null = $p.ReadExisting()

Write-Host "Sending motor bench spin test..." -ForegroundColor Cyan

# Send the motor_test script
$code = [System.IO.File]::ReadAllText((Join-Path (Split-Path -Parent $PSScriptRoot) "motor_test.py"))
$p.Write("exec('''" + $code.Replace("'''", "") + "''')`r`n")

$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.ElapsedMilliseconds -lt 15000) {
    try {
        $chunk = $p.ReadExisting()
        if ($chunk) { Write-Host $chunk -NoNewline }
        if ($chunk -match "Safely resetting Yukon") { break }
    } catch { break }
    Start-Sleep -Milliseconds 150
}

$p.Close()
Write-Host "`nMotor test completed." -ForegroundColor Green
