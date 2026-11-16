param(
    [string]$Port = "COM3",
    [string]$FileName = "hw_config.py"
)

$serial = New-Object System.IO.Ports.SerialPort $Port, 115200, None, 8, One
$serial.ReadTimeout = 4000
$serial.WriteTimeout = 4000
$serial.DtrEnable = $true
$serial.RtsEnable = $true
$serial.Open()

# Interrupt
$serial.Write([byte[]]@(0x03, 0x03), 0, 2)
Start-Sleep -Milliseconds 250
$null = $serial.ReadExisting()

# Read file
$py = @"
with open('$FileName', 'r') as f:
    print('===FILE_START===')
    print(f.read())
    print('===FILE_END===')
"@
$bytes = [System.Text.Encoding]::UTF8.GetBytes($py + "`r`n")
$serial.Write($bytes, 0, $bytes.Length)
Start-Sleep -Milliseconds 800

$output = $serial.ReadExisting()
Write-Host $output
$serial.Close()
