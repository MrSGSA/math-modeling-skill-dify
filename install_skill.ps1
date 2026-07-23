$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = Join-Path $repoRoot "math-modeling"

if (-not (Test-Path -LiteralPath $source)) {
    throw "Skill source not found: $source"
}

$codexRoot = if ($env:CODEX_HOME) {
    $env:CODEX_HOME
} else {
    Join-Path $env:USERPROFILE ".codex"
}

$skillsRoot = Join-Path $codexRoot "skills"
$target = Join-Path $skillsRoot "math-modeling"

if (Test-Path -LiteralPath $target) {
    throw "Target already exists. Back it up or move it before installing: $target"
}

New-Item -ItemType Directory -Path $skillsRoot -Force | Out-Null
Copy-Item -LiteralPath $source -Destination $target -Recurse

Write-Host "Math-modeling skill installed to: $target"
Write-Host "Restart Codex to load the skill."
