param(
    [ValidateSet('Check', 'Upgrade', 'Import', 'ImportSkills', 'ImportStructured', 'Api', 'Worker', 'Stop')]
    [string]$Mode = 'Check',
    [string]$Source,
    [string]$EmbeddingModel = 'BAAI/bge-small-zh-v1.5',
    [switch]$LocalFilesOnly,
    [string]$Python = 'D:\APP\miniconda3\envs\mealflow\python.exe',
    [string]$PgRoot = "$env:LOCALAPPDATA\Temp\opencode\mealflow-rag-pg16",
    [string]$PasswordFile = "$env:LOCALAPPDATA\Temp\opencode\mealflow-rag-pg16-password.txt"
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$pgBin = Join-Path (Split-Path $Python -Parent) 'Library\bin'
$env:PATH = "$pgBin;$env:PATH"
$env:PGPASSWORD = [IO.File]::ReadAllText($PasswordFile).Trim()
$env:DATABASE_URL = 'postgresql+psycopg://mealflow@127.0.0.1:5434/mealflow_rag?gssencmode=disable'
$env:APP_ENV = 'development'
$env:PYTHONUTF8 = '1'
$env:RETRIEVAL_MODE = 'native'
$env:RETRIEVAL_EMBEDDING_MODEL = $EmbeddingModel
$env:RETRIEVAL_EMBEDDING_DIMENSIONS = '512'
$env:RETRIEVAL_TEMPLATE_VERSION = 'bge-zh-query-v1'
$env:RETRIEVAL_TOKENIZER_VERSION = 'v1'
if ($LocalFilesOnly) {
    $env:RETRIEVAL_EMBEDDING_LOCAL_FILES_ONLY = 'true'
} else {
    $env:RETRIEVAL_EMBEDDING_LOCAL_FILES_ONLY = 'false'
}

if ($Mode -eq 'Stop') {
    & "$pgBin\pg_ctl.exe" -D "$PgRoot\pgdata" -m fast -w stop
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL stop failed' }
    exit 0
}

& "$pgBin\pg_isready.exe" -h 127.0.0.1 -p 5434 -U mealflow -d postgres *> $null
if ($LASTEXITCODE -ne 0) {
    & "$pgBin\pg_ctl.exe" -D "$PgRoot\pgdata" -l "$PgRoot\postgres.log" -o '-h 127.0.0.1 -p 5434' -w start
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed' }
}
$actualData = & "$pgBin\psql.exe" -X -w -h 127.0.0.1 -p 5434 -U mealflow -d postgres -Atc 'SHOW data_directory'
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL identity check failed' }
if ([IO.Path]::GetFullPath($actualData) -ne [IO.Path]::GetFullPath("$PgRoot\pgdata")) {
    throw 'Port 5434 belongs to a different PostgreSQL cluster'
}

Push-Location "$repo\apps\api"
try {
    if ($Mode -eq 'Api') {
        & $Python -m uvicorn app.main:app --reload --port 8000
    } elseif ($Mode -eq 'Worker') {
        & $Python -m app.worker
    } elseif ($Mode -eq 'Upgrade') {
        & $Python -m alembic upgrade head
    } elseif ($Mode -eq 'Import') {
        if (-not $Source) { throw '-Source is required for Import' }
        & $Python "$repo\scripts\import_howtocook_rag.py" --source $Source --apply
    } elseif ($Mode -eq 'ImportSkills') {
        if (-not $Source) { throw '-Source is required for ImportSkills' }
        & $Python "$repo\scripts\import_cooking_skills.py" --source $Source --apply
    } elseif ($Mode -eq 'ImportStructured') {
        if (-not $Source) { throw '-Source is required for ImportStructured' }
        & $Python "$repo\scripts\import_structured_food_knowledge.py" --source $Source --apply
    } else {
        & $Python -m alembic current
        if ($LASTEXITCODE -ne 0) { throw 'Migration status check failed' }
        & "$pgBin\psql.exe" -X -w -h 127.0.0.1 -p 5434 -U mealflow -d mealflow_rag -c "SELECT extversion FROM pg_extension WHERE extname='vector'; SELECT count(*) AS recipes FROM recipes; SELECT count(*) AS documents FROM knowledge_documents; SELECT count(*) AS active_chunks FROM knowledge_chunks c JOIN index_generations g ON g.id=c.generation_id WHERE g.active AND c.embedding_vector IS NOT NULL;"
    }
    if ($LASTEXITCODE -ne 0) { throw "$Mode failed" }
} finally {
    Pop-Location
}
