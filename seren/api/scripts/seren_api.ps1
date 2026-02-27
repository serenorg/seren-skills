param(
  [Parameter(Position = 0, Mandatory = $true)]
  [string]$Method,

  [Parameter(Position = 1, Mandatory = $true)]
  [string]$PathOrUrl,

  [string]$Body,
  [string]$BodyFile,
  [string]$Host = $env:SEREN_API_HOST,
  [string[]]$Header,
  [switch]$NoAuth,

  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$CurlArgs
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($Host)) {
  $Host = 'https://api.serendb.com'
}
$Host = $Host.TrimEnd('/')

if (-not $Header) {
  $Header = @()
}

if (-not $NoAuth) {
  $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  . (Join-Path $scriptDir 'resolve_credentials.ps1')

  if ([string]::IsNullOrWhiteSpace($env:SEREN_API_KEY)) {
    throw 'Failed to resolve SEREN_API_KEY.'
  }

  $Header += "Authorization: Bearer $($env:SEREN_API_KEY)"
}

if (-not [string]::IsNullOrWhiteSpace($Body) -and -not [string]::IsNullOrWhiteSpace($BodyFile)) {
  throw 'Use either -Body or -BodyFile, not both.'
}

$hasContentType = $false
foreach ($h in $Header) {
  if ($h -match '^\s*Content-Type\s*:') {
    $hasContentType = $true
    break
  }
}

if ((-not [string]::IsNullOrWhiteSpace($Body) -or -not [string]::IsNullOrWhiteSpace($BodyFile)) -and -not $hasContentType) {
  $Header += 'Content-Type: application/json'
}

if ($PathOrUrl -match '^https?://') {
  $url = $PathOrUrl
} elseif ($PathOrUrl.StartsWith('/')) {
  $url = "$Host$PathOrUrl"
} else {
  $url = "$Host/$PathOrUrl"
}

$curlCmd = Get-Command curl.exe -ErrorAction SilentlyContinue
if (-not $curlCmd) {
  $curlCmd = Get-Command curl -CommandType Application -ErrorAction SilentlyContinue
}
if (-not $curlCmd) {
  throw 'curl is required but was not found on PATH.'
}

$curlArgList = @('-sS', '-X', $Method.ToUpperInvariant())
foreach ($h in $Header) {
  if (-not [string]::IsNullOrWhiteSpace($h)) {
    $curlArgList += @('-H', $h)
  }
}

if (-not [string]::IsNullOrWhiteSpace($BodyFile)) {
  if (-not (Test-Path -LiteralPath $BodyFile)) {
    throw "Body file not found: $BodyFile"
  }
  $curlArgList += @('--data-binary', "@$BodyFile")
} elseif (-not [string]::IsNullOrWhiteSpace($Body)) {
  $curlArgList += @('--data', $Body)
}

if ($CurlArgs) {
  $curlArgList += $CurlArgs
}

$curlArgList += $url

& $curlCmd.Source @curlArgList
