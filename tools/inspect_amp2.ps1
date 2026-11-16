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

$cmd = "f = open('/lib/pimoroni_yukon/modules/audio_amp.py'); [f.readline() for _ in range(35)]; [print(f.readline().strip()) for _ in range(40)]; f.close()`r`n"
$p.Write($cmd)
Start-Sleep -Milliseconds 800
$out = $p.ReadExisting()
Write-Host "AMP_INIT: $out"

$p.Close()
