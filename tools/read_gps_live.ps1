$p = New-Object System.IO.Ports.SerialPort "COM3", 115200, None, 8, One
$p.ReadTimeout = 4000
$p.WriteTimeout = 4000
$p.DtrEnable = $true
$p.RtsEnable = $true
$p.Open()

# Interrupt
$p.Write([byte[]]@(0x03, 0x03), 0, 2)
Start-Sleep -Milliseconds 300
$null = $p.ReadExisting()

# MicroPython GPS reader loop
$code = @"
from pimoroni_yukon import Yukon
import time
yukon = Yukon()

print('=== READING LIVE GPS (PA1010D) ===')
buf = ''
t_end = time.ticks_add(time.ticks_ms(), 5000)
sentences = []

while time.ticks_diff(t_end, time.ticks_ms()) > 0:
    try:
        raw = yukon.i2c.readfrom(0x10, 64)
        text = ''.join(chr(b) for b in raw if 32 <= b <= 126 or b in (10, 13))
        buf += text
        while '\n' in buf:
            line, buf = buf.split('\n', 1)
            line = line.strip()
            if line.startswith('$G') or line.startswith('$P'):
                sentences.append(line)
    except Exception as e:
        pass
    time.sleep_ms(60)

print(f'Captured {len(sentences)} NMEA sentences:')
for s in sentences[-12:]:
    print(' ', s)
"@

$bytes = [System.Text.Encoding]::UTF8.GetBytes($code + "`r`n")
$p.Write($bytes, 0, $bytes.Length)

$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.ElapsedMilliseconds -lt 8000) {
    try {
        $chunk = $p.ReadExisting()
        if ($chunk) { Write-Host $chunk -NoNewline }
        if ($chunk -match "Captured") { break }
    } catch { break }
    Start-Sleep -Milliseconds 200
}

# Wait for rest of output
Start-Sleep -Milliseconds 1500
Write-Host $p.ReadExisting()

$p.Close()
