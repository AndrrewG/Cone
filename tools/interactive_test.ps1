$p = New-Object System.IO.Ports.SerialPort "COM3", 115200, None, 8, One
$p.ReadTimeout = 4000
$p.WriteTimeout = 4000
$p.DtrEnable = $true
$p.RtsEnable = $true
$p.Open()

function Send-Line([string]$cmd) {
    $p.Write($cmd + "`r`n")
    Start-Sleep -Milliseconds 350
    $out = $p.ReadExisting()
    Write-Host $out
}

# Ctrl-C to interrupt
$p.Write([byte[]]@(0x03, 0x03), 0, 2)
Start-Sleep -Milliseconds 300
$null = $p.ReadExisting()

Send-Line "from pimoroni_yukon import Yukon"
Send-Line "yukon = Yukon()"
Send-Line "print('I2C_SCAN:', [hex(x) for x in yukon.i2c.scan()])"
Send-Line "import network"
Send-Line "wlan = network.WLAN(network.STA_IF); wlan.active(True)"
Start-Sleep -Milliseconds 500
Send-Line "print('WIFI_STATUS:', wlan.status())"
Send-Line "nets = wlan.scan()"
Start-Sleep -Milliseconds 1500
Send-Line "print('FOUND NETWORKS:', len(nets))"

$p.Close()
