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

$py = @"
with open('/lib/pimoroni_yukon/modules/audio_amp.py') as f:
    lines = f.readlines()
for i, l in enumerate(lines):
    if 'def __init__' in l or 'def initialise' in l:
        print(f'{i}: {l.strip()}')
        for j in range(1, 10):
            print(f'   {lines[i+j].strip()}')
"@
$p.Write($py + "`r`n")
Start-Sleep -Milliseconds 800
$out = $p.ReadExisting()
Write-Host "DEFS: $out"
$p.Close()
