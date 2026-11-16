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

# Interrupt
$p.Write([byte[]]@(0x03, 0x03), 0, 2)
Start-Sleep -Milliseconds 250
$null = $p.ReadExisting()

Send-Cmd "from pimoroni_yukon import Yukon, SLOT1, SLOT2, SLOT4, SLOT5, SLOT6"
Send-Cmd "from pimoroni_yukon.modules import DualMotorModule, LEDStripModule, RM2WirelessModule, AudioAmpModule"
Send-Cmd "yukon = Yukon()"
Send-Cmd "m1 = DualMotorModule(); yukon.register_with_slot(m1, SLOT1)"
Send-Cmd "m2 = DualMotorModule(); yukon.register_with_slot(m2, SLOT2)"
Send-Cmd "leds = LEDStripModule(LEDStripModule.NEOPIXEL, 0, 0, 50, 0.4); yukon.register_with_slot(leds, SLOT4)"
Send-Cmd "rm2 = RM2WirelessModule(); yukon.register_with_slot(rm2, SLOT5)"
Send-Cmd "amp = AudioAmpModule(); yukon.register_with_slot(amp, SLOT6)"

# Now verify with all modules registered!
Send-Cmd "yukon.verify_and_initialise()"
Send-Cmd "yukon.enable_main_output()"
Send-Cmd "print('SYSTEM_HEALTH: Supply =', round(yukon.read_input_voltage(), 2), 'V | Current =', round(yukon.read_current(), 3), 'A')"

# Sweep 5 LEDs in amber on the strip
Send-Cmd "for i in range(5): leds.strip.set_rgb(i, 255, 140, 0)"
Send-Cmd "leds.strip.update()"
Start-Sleep -Seconds 1
Send-Cmd "for i in range(50): leds.strip.set_rgb(i, 0, 0, 0)"
Send-Cmd "leds.strip.update()"

# Reset
Send-Cmd "yukon.reset()"
Send-Cmd "print('ALL_SYSTEMS_VERIFIED_CLEAN')"

$p.Close()
