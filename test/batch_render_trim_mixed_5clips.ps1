param(
    [int]$StartIndex = 1,
    [int]$Limit = 0,
    [switch]$DryRun,
    [switch]$KeepTemp
)

$scriptPath = "D:\VideoCut-Wrapper\test\batch_render_trim_mixed_5clips.py"

$arguments = @(
    $scriptPath,
    "--start-index", $StartIndex
)

if ($Limit -gt 0) {
    $arguments += @("--limit", $Limit)
}

if ($DryRun) {
    $arguments += "--dry-run"
}

if ($KeepTemp) {
    $arguments += "--keep-temp"
}

& python @arguments
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    throw "Batch render script failed with exit code: $exitCode"
}
