param(
    [ValidateSet('Prepare', 'Api', 'Worker', 'Web', 'Check')]
    [string]$Mode = 'Check',
    [string]$PgRoot = "$env:LOCALAPPDATA\Temp\opencode\mealflow-pgvector16",
    [string]$PgUser = 'mealflow',
    [string]$CredentialFile = "$env:LOCALAPPDATA\Temp\opencode\mealflow-s7-vector-login.json"
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$python = Join-Path $repo '.venv\Scripts\python.exe'
$pgBin = Join-Path $PgRoot 'Library\bin'
$pgData = Join-Path $PgRoot 'pgdata'
$env:APP_ENV = 'development'
$env:DATABASE_URL = "postgresql+psycopg://${PgUser}@127.0.0.1:5434/mealflow_s7?gssencmode=disable"
$env:CORS_ORIGINS = 'http://localhost:5175,http://127.0.0.1:5175'
$env:VITE_API_PROXY_TARGET = 'http://127.0.0.1:8002'
$env:LLM_PROVIDER = 'fake'
$env:PYTHONUTF8 = '1'

# This is a user-owned portable PG16 cluster. Never control Windows services.
if ($Mode -eq 'Prepare') {
    & "$pgBin\pg_isready.exe" -h 127.0.0.1 -p 5434 -U $PgUser -d postgres *> $null
    if ($LASTEXITCODE -ne 0) {
        & "$pgBin\pg_ctl.exe" -D $pgData -l "$PgRoot\s7-postgres.log" -o '-h 127.0.0.1 -p 5434' -w start
        if ($LASTEXITCODE -ne 0) { throw 'Portable PG16 startup failed' }
    }
}

if ($Mode -in @('Prepare', 'Check')) {
    $identity = & "$pgBin\psql.exe" -X -w -h 127.0.0.1 -p 5434 -U $PgUser -d postgres -Atc "SELECT current_setting('server_version_num'), current_setting('data_directory');"
    if ($LASTEXITCODE -ne 0) { throw 'PG16 connection failed' }
    $parts = $identity.Split('|')
    if ([int]$parts[0] -lt 160000 -or [int]$parts[0] -ge 170000) { throw 'Port 5434 is not PG16' }
    if ([IO.Path]::GetFullPath($parts[1]) -ne [IO.Path]::GetFullPath($pgData)) {
        throw 'Port 5434 belongs to another PostgreSQL data directory'
    }
    Write-Host "PG16: 127.0.0.1:5434; data=$($parts[1]); database=mealflow_s7"
}

if ($Mode -eq 'Prepare') {
    $exists = & "$pgBin\psql.exe" -X -w -h 127.0.0.1 -p 5434 -U $PgUser -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='mealflow_s7';"
    if ($LASTEXITCODE -ne 0) { throw 'Database lookup failed' }
    if ($exists -ne '1') {
        & "$pgBin\createdb.exe" -w -h 127.0.0.1 -p 5434 -U $PgUser mealflow_s7
        if ($LASTEXITCODE -ne 0) { throw 'Database creation failed' }
    }
    Set-Location "$repo\apps\api"
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Migration failed' }
    # Seed is explicit; API/worker startup never calls it.
    & $python -m app.seed_s7_demo --credential-file $CredentialFile
    if ($LASTEXITCODE -ne 0) { throw 'Explicit S7 seed failed' }
    Write-Host "Credentials for a newly created account only: $CredentialFile (never printed)"
    Write-Host 'Next, open three terminals with this script: -Mode Api / -Mode Worker / -Mode Web'
    exit 0
}

if ($Mode -eq 'Api') {
    if (Get-NetTCPConnection -State Listen -LocalPort 8002 -ErrorAction SilentlyContinue) {
        $health = Invoke-RestMethod 'http://127.0.0.1:8002/api/v1/health'
        if ($health.status -ne 'ok') { throw 'Port 8002 occupied without healthy API' }
        Write-Host 'API port 8002 already healthy; no new process started. Use Check for DB verification.'
        exit 0
    }
    Set-Location "$repo\apps\api"
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port 8002
    exit $LASTEXITCODE
}

if ($Mode -eq 'Worker') {
    # Held by this foreground launcher for the whole worker lifetime.
    $mutex = New-Object System.Threading.Mutex($false, 'Local\MealFlowS7VectorWorker5434')
    if (-not $mutex.WaitOne(0)) { throw 'S7 worker launcher already running' }
    Set-Location "$repo\apps\api"
    Write-Host 'Worker: python -m app.worker; PG16 127.0.0.1:5434/mealflow_s7; Ctrl+C stops it'
    try { & $python -m app.worker } finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
    exit $LASTEXITCODE
}

if ($Mode -eq 'Web') {
    if (Get-NetTCPConnection -State Listen -LocalPort 5175 -ErrorAction SilentlyContinue) {
        $web = Invoke-WebRequest 'http://127.0.0.1:5175/' -UseBasicParsing
        if ($web.StatusCode -ne 200) { throw 'Port 5175 occupied without healthy web server' }
        Write-Host 'Web port 5175 already responding; no new process started'
        exit 0
    }
    Set-Location "$repo\apps\web"
    & npm.cmd run dev -- --host 127.0.0.1 --port 5175 --strictPort
    exit $LASTEXITCODE
}

if ($Mode -eq 'Check') {
    Set-Location "$repo\apps\api"
    & $python -c 'from app.db.schema_guard import ensure_schema_current; ensure_schema_current()'
    if ($LASTEXITCODE -ne 0) { throw 'Database health failed' }
    & "$pgBin\psql.exe" -X -w -h 127.0.0.1 -p 5434 -U $PgUser -d mealflow_s7 -c "SELECT extversion AS vector_version FROM pg_extension WHERE extname='vector'; SELECT count(*) AS ready_active_generations FROM index_generations WHERE active AND status='ready';"
    if ($LASTEXITCODE -ne 0) { throw 'Native index health failed' }
    $indexReady = & "$pgBin\psql.exe" -X -w -h 127.0.0.1 -p 5434 -U $PgUser -d mealflow_s7 -Atc "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector') AND (SELECT count(*) FROM index_generations WHERE active AND status='ready') = 1 AND (SELECT count(*) FROM knowledge_chunks WHERE embedding_vector IS NOT NULL AND search_tsv IS NOT NULL) >= 5;"
    if ($LASTEXITCODE -ne 0 -or $indexReady -ne 't') { throw 'Native index is not ready' }
    $health = Invoke-RestMethod 'http://127.0.0.1:8002/api/v1/health'
    if ($health.status -ne 'ok') { throw 'API health failed' }
    $web = Invoke-WebRequest 'http://127.0.0.1:5175/' -UseBasicParsing
    if ($web.StatusCode -ne 200) { throw 'Web health failed' }
    Write-Host 'API http://127.0.0.1:8002 healthy; web http://127.0.0.1:5175 healthy'
    Write-Host 'Database checks target PG16; PG17:5433 and processes on 8000/5173 are untouched.'
}
