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

Send-Cmd "from pimoroni_yukon import Yukon, SLOT4"
Send-Cmd "from pimoroni_yukon.modules import LEDStripModule"
Send-Cmd "yukon = Yukon()"
Send-Cmd "led_mod = LEDStripModule(LEDStripModule.NEOPIXEL, 0, 0, 50, 0.4)"
Send-Cmd "yukon.register_with_slot(led_mod, SLOT4)"
Send-Cmd "yukon.verify_and_initialise()"
Send-Cmd "print('SLOT4_VERIFIED: YES')"
Send-Cmd "yukon.enable_main_output()"
Send-Cmd "print('MAIN_OUTPUT_ENABLED: YES')"

# Light up first 10 LEDs in amber (255, 140, 0)
Send-Cmd "for i in range(10): led_mod.strip.set_rgb(i, 255, 140, 0)"
Send-Cmd "led_mod.strip.update()"
Send-Cmd "print('LEDS_LIT_AMBER')"
Start-Sleep -Seconds 2

# Turn off
Send-Cmd "for i in range(50): led_mod.strip.set_rgb(i, 0, 0, 0)"
Send-Cmd "led_mod.strip.update()"
Send-Cmd "print('LEDS_CLEARED')"

Send-Cmd "yukon.reset()"
$p.Close()
