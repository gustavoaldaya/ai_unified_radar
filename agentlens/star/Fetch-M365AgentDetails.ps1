<#
.SYNOPSIS
    Descarga los detalles de todos los `copilotPackage` del catálogo Agent 365
    a un fichero JSON, con retry+backoff+checkpoint incremental.

.DESCRIPTION
    Alimenta `agentlens/star/m365_agents.json` que consume `build_star_pg.py`.

    Maneja el 424/429 "Too Many Requests" del endpoint
    `/beta/copilot/admin/catalog/packages/{id}`, guarda progreso cada N agentes
    (por defecto 100), y permite reanudar tras un corte parcial (--Resume).

.PARAMETER OutputPath
    Fichero JSON de salida. Default: star\m365_agents.json (relativo al cwd).

.PARAMETER SleepMs
    Pausa entre requests exitosos, en milisegundos. Default: 200 (5 req/s).

.PARAMETER MaxRetries
    Reintentos por request antes de rendirse. Default: 6 (backoff hasta ~32s).

.PARAMETER SaveEvery
    Cada cuántos agentes se hace checkpoint a disco (write-atomic). Default: 100.

.PARAMETER MaxAgents
    Si >0, solo descarga los primeros N agentes. Útil para PoC/smoke test.

.PARAMETER Resume
    Si se pasa, lee OutputPath existente y salta los `id` ya presentes.

.PARAMETER FailuresPath
    Log de IDs que no se pudieron descargar tras agotar los reintentos.
    Default: star\m365_agents.failures.txt.

.EXAMPLE
    # Smoke test con 20 agentes primero
    .\Fetch-M365AgentDetails.ps1 -MaxAgents 20

.EXAMPLE
    # Descarga completa (~1883 agentes; ~7-10 min con SleepMs=200)
    .\Fetch-M365AgentDetails.ps1

.EXAMPLE
    # Reanudar tras un corte
    .\Fetch-M365AgentDetails.ps1 -Resume
#>

[CmdletBinding()]
param(
    [string]$OutputPath = "star\m365_agents.json",
    [int]$SleepMs = 200,
    [int]$MaxRetries = 6,
    [int]$SaveEvery = 100,
    [int]$MaxAgents = 0,
    [switch]$Resume,
    [string]$FailuresPath = "star\m365_agents.failures.txt"
)

$ErrorActionPreference = 'Stop'

# ---------- Preflight ----------

try {
    $ctx = Get-MgContext
    if (-not $ctx) { throw "No Graph context" }
} catch {
    Write-Error "No estás conectado a Graph. Ejecuta primero: Connect-MgGraph -Scopes 'CopilotPackages.Read.All'"
    exit 1
}
if ($ctx.Scopes -notcontains 'CopilotPackages.Read.All') {
    Write-Warning "El contexto actual NO incluye CopilotPackages.Read.All (scopes: $($ctx.Scopes -join ', ')). Reconecta si obtienes 403."
}

# Asegura la carpeta destino
$outDir = Split-Path -Parent $OutputPath
if ($outDir -and -not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }

# ---------- Helpers ----------

function Save-Progress {
    param([Parameter(Mandatory)][object[]]$Data,
          [Parameter(Mandatory)][string]$Path)
    # write-atomic: temp file + Move-Item -Force
    $tmp = "$Path.tmp"
    # -Depth 20 preserva el JSON anidado de elementDetails.elements.definition
    ,$Data | ConvertTo-Json -Depth 20 | Out-File -Encoding utf8 $tmp
    Move-Item -Force $tmp $Path
}

function Is-ThrottleError {
    param($ErrorRecord)
    $status = 0
    try { $status = [int]$ErrorRecord.Exception.Response.StatusCode } catch {}
    if ($status -in @(408, 424, 429, 500, 502, 503, 504)) { return $true }
    $msg = "$($ErrorRecord.Exception.Message) $($ErrorRecord.ErrorDetails.Message)"
    return ($msg -match 'Too Many Requests' -or $msg -match 'Failed Dependency')
}

# ---------- 1) Listar todos los package IDs (con paginación) ----------

