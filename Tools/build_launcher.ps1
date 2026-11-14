param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [DateTimeOffset]$ReleaseCutoff = [DateTimeOffset]::UtcNow,
    [string]$ReleaseTag = 'beta1.2.1-preview',
    [long]$ReleaseId = 0
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath $Root).Path
$work = Join-Path $Root ('publication-review/launcher-build-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$allowlist = Get-Content -LiteralPath (Join-Path $Root 'Tools/publication-files.json') -Raw -Encoding utf8 | ConvertFrom-Json
$files = [ordered]@{}
foreach ($entry in $allowlist.repositories.'astro-archive') {
    if ($entry.destination -match '^(Backend/|Bridge/|UI/|start\.pyw$|requirements-lock\.txt$|Tools/runtime_probe\.py$)') {
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
$exe = Join-Path $Root 'AstroArchive.exe'
$arguments = @('/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/debug-',
    "/out:$exe", '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll', '/r:System.Web.Extensions.dll',
    '/r:System.IO.Compression.dll', '/r:System.IO.Compression.FileSystem.dll',
    "/win32icon:$(Join-Path $Root 'UI/Shared/Web/Assets/AstroArchive.ico')",
    "/resource:$(Join-Path $Root 'UI/Shared/Web/Assets/AstroArchive.ico'),logo.ico",
    "/resource:$manifestPath,manifest.json", (Join-Path $Root 'Tools/Launcher/Launcher.cs'))
& $compiler @arguments
if ($LASTEXITCODE -ne 0) { throw 'Launcher compilation failed' }
Write-Output $exe
Write-Output "Embedded integrity checks: $($files.Count) files"
