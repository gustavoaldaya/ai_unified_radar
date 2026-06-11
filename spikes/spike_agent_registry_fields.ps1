# SPIKE ADR-K23 eje 3 - campos reales del Agent Registry via Graph
# Requiere: PowerShell 7+, Microsoft.Graph.Authentication, rol AI Administrator
# Si 403: copiar el cuerpo del error - nombra permiso/licencia requerido (hallazgo C4)

$out = "C:\Claude_environment\projects\ai_unified_radar\spikes\output"
New-Item -ItemType Directory -Force -Path $out | Out-Null

Connect-MgGraph -Scopes "Directory.Read.All"
# Nota: el scope exacto de estos endpoints no esta documentado de forma estable;
# si devuelve 403 con un scope nombrado, anadirlo a -Scopes y reintentar.

# --- 1. Inventario (C1) ---
try {
  $pkgs = Invoke-MgGraphRequest -Method GET `
    -Uri "https://graph.microsoft.com/beta/copilot/admin/catalog/packages?`$top=100" `
    -OutputType PSObject
  $pkgs | ConvertTo-Json -Depth 10 | Out-File "$out\packages_list.json" -Encoding utf8NoBOM
  $pkgs.value | ForEach-Object { $_.PSObject.Properties.Name } |
    Sort-Object -Unique | Out-File "$out\packages_fields.txt" -Encoding utf8NoBOM
  Write-Host "C1: $($pkgs.value.Count) packages. Campos en packages_fields.txt"
} catch {
  $_ | Out-File "$out\packages_error.txt" -Encoding utf8NoBOM
  Write-Host "C1/C4: error en packages - ver packages_error.txt"
}

# --- 2. Detalle por package, uno por tipo (C2: manifest/instructions?) ---
if ($pkgs) {
  $typeProp = @("packageType","type","agentType") |
    Where-Object { $pkgs.value[0].PSObject.Properties.Name -contains $_ } |
    Select-Object -First 1
  $sample = if ($typeProp) { $pkgs.value | Group-Object $typeProp | ForEach-Object { $_.Group[0] } }
            else { $pkgs.value | Select-Object -First 5 }
  foreach ($p in $sample) {
    try {
      $d = Invoke-MgGraphRequest -Method GET `
        -Uri "https://graph.microsoft.com/beta/copilot/admin/catalog/packages/$($p.id)" `
        -OutputType PSObject
      $d | ConvertTo-Json -Depth 15 | Out-File "$out\package_detail_$($p.id).json" -Encoding utf8NoBOM
    } catch { "$($p.id): $($_.Exception.Message)" | Out-File "$out\package_detail_errors.txt" -Append -Encoding utf8NoBOM }
  }
}

# --- 3. Agent instances + agent cards (C3) ---
try {
  $inst = Invoke-MgGraphRequest -Method GET `
    -Uri "https://graph.microsoft.com/beta/agentRegistry/agentInstances" -OutputType PSObject
  $inst | ConvertTo-Json -Depth 10 | Out-File "$out\agent_instances.json" -Encoding utf8NoBOM
  foreach ($i in ($inst.value | Select-Object -First 10)) {
    try {
      $card = Invoke-MgGraphRequest -Method GET `
        -Uri "https://graph.microsoft.com/beta/agentRegistry/agentInstances/$($i.id)/agentCardManifest" `
        -OutputType PSObject
      $card | ConvertTo-Json -Depth 15 | Out-File "$out\agent_card_$($i.id).json" -Encoding utf8NoBOM
    } catch { "$($i.id): $($_.Exception.Message)" | Out-File "$out\agent_card_errors.txt" -Append -Encoding utf8NoBOM }
  }
} catch { $_ | Out-File "$out\agent_instances_error.txt" -Encoding utf8NoBOM }

Write-Host "Spike completado. Entregar a Claude: packages_fields.txt + 2-3 package_detail_*.json + 1-2 agent_card_*.json"