Write-Host "Listando package IDs del catálogo..." -ForegroundColor Cyan
$ids = New-Object System.Collections.Generic.List[string]
$next = "https://graph.microsoft.com/beta/copilot/admin/catalog/packages"
while ($next) {
    $resp = Invoke-MgGraphRequest -Method GET $next
    foreach ($item in $resp.value) { $ids.Add($item.id) }
    $next = $resp.'@odata.nextLink'
}
Write-Host "Total IDs encontrados: $($ids.Count)" -ForegroundColor Green

if ($MaxAgents -gt 0 -and $ids.Count -gt $MaxAgents) {
    $ids = [System.Collections.Generic.List[string]]($ids | Select-Object -First $MaxAgents)
    Write-Host "Limitando a los primeros $MaxAgents" -ForegroundColor Yellow
}

# ---------- 2) Reanudar si corresponde ----------

$results = New-Object System.Collections.Generic.List[object]
$done = @{}

if ($Resume -and (Test-Path $OutputPath)) {
    Write-Host "Modo Resume: leyendo $OutputPath existente..." -ForegroundColor Cyan
    $existing = @(Get-Content $OutputPath -Raw | ConvertFrom-Json)
    foreach ($e in $existing) {
        $results.Add($e)
        if ($e.id) { $done[$e.id] = $true }
    }
    Write-Host "  Ya presentes: $($results.Count) agentes" -ForegroundColor Gray
}

$pending = @($ids | Where-Object { -not $done.ContainsKey($_) })
Write-Host "Pendientes por descargar: $($pending.Count)" -ForegroundColor Green

if ($pending.Count -eq 0) {
    Write-Host "Nada que hacer." -ForegroundColor Yellow
    exit 0
}

# ---------- 3) Descarga con retry+backoff+checkpoint ----------

$failed = New-Object System.Collections.Generic.List[string]
$startTime = Get-Date
$i = 0

foreach ($id in $pending) {
    $i++
    $url = "https://graph.microsoft.com/beta/copilot/admin/catalog/packages/$id"

    $ok = $false
    for ($attempt = 1; $attempt -le $MaxRetries -and -not $ok; $attempt++) {
        try {
            $detail = Invoke-MgGraphRequest -Method GET $url
            $results.Add($detail)
            $ok = $true
        } catch {
            if (Is-ThrottleError $_) {
                # backoff exponencial con jitter: 2^(n-1) + random(0..500ms)
                $waitS = [Math]::Min(32, [Math]::Pow(2, $attempt - 1))
                $waitMs = [int]($waitS * 1000) + (Get-Random -Minimum 0 -Maximum 500)
                Write-Warning ("[{0}/{1}] {2}  intento {3}/{4}, esperando {5}ms" -f `
                    $i, $pending.Count, $id, $attempt, $MaxRetries, $waitMs)
                Start-Sleep -Milliseconds $waitMs
            } else {
                Write-Error "[$i/$($pending.Count)] $id  fallo NO-throttle: $($_.Exception.Message)"
                $failed.Add($id)
                break
            }
        }
    }
    if (-not $ok) {
        Write-Warning "[$i/$($pending.Count)] $id  agotados los reintentos, saltando"
        $failed.Add($id)
    }

    Start-Sleep -Milliseconds $SleepMs

    if ($i % $SaveEvery -eq 0) {
        Save-Progress -Data $results.ToArray() -Path $OutputPath
        $elapsed = (Get-Date) - $startTime
        $rate = [Math]::Round($i / [Math]::Max($elapsed.TotalSeconds, 1), 2)
        Write-Host ("[{0}/{1}] Checkpoint: {2} agentes guardados, {3} fallos, {4} req/s" -f `
            $i, $pending.Count, $results.Count, $failed.Count, $rate) -ForegroundColor Cyan
    }
}

# ---------- 4) Guardado final ----------

Save-Progress -Data $results.ToArray() -Path $OutputPath

if ($failed.Count -gt 0) {
    $failed | Out-File -Encoding utf8 $FailuresPath
    Write-Warning "$($failed.Count) IDs fallaron. Ver: $FailuresPath (relanza con -Resume para reintentar solo esos)."
}

$totalElapsed = (Get-Date) - $startTime
Write-Host ""
Write-Host "=== HECHO ===" -ForegroundColor Green
Write-Host "  Agentes en $OutputPath : $($results.Count)"
Write-Host "  Fallos                  : $($failed.Count)"
Write-Host "  Tiempo total            : $($totalElapsed.ToString('mm\:ss'))"
