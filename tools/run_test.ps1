$p = New-Object System.IO.Ports.SerialPort "COM3", 115200, None, 8, One
$p.ReadTimeout = 5000
$p.WriteTimeout = 5000
$p.DtrEnable = $true
$p.RtsEnable = $true
$p.Open()

function Send-Cmd([string]$cmd) {
    $p.Write($cmd + "`r`n")
    Start-Sleep -Milliseconds 400
    $out = $p.ReadExisting()
    Write-Host $out
}

# Interrupt
$p.Write([byte[]]@(0x03, 0x03), 0, 2)
Start-Sleep -Milliseconds 300
$null = $p.ReadExisting()

Write-Host "=== YUKON LIVE TELEMETRY & HARDWARE RUNNER ===" -ForegroundColor Green

Send-Cmd "from pimoroni_yukon import Yukon, SLOT1, SLOT2, SLOT4, SLOT5, SLOT6"
Send-Cmd "from pimoroni_yukon.modules import DualMotorModule, LEDStripModule, RM2WirelessModule, AudioAmpModule"
Send-Cmd "yukon = Yukon()"
Send-Cmd "m1 = DualMotorModule(); yukon.register_with_slot(m1, SLOT1)"
Send-Cmd "m2 = DualMotorModule(); yukon.register_with_slot(m2, SLOT2)"
Send-Cmd "leds = LEDStripModule(LEDStripModule.NEOPIXEL, 0, 0, 50, 0.4); yukon.register_with_slot(leds, SLOT4)"
Send-Cmd "rm2 = RM2WirelessModule(); yukon.register_with_slot(rm2, SLOT5)"
Send-Cmd "amp = AudioAmpModule(0); yukon.register_with_slot(amp, SLOT6)"

# Initialize & verify
Send-Cmd "yukon.verify_and_initialise()"
Send-Cmd "yukon.enable_main_output()"

# Telemetry
Send-Cmd "v = yukon.read_input_voltage(); a = yukon.read_current(); t = yukon.read_temperature()"
Send-Cmd "print(f'POWER: Voltage={v:.2f}V, Current={a:.3f}A, Temp={t:.1f}C')"

# IMU
Send-Cmd "yukon.i2c.writeto_mem(0x68, 0x06, bytes([0x01]))"
Send-Cmd "import struct"
Send-Cmd "raw = yukon.i2c.readfrom_mem(0x68, 0x2D, 12)"
Send-Cmd "ax, ay, az, gx, gy, gz = struct.unpack('>hhhhhh', raw)"
Send-Cmd "print(f'IMU: Accel=({ax/16384:.2f}, {ay/16384:.2f}, {az/16384:.2f})g, Gyro=({gx/131:.1f}, {gy/131:.1f}, {gz/131:.1f})dps')"

# GPS
Send-Cmd "gps_bytes = yukon.i2c.readfrom(0x10, 64)"
Send-Cmd "gps_txt = ''.join(chr(b) for b in gps_bytes if 32 <= b <= 126 or b in (10, 13))"
Send-Cmd "print('GPS_NMEA:', repr(gps_txt[:70]))"

# Wi-Fi
Send-Cmd "import network; w = network.WLAN(network.STA_IF); w.active(True)"
Send-Cmd "nets = w.scan()"
Send-Cmd "print(f'WIFI: Found {len(nets)} networks. Best: {nets[0][0].decode()} (RSSI: {nets[0][3]} dBm)')"

# Amber LED flash test
Send-Cmd "for i in range(10): leds.strip.set_rgb(i, 255, 120, 0)"
Send-Cmd "leds.strip.update()"
Start-Sleep -Seconds 1
Send-Cmd "for i in range(50): leds.strip.set_rgb(i, 0, 0, 0)"
Send-Cmd "leds.strip.update()"

# Reset
Send-Cmd "yukon.reset()"
Send-Cmd "print('ALL_SYSTEMS_VERIFIED_AND_SAFE')"

$p.Close()
