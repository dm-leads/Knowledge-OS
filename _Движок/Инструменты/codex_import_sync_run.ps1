param(
    [Parameter(Mandatory=$true)][string]$ThreadId,
    [string]$CodexHome = (Join-Path $env:USERPROFILE '.codex')
)
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'codex_import_sync.py'
$codexPackage = Get-AppxPackage -Name 'OpenAI.Codex' | Sort-Object Version -Descending | Select-Object -First 1
if (-not $codexPackage) { throw 'Installed Codex package not found.' }
$nativeExe = Join-Path $codexPackage.InstallLocation 'app\resources\codex.exe'
if (-not (Test-Path -LiteralPath $nativeExe -PathType Leaf)) { throw "Native Codex executable missing: $nativeExe" }
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) { throw "Repair script missing: $scriptPath" }
$backupPath = Join-Path $env:LOCALAPPDATA ('CodexImportRepair\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
Write-Host 'Pause the source Claude Code session. Keep Codex fully closed until this window reports completed.'
Write-Host "Codex version: $($codexPackage.Version)"
Write-Host "Backup and report: $backupPath"
$consoleLog = $backupPath + '.console.log'
$savedErrorPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
& python $scriptPath repair --home $CodexHome --exe $nativeExe --thread $ThreadId --output $backupPath --wait-seconds 600 2>&1 | ForEach-Object {
    $lineText = $_.ToString()
    Write-Host $lineText
    Add-Content -LiteralPath $consoleLog -Value $lineText -Encoding UTF8
}
$repairExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $savedErrorPreference
}
Write-Host "Console log: $consoleLog"
$reportPath = Join-Path $backupPath 'report.json'
if (Test-Path -LiteralPath $reportPath) {
    $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Write-Host "Status: $($report.status)"
    if ($repairExit -eq 0 -and $report.status -eq 'completed') {
        Write-Host "Added records: $($report.added_response_items). You can reopen Codex."
    } else {
        Write-Host 'Keep the backup. Do not rerun or restore files blindly. Report needs inspection.'
    }
}
Read-Host 'Press Enter to close this window'
