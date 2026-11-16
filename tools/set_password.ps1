# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Proprietary & Confidential — Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Script:      tools/set_password.ps1
# Description: Interactive local credential setter for secrets.json (IDEA-007).
#              Prompts with masked input, computes 1,000-round salted SHA-256,
#              preserves any Wi-Fi keys, and writes UTF-8 (no BOM) atomically.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================
param(
    [string]$Username = "",
    [string]$PlainPassword = "",
    [switch]$RotateBeacon = $false,
    [string]$SecretsPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $SecretsPath) {
    $SecretsPath = Join-Path $projectRoot "secrets.json"
}

function Read-MaskedPassword([string]$PromptText) {
    try {
        $secStr = Read-Host $PromptText -AsSecureString
        $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secStr)
        try {
            return [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        }
        finally {
            if ($bstr -ne [IntPtr]::Zero) {
                [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
            }
        }
    }
    catch {
        Write-Host -NoNewline "${PromptText}: "
        $sb = New-Object System.Text.StringBuilder
        while ($true) {
            $key = [Console]::ReadKey($true)
            if ($key.Key -eq [ConsoleKey]::Enter) {
                Write-Host ""
                break
            }
            elseif ($key.Key -eq [ConsoleKey]::Backspace) {
                if ($sb.Length -gt 0) {
                    $null = $sb.Remove($sb.Length - 1, 1)
                    Write-Host -NoNewline "`b `b"
                }
            }
            elseif ([int][char]$key.KeyChar -ge 0x20) {
                $null = $sb.Append($key.KeyChar)
                Write-Host -NoNewline "*"
            }
        }
        return $sb.ToString()
    }
}

function Test-ValidUsername([string]$Val) {
    if ([string]::IsNullOrEmpty($Val)) { return $false }
    if ($Val.Length -lt 1 -or $Val.Length -gt 32) { return $false }
    foreach ($ch in $Val.ToCharArray()) {
        $code = [int][char]$ch
        if ($code -lt 0x21 -or $code -gt 0x7E) { return $false }
    }
    return $true
}

function Test-ValidBeaconToken([string]$Val) {
    if ([string]::IsNullOrEmpty($Val)) { return $false }
    if ($Val.Length -lt 16 -or $Val.Length -gt 128) { return $false }
    foreach ($ch in $Val.ToCharArray()) {
        $code = [int][char]$ch
        $isAlnum = (($code -ge 0x30 -and $code -le 0x39) -or
                    ($code -ge 0x41 -and $code -le 0x5A) -or
                    ($code -ge 0x61 -and $code -le 0x7A))
        if (-not ($isAlnum -or $ch -in @('-', '_', '.', '~'))) { return $false }
    }
    return $true
}

function New-UrlSafeToken([System.Security.Cryptography.RandomNumberGenerator]$Rng, [int]$NumBytes) {
    $buf = New-Object byte[] $NumBytes
    $Rng.GetBytes($buf)
    return [Convert]::ToBase64String($buf).Replace("+", "-").Replace("/", "_").TrimEnd("=")
}

# 1. Load any existing secrets.json so Wi-Fi ("ssid"/"password") and beacon_token stay intact
$existingMap = [ordered]@{}
$defaultUser = "admin"
$existingBeacon = ""

if (Test-Path -LiteralPath $SecretsPath) {
    try {
        $rawText = [System.IO.File]::ReadAllText($SecretsPath, [System.Text.Encoding]::UTF8)
        $parsed = $rawText | ConvertFrom-Json
        if ($null -ne $parsed) {
            foreach ($prop in $parsed.PSObject.Properties) {
                if ($prop.Name -ne "bootstrapped_password") {
                    $existingMap[$prop.Name] = $prop.Value
                }
            }
            if ($existingMap.Contains("username") -and (Test-ValidUsername ([string]$existingMap["username"]))) {
                $defaultUser = [string]$existingMap["username"]
            }
            if ($existingMap.Contains("beacon_token") -and (Test-ValidBeaconToken ([string]$existingMap["beacon_token"]))) {
                $existingBeacon = [string]$existingMap["beacon_token"]
            }
        }
    }
    catch {
        Write-Warning "Existing secrets.json could not be parsed; creating fresh credentials."
    }
}

Write-Host "`n=== ARSE Mk.I — Operator Logon Credential Setup ===" -ForegroundColor Cyan

# 2. Prompt for Username (if not passed via -Username)
if ([string]::IsNullOrWhiteSpace($Username)) {
    $inputUser = Read-Host "Operator username [$defaultUser]"
    if ([string]::IsNullOrWhiteSpace($inputUser)) {
        $Username = $defaultUser
    }
    else {
        $Username = $inputUser.Trim()
    }
}
else {
    $Username = $Username.Trim()
}

if (-not (Test-ValidUsername $Username)) {
    throw "Username must be 1..32 printable non-whitespace ASCII characters (0x21..0x7E)."
}

# 3. Prompt for Password with masked confirmation (unless passed via -PlainPassword)
$plainPw = $null
$plainConfirm = $null
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$sha = [System.Security.Cryptography.SHA256]::Create()

try {
    if (-not $PSBoundParameters.ContainsKey("PlainPassword") -or [string]::IsNullOrEmpty($PlainPassword)) {
        $plainPw = Read-MaskedPassword "New operator password (8-128 chars)"
        $plainConfirm = Read-MaskedPassword "Confirm new operator password"

        if ($plainPw -cne $plainConfirm) {
            throw "Passwords did not match — secrets.json was not modified."
        }
    }
    else {
        $plainPw = $PlainPassword
    }

    if ($plainPw.Length -lt 8 -or $plainPw.Length -gt 128) {
        throw "Password must be between 8 and 128 characters."
    }

    # 4. Generate 16-byte random salt & compute 1,000-round iterated SHA-256
    #    Matches security/auth_manager.py hash_password(password_str, salt_bytes, rounds=1000)
    $salt = New-Object byte[] 16
    $rng.GetBytes($salt)
    $saltHex = ([System.BitConverter]::ToString($salt)).Replace("-", "").ToLowerInvariant()

    $pwBytes = [System.Text.Encoding]::UTF8.GetBytes($plainPw)
    try {
        $round1 = New-Object byte[] ($salt.Length + $pwBytes.Length)
        [Array]::Copy($salt, 0, $round1, 0, $salt.Length)
        [Array]::Copy($pwBytes, 0, $round1, $salt.Length, $pwBytes.Length)
        $digest = $sha.ComputeHash($round1)

        $roundN = New-Object byte[] ($salt.Length + 32)
        [Array]::Copy($salt, 0, $roundN, 0, $salt.Length)
        for ($i = 1; $i -lt 1000; $i++) {
            [Array]::Copy($digest, 0, $roundN, $salt.Length, 32)
            $digest = $sha.ComputeHash($roundN)
        }
        $hashHex = ([System.BitConverter]::ToString($digest)).Replace("-", "").ToLowerInvariant()
    }
    finally {
        [Array]::Clear($pwBytes, 0, $pwBytes.Length)
    }

    # 5. Preserve or generate 24-byte URL-safe beacon_token
    if ($RotateBeacon -or -not $existingBeacon) {
        $beaconToken = New-UrlSafeToken -Rng $rng -NumBytes 24
    }
    else {
        $beaconToken = $existingBeacon
    }

    $existingMap["username"] = $Username
    $existingMap["password_salt_hex"] = $saltHex
    $existingMap["password_hash_hex"] = $hashHex
    $existingMap["beacon_token"] = $beaconToken

    # 6. Write UTF-8 WITHOUT BOM atomically (MicroPython ujson rejects UTF-8 BOMs)
    $parentDir = Split-Path -Parent $SecretsPath
    if ($parentDir -and -not (Test-Path -LiteralPath $parentDir)) {
        $null = New-Item -ItemType Directory -Path $parentDir -Force
    }

    $jsonOut = ($existingMap | ConvertTo-Json -Depth 10) + "`n"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $tmpPath = "$SecretsPath.tmp"
    [System.IO.File]::WriteAllBytes($tmpPath, $utf8NoBom.GetBytes($jsonOut))
    Move-Item -LiteralPath $tmpPath -Destination $SecretsPath -Force

    Write-Host "`n[OK] Saved salted 1,000-round SHA-256 credentials to: $SecretsPath" -ForegroundColor Green
    Write-Host "     Username     : $Username"
    Write-Host "     Beacon Token : $beaconToken"
    Write-Host "     Git Status   : Ignored via .gitignore (never committed)`n" -ForegroundColor DarkGray
}
finally {
    $sha.Dispose()
    $rng.Dispose()
    $plainPw = $null
    $plainConfirm = $null
}
