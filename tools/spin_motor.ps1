$p = New-Object System.IO.Ports.SerialPort "COM3", 115200, None, 8, One
$p.ReadTimeout = 4000
$p.WriteTimeout = 4000
$p.DtrEnable = $true
$p.RtsEnable = $true
$p.Open()

function Send-Cmd([string]$cmd) {
    $p.Write($cmd + "`r`n")
    Start-Sleep -Milliseconds 350
    $out = $p.ReadExisting()
    Write-Host $out
}

# Interrupt any previous code
$p.Write([byte[]]@(0x03, 0x03), 0, 2)
Start-Sleep -Milliseconds 300
$null = $p.ReadExisting()

Write-Host "=== INITIALIZING YUKON & SLOTS ===" -ForegroundColor Cyan
Send-Cmd "from pimoroni_yukon import Yukon, SLOT1, SLOT2, SLOT4, SLOT5, SLOT6"
Send-Cmd "from pimoroni_yukon.modules import DualMotorModule, LEDStripModule, RM2WirelessModule, AudioAmpModule"
Send-Cmd "yukon = Yukon()"
Send-Cmd "m1 = DualMotorModule(); yukon.register_with_slot(m1, SLOT1)"
Send-Cmd "m2 = DualMotorModule(); yukon.register_with_slot(m2, SLOT2)"
Send-Cmd "leds = LEDStripModule(LEDStripModule.NEOPIXEL, 0, 0, 50, 0.4); yukon.register_with_slot(leds, SLOT4)"
Send-Cmd "rm2 = RM2WirelessModule(); yukon.register_with_slot(rm2, SLOT5)"
Send-Cmd "amp = AudioAmpModule(0); yukon.register_with_slot(amp, SLOT6)"

Send-Cmd "yukon.verify_and_initialise()"
Send-Cmd "m1.set_current_limit(1.0)"
Send-Cmd "yukon.enable_main_output()"
Send-Cmd "print('READY: Supply =', round(yukon.read_input_voltage(), 2), 'V')"

Write-Host "`n=== TESTING FORWARD SPIN (Slot 1, Motor 1 @ 35% speed) ===" -ForegroundColor Green
Send-Cmd "m1.enable()"
Send-Cmd "m1.motor1.speed(0.35)"
Start-Sleep -Milliseconds 600
Send-Cmd "print('SPINNING FORWARD | Current =', round(yukon.read_current(), 3), 'A')"
Start-Sleep -Milliseconds 900

Send-Cmd "m1.motor1.stop()"
Send-Cmd "print('STOPPED')"
Start-Sleep -Milliseconds 800

Write-Host "`n=== TESTING REVERSE SPIN (Slot 1, Motor 1 @ -35% speed) ===" -ForegroundColor Green
Send-Cmd "m1.motor1.speed(-0.35)"
Start-Sleep -Milliseconds 600
Send-Cmd "print('SPINNING REVERSE | Current =', round(yukon.read_current(), 3), 'A')"
Start-Sleep -Milliseconds 900

Send-Cmd "m1.motor1.stop()"
Send-Cmd "m1.disable()"
Send-Cmd "yukon.reset()"
Send-Cmd "print('--> TEST COMPLETE: MOTOR SAFELY STOPPED & STANDBY ACTIVE')"

# Check GPS NMEA state
Write-Host "`n=== CHECKING GPS NMEA STREAM ===" -ForegroundColor Cyan
Send-Cmd "from pimoroni_yukon import Yukon; y = Yukon()"
Send-Cmd "gps_raw = y.i2c.readfrom(0x10, 64)"
Send-Cmd "print('GPS:', repr(''.join(chr(b) for b in gps_raw if 32 <= b <= 126 or b in (10, 13))[:80]))"

$p.Close()
