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

$p.Write("f = open('/lib/pimoroni_yukon/modules/audio_amp.py'); lines = [f.readline() for _ in range(45)]; f.close()`r`n")
Start-Sleep -Milliseconds 400
$p.Write("for l in lines: print(l, end='')`r`n")
Start-Sleep -Milliseconds 600
$out = $p.ReadExisting()
Write-Host $out
$p.Close()
