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

function Send-Line([string]$cmd) {
    $p.Write($cmd + "`r`n")
    Start-Sleep -Milliseconds 300
    $out = $p.ReadExisting()
    Write-Host $out
}

Send-Line "from pimoroni_yukon import Yukon"
Send-Line "y = Yukon()"
Send-Line "import time"

# Accumulate and display NMEA sentences
Send-Line "buf = ''"
Send-Line "for _ in range(30):"
Send-Line "    raw = y.i2c.readfrom(0x10, 64)"
Send-Line "    buf += ''.join(chr(b) for b in raw if 32 <= b <= 126 or b in (10, 13))"
Send-Line "    while '\n' in buf:"
Send-Line "        line, buf = buf.split('\n', 1)"
Send-Line "        if any(line.startswith(p) for p in ('$GNRMC', '$GNGGA', '$GPGGA', '$GPRMC', '$GNVTG')):"
Send-Line "            print('NMEA:', line.strip())"
Send-Line "    time.sleep_ms(100)"

$p.Close()
