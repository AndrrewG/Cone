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

$cmd = "import pimoroni_yukon.modules.audio_amp as aa; print(dir(aa.AudioAmpModule)); print(aa.AudioAmpModule.__init__)`r`n"
$p.Write($cmd)
Start-Sleep -Milliseconds 500
$out = $p.ReadExisting()
Write-Host "AMP_INFO: $out"

# Also read lines 20-50 of the file directly
$cmd2 = "f = open('/lib/pimoroni_yukon/modules/audio_amp.py'); [print(f.readline().strip()) for _ in range(35)]; f.close()`r`n"
$p.Write($cmd2)
Start-Sleep -Milliseconds 600
$out2 = $p.ReadExisting()
Write-Host "AMP_SOURCE: $out2"

$p.Close()
