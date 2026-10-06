# =============================================================================
#  Inyección de fallas de red para validar las métricas (Windows PowerShell)
#  Usa tc/netem dentro del contenedor para agregar retardo, jitter y pérdida.
#
#  Ejemplos:
#    .\herramientas\inyectar_falla.ps1 nao 80 30 3     # 80 ms ± 30 ms y 3 % de pérdida
#    .\herramientas\inyectar_falla.ps1 cliente2 0 0 20 # solo 20 % de pérdida
#    .\herramientas\inyectar_falla.ps1 nao quitar      # vuelve a la normalidad
#    .\herramientas\inyectar_falla.ps1 pepper caer     # detiene el contenedor
#    .\herramientas\inyectar_falla.ps1 pepper levantar # lo vuelve a iniciar
#
#  Observa el tablero (http://localhost:8000) y la ESP32 esclava: el LED del
#  contenedor pasa a parpadeo lento (DEGRADADO) o se apaga (CAÍDO).
# =============================================================================
param(
    [Parameter(Mandatory = $true)][string]$Contenedor,
    [Parameter(Mandatory = $true)][string]$Retardo,
    [int]$Jitter = 0,
    [double]$Perdida = 0
)
$c = "zv-$Contenedor"
switch ($Retardo) {
    "quitar"   { docker exec $c tc qdisc del dev eth0 root 2>$null; Write-Host "Red de $c normal."; exit 0 }
    "caer"     { docker stop $c | Out-Null; Write-Host "$c detenido."; exit 0 }
    "levantar" { docker start $c | Out-Null; Write-Host "$c iniciado."; exit 0 }
}
$args_tc = "delay ${Retardo}ms ${Jitter}ms"
if ($Perdida -gt 0) { $args_tc += " loss ${Perdida}%" }
docker exec $c sh -c "tc qdisc replace dev eth0 root netem $args_tc"
if ($LASTEXITCODE -eq 0) { Write-Host "Aplicado a ${c}: netem $args_tc" }
else { Write-Host "No se pudo aplicar netem (¿el kernel de Docker no trae sch_netem?)." -ForegroundColor Red }
