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

$p.Write("from pimoroni_yukon.modules import AudioAmpModule`r`n")
Start-Sleep -Milliseconds 200
$p.Write("help(AudioAmpModule)`r`n")
Start-Sleep -Milliseconds 500
$out = $p.ReadExisting()
Write-Host $out
$p.Close()
