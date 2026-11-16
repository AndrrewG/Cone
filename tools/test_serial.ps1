$p = New-Object System.IO.Ports.SerialPort "COM3", 115200, None, 8, One
$p.ReadTimeout = 3000
$p.WriteTimeout = 3000
$p.DtrEnable = $true
$p.RtsEnable = $true
$p.Open()

# Send Ctrl-C twice to halt any running loop
$p.Write([byte[]]@(0x03, 0x03), 0, 2)
Start-Sleep -Milliseconds 400

# Flush input
$null = $p.ReadExisting()

# Send simple command
$cmd = "print('PING_OK')`r`n"
$p.Write($cmd)
Start-Sleep -Milliseconds 600

$out = $p.ReadExisting()
$p.Close()
Write-Host "RESPONSE: $out"
