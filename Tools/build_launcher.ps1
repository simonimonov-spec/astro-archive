[CmdletBinding(DefaultParameterSetName = 'Build')]
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [Parameter(ParameterSetName = 'Build')]
    [DateTimeOffset]$ReleaseCutoff = [DateTimeOffset]::UtcNow,
    [Parameter(ParameterSetName = 'Build')]
    [string]$ReleaseTag = 'beta1.3-preview',
    [Parameter(ParameterSetName = 'Build')]
    [long]$ReleaseId = 0,
    [Parameter(Mandatory, ParameterSetName = 'Verify')]
    [switch]$VerifyReleaseSignature
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath $Root).Path
$exe = Join-Path $Root 'AstroArchive.exe'

if ($VerifyReleaseSignature) {
    $signature = Get-AuthenticodeSignature -LiteralPath $exe
    if ($signature.Status -ne 'Valid' -or $signature.SignatureType -ne 'Authenticode') {
        throw "Release launcher requires a valid embedded Authenticode signature. Windows reported: $($signature.Status) ($($signature.SignatureType))."
    }
    if (-not $signature.TimeStamperCertificate) {
        throw 'Release launcher requires a timestamped Authenticode signature.'
    }
    Write-Output "Verified timestamped signature: $($signature.SignerCertificate.Subject)"
    return
}

$work = Join-Path $Root ('publication-review/launcher-build-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$allowlist = Get-Content -LiteralPath (Join-Path $Root 'Tools/publication-files.json') -Raw -Encoding utf8 | ConvertFrom-Json
$files = [ordered]@{}
foreach ($entry in $allowlist.repositories.'astro-archive') {
    if ($entry.destination -match '^(Backend/|Bridge/|UI/|start\.pyw$|requirements-lock\.txt$|Tools/(runtime_probe|apply_update)\.py$)') {
        $source = Join-Path $Root $entry.source
        $files[$entry.destination] = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$packages = @(Get-Content -LiteralPath (Join-Path $Root 'requirements-lock.txt') | Where-Object { $_ -match '^[a-zA-Z].*==' })
$cutoffUtc = $ReleaseCutoff.ToUniversalTime().ToString('o', [Globalization.CultureInfo]::InvariantCulture)
$manifest = @{ files = $files; packages = $packages; release_id = $ReleaseId; release_tag = $ReleaseTag; release_cutoff = $cutoffUtc }
$manifestPath = Join-Path $work 'manifest.json'
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$arguments = @('/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/debug-',
    "/out:$exe", '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll', '/r:System.Web.Extensions.dll',
    '/r:System.IO.Compression.dll', '/r:System.IO.Compression.FileSystem.dll',
    "/win32icon:$(Join-Path $Root 'UI/Shared/Web/Assets/AstroArchive.ico')",
    "/resource:$(Join-Path $Root 'UI/Shared/Web/Assets/AstroArchive.ico'),logo.ico",
    "/resource:$manifestPath,manifest.json",
    "/resource:$(Join-Path $Root 'Bridge/Desktop/startup-locales.json'),startup-locales.json",
    (Join-Path $Root 'Tools/Launcher/Launcher.cs'))
& $compiler @arguments
if ($LASTEXITCODE -ne 0) { throw 'Launcher compilation failed' }
Write-Output $exe
Write-Output "Embedded integrity checks: $($files.Count) files"
