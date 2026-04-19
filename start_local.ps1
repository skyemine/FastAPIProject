param(
    [string]$Host = "127.0.0.1",
    [int]$Port = 8000
)

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Error "Virtual environment not found. Create it first or restore .venv."
    exit 1
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Update SECRET_KEY before public deploy."
}

Get-Content ".env" | ForEach-Object {
    if ($_ -match '^\s*#' -or $_ -notmatch '=') {
        return
    }
    $parts = $_ -split '=', 2
    [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
}

.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host $Host --port $Port
