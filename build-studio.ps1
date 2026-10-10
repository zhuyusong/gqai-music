param(
    [Parameter(Mandatory = $true)][string]$Aoci,
    [Parameter(Mandatory = $true)][string]$Browser,
    [string]$OutputName,
    [switch]$Check
)
$ErrorActionPreference = 'Stop'
$Builder = Join-Path (Split-Path -Parent $PSScriptRoot) 'client/build.ps1'
if (-not (Test-Path -LiteralPath $Builder -PathType Leaf)) {
    throw 'Build from the complete development workspace containing client/build.ps1 and the sibling projects.'
}
& $Builder @PSBoundParameters
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
