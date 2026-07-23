$ErrorActionPreference = "Stop"

$bridgeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $bridgeRoot

$venvPython = Join-Path $bridgeRoot ".venv\Scripts\python.exe"
$serverScript = Join-Path $bridgeRoot "mcp_server.py"

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating Python virtual environment..."
    & python -m venv (Join-Path $bridgeRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the Python virtual environment."
    }
}

Write-Host "Checking Python dependencies..."
& $venvPython -m pip install -r (Join-Path $bridgeRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Python dependencies."
}

Write-Host "Registering Codex MCP: math_modeling_knowledge"
& codex mcp remove math_modeling_knowledge 2>$null
& codex mcp add math_modeling_knowledge -- $venvPython $serverScript
if ($LASTEXITCODE -ne 0) {
    throw "Failed to register the Codex MCP server."
}

Write-Host "Running MCP protocol and nine-database checks..."
& $venvPython (Join-Path $bridgeRoot "test_mcp_protocol.py")
if ($LASTEXITCODE -ne 0) {
    throw "The MCP protocol test failed."
}

Write-Host "MCP installation and verification completed successfully."
Write-Host "Fully exit and restart Codex to load the two knowledge-base tools."
