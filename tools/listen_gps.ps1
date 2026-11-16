$p = New-Object System.IO.Ports.SerialPort "COM3", 115200, None, 8, One
$p.ReadTimeout = 4000
$p.WriteTimeout = 4000
$p.DtrEnable = $true
$p.RtsEnable = $true
$p.Open()

# Interrupt
$p.Write([byte[]]@(0x03, 0x03), 0, 2)
Start-Sleep -Milliseconds 250
$null = $p.ReadExisting()

$py = @"
from pimoroni_yukon import Yukon
import time
y = Yukon()
print('--- STREAMING GPS 4 SECONDS ---')
buf = ''
t_end = time.ticks_add(time.ticks_ms(), 4000)
while time.ticks_diff(t_end, time.ticks_ms()) > 0:
    raw = y.i2c.readfrom(0x10, 64)
    buf += ''.join(chr(b) for b in raw if 32 <= b <= 126 or b in (10, 13))
    while '\n' in buf:
        line, buf = buf.split('\n', 1)
        line = line.strip()
        if line.startswith('$G'):
            print('  NMEA:', line)
    time.sleep_ms(80)
print('--- END GPS STREAM ---')
"@

$p.Write($py + "`r`n")
$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.ElapsedMilliseconds -lt 6000) {
    $chunk = $p.ReadExisting()
    if ($chunk) { Write-Host $chunk -NoNewline }
    if ($chunk -match "END GPS STREAM") { break }
    Start-Sleep -Milliseconds 150
}
$p.Close()
