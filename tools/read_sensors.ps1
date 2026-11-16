$p = New-Object System.IO.Ports.SerialPort "COM3", 115200, None, 8, One
$p.ReadTimeout = 4000
$p.WriteTimeout = 4000
$p.DtrEnable = $true
$p.RtsEnable = $true
$p.Open()

function Send-Cmd([string]$cmd) {
    $p.Write($cmd + "`r`n")
    Start-Sleep -Milliseconds 300
    $out = $p.ReadExisting()
    Write-Host $out
}

# Interrupt
$p.Write([byte[]]@(0x03, 0x03), 0, 2)
Start-Sleep -Milliseconds 250
$null = $p.ReadExisting()

Send-Cmd "from pimoroni_yukon import Yukon"
Send-Cmd "yukon = Yukon()"
Send-Cmd "print('BOARD_VOLTAGE:', yukon.read_input_voltage())"
Send-Cmd "print('BOARD_TEMP:', yukon.read_temperature())"

# Test IMU WHO_AM_I and wake up
Send-Cmd "who = yukon.i2c.readfrom_mem(0x68, 0x00, 1)[0]"
Send-Cmd "print('IMU_WHO_AM_I:', hex(who))"
Send-Cmd "yukon.i2c.writeto_mem(0x68, 0x06, bytes([0x01]))"
Start-Sleep -Milliseconds 100

# Read Accel (X, Y, Z)
Send-Cmd "import struct"
Send-Cmd "raw_accel = yukon.i2c.readfrom_mem(0x68, 0x2D, 6)"
Send-Cmd "ax, ay, az = struct.unpack('>hhh', raw_accel)"
Send-Cmd "print('ACCEL_G:', ax/16384.0, ay/16384.0, az/16384.0)"

# Read GPS NMEA stream (0x10)
Send-Cmd "gps_raw = yukon.i2c.readfrom(0x10, 64)"
Send-Cmd "gps_str = ''.join(chr(b) for b in gps_raw if 32 <= b <= 126 or b in (10, 13))"
Send-Cmd "print('GPS_NMEA_DATA:', repr(gps_str[:80]))"

$p.Close()
