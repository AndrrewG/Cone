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

$cmd = "exec(\""with open('/lib/pimoroni_yukon/modules/audio_amp.py') as f:\n for l in f:\n  if 'def __init__' in l or 'def initialise' in l: print(l.strip())\n\"")`r`n"
$p.Write($cmd)
Start-Sleep -Milliseconds 600
$out = $p.ReadExisting()
Write-Host "INIT_SIG: $out"
$p.Close()
