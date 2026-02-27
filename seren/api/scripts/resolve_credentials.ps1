param()

$ErrorActionPreference = 'Stop'

function Get-CredentialsPath {
  if (-not [string]::IsNullOrWhiteSpace($env:SEREN_CREDENTIALS_FILE)) {
    return $env:SEREN_CREDENTIALS_FILE
  }

  # The seren CLI uses etcetera::choose_base_strategy() which resolves to
  # XDG on all unix platforms (including macOS).  Match that behaviour so
  # the credential scripts and the CLI share the same file.
  if ($IsWindows -and -not [string]::IsNullOrWhiteSpace($env:APPDATA)) {
    return (Join-Path $env:APPDATA 'seren\credentials.toml')
  }

  $xdg = if (-not [string]::IsNullOrWhiteSpace($env:XDG_CONFIG_HOME)) {
    $env:XDG_CONFIG_HOME
  } else {
    Join-Path $HOME '.config'
  }

  return (Join-Path $xdg 'seren/credentials.toml')
}

# Read a bare TOML key from the credentials file.
# The CLI writes flat key-value pairs (no section headers):
#   api_key = "..."        (API-key login)
#   access_token = "..."   (OAuth login)
function Read-TomlKey([string]$Path, [string]$Key) {
  if (-not (Test-Path -LiteralPath $Path)) {
    return $null
  }

  foreach ($line in Get-Content -LiteralPath $Path) {
    if ($line -match ('^\s*' + [regex]::Escape($Key) + '\s*=\s*"(.*?)"(?:\s*#.*)?\s*$')) {
      return $Matches[1].Trim()
    }
  }

  return $null
}

$apiHost = if ([string]::IsNullOrWhiteSpace($env:SEREN_API_HOST)) {
  'https://api.serendb.com'
} else {
  $env:SEREN_API_HOST.TrimEnd('/')
}

$autoCreate = if ([string]::IsNullOrWhiteSpace($env:SEREN_AUTO_CREATE_KEY)) {
  '1'
} else {
  $env:SEREN_AUTO_CREATE_KEY
}

$credentialsFile = Get-CredentialsPath
$env:SEREN_CREDENTIALS_FILE = $credentialsFile

# Resolve an existing key.  The CLI stores either api_key (API-key login)
# or access_token (OAuth login).  Prefer api_key.
if ([string]::IsNullOrWhiteSpace($env:SEREN_API_KEY)) {
  $storedKey = Read-TomlKey -Path $credentialsFile -Key 'api_key'
  if ([string]::IsNullOrWhiteSpace($storedKey)) {
    $storedKey = Read-TomlKey -Path $credentialsFile -Key 'access_token'
  }
  if (-not [string]::IsNullOrWhiteSpace($storedKey)) {
    $env:SEREN_API_KEY = $storedKey
  }
}

if ([string]::IsNullOrWhiteSpace($env:SEREN_API_KEY)) {
  if ($autoCreate -eq '0') {
    throw "SEREN_API_KEY not set and no key found in $credentialsFile"
  }

  $response = Invoke-RestMethod -Method Post -Uri "$apiHost/auth/agent" -ContentType 'application/json' -Body '{}'
  $key = $response.data.agent.api_key

  if ([string]::IsNullOrWhiteSpace($key)) {
    $key = $response.api_key
  }

  if ([string]::IsNullOrWhiteSpace($key) -and $null -ne $response.body) {
    $key = $response.body.api_key
  }

  if ([string]::IsNullOrWhiteSpace($key)) {
    throw 'failed to parse api_key from /auth/agent response'
  }

  $dir = Split-Path -Parent $credentialsFile
  if (-not [string]::IsNullOrWhiteSpace($dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
  }

  # Append to existing file rather than clobbering CLI-written tokens.
  if ((Test-Path -LiteralPath $credentialsFile) -and ((Get-Content -LiteralPath $credentialsFile -Raw) -match '\S')) {
    $lines = Get-Content -LiteralPath $credentialsFile | Where-Object { $_ -notmatch '^\s*api_key\s*=' }
    $lines += ('api_key = "{0}"' -f $key)
    Set-Content -LiteralPath $credentialsFile -Value ($lines -join "`n") -Encoding utf8
  } else {
    Set-Content -LiteralPath $credentialsFile -Value ('api_key = "{0}"' -f $key) -NoNewline -Encoding utf8
  }

  $env:SEREN_API_KEY = $key
}
