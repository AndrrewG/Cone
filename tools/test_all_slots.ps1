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
Send-Cmd "amp = AudioAmpModule(0); yukon.register_with_slot(amp, SLOT6)"

# Now verify and initialise all 5 slots!
Send-Cmd "yukon.verify_and_initialise()"
Send-Cmd "print('--> ALL 5 SLOTS VERIFIED!')"

Send-Cmd "yukon.enable_main_output()"
Send-Cmd "print('--> MAIN POWER OUTPUT ENABLED (e-Fuse active)!')"
Send-Cmd "print('TELEMETRY: Volts =', round(yukon.read_input_voltage(), 2), 'V | Amps =', round(yukon.read_current(), 3), 'A | Temp =', round(yukon.read_temperature(), 1), 'C')"

# Flash 5 LEDs in orange briefly to visually confirm
Send-Cmd "for i in range(5): leds.strip.set_rgb(i, 255, 100, 0)"
Send-Cmd "leds.strip.update()"
Start-Sleep -Seconds 1
Send-Cmd "for i in range(50): leds.strip.set_rgb(i, 0, 0, 0)"
Send-Cmd "leds.strip.update()"

# Reset
Send-Cmd "yukon.reset()"
Send-Cmd "print('--> YUKON RESET TO SAFE STANDBY')"

$p.Close()
