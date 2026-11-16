$p = New-Object System.IO.Ports.SerialPort "COM3", 115200, None, 8, One
$p.ReadTimeout = 8000
$p.WriteTimeout = 8000
$p.DtrEnable = $true
$p.RtsEnable = $true
$p.Open()

# Interrupt
$p.Write([byte[]]@(0x03, 0x03), 0, 2)
Start-Sleep -Milliseconds 400
$null = $p.ReadExisting()

# Enter raw REPL
$p.Write([byte[]]@(0x01), 0, 1)
Start-Sleep -Milliseconds 300
$null = $p.ReadExisting()

$pyCode = @"
import time, network
from pimoroni_yukon import Yukon, SLOT5
from pimoroni_yukon.modules import RM2WirelessModule

print('--- STARTING DIAGNOSTIC ---')
yukon = Yukon()
rm2 = RM2WirelessModule()
yukon.register_with_slot(rm2, SLOT5)

try:
    yukon.verify_and_initialise()
    print('SLOT5_RM2_VERIFIED: YES')
except Exception as e:
    print('SLOT5_VERIFY_ERROR:', e)

try:
    yukon.enable_main_output()
    print('MAIN_POWER_ENABLED: YES')
    print('VOLTAGE:', round(yukon.read_input_voltage(), 2), 'V')
except Exception as e:
    print('POWER_ENABLE_ERROR:', e)

# 1. I2C Bus Scan
try:
    devices = yukon.i2c.scan()
    print('I2C_DEVICES:', [hex(x) for x in devices])
except Exception as e:
    print('I2C_SCAN_ERROR:', e)

# 2. Test GPS on 0x10
try:
    if 0x10 in devices:
        print('GPS_I2C_0x10: FOUND')
        raw = yukon.i2c.readfrom(0x10, 64)
        sample = ''.join(chr(b) for b in raw if 32 <= b <= 126 or b in (10, 13))
        print('GPS_NMEA_SAMPLE:', repr(sample[:60]))
    else:
        print('GPS_I2C_0x10: NOT_FOUND')
except Exception as e:
    print('GPS_READ_ERROR:', e)

# 3. Test IMU on 0x68
try:
    if 0x68 in devices:
        who = yukon.i2c.readfrom_mem(0x68, 0x00, 1)[0]
        print('IMU_WHO_AM_I: 0x{:02X} (PASS if 0xEA)'.format(who))
    else:
        print('IMU_I2C_0x68: NOT_FOUND')
except Exception as e:
    print('IMU_READ_ERROR:', e)

# 4. Test Wi-Fi scan
try:
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    time.sleep_ms(600)
    nets = wlan.scan()
    print('WIFI_SCAN: SUCCESS - Found', len(nets), 'networks')
    for n in nets[:3]:
        print('  SSID:', n[0].decode('utf-8', 'ignore'), '| RSSI:', n[3])
except Exception as e:
    print('WIFI_ERROR:', e)

print('--- DIAGNOSTIC COMPLETE ---')
"@

# Send code + Ctrl-D
$bytes = [System.Text.Encoding]::UTF8.GetBytes($pyCode + "`x04")
$p.Write($bytes, 0, $bytes.Length)

# Read output
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$result = ""
while ($sw.ElapsedMilliseconds -lt 15000) {
    try {
        $chunk = $p.ReadExisting()
        if ($chunk) {
            $result += $chunk
            if ($result -match "DIAGNOSTIC COMPLETE") { break }
        }
    } catch { break }
    Start-Sleep -Milliseconds 200
}

# Exit raw repl
$p.Write([byte[]]@(0x02), 0, 1)
$p.Close()

Write-Host $result
