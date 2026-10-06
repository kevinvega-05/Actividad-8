# =============================================================================
#  Validación de la segmentación por VLAN (Windows PowerShell)
#  Ejecuta, desde dentro de los contenedores, lo que DEBE funcionar y lo que
#  DEBE estar bloqueado.   Uso:   powershell -ExecutionPolicy Bypass -File herramientas\validar_red.ps1
# =============================================================================
$ok = 0; $fallo = 0
function Prueba($desc, $esperado, $cont, $cmd) {
    docker exec $cont sh -c $cmd *> $null
    $r = if ($LASTEXITCODE -eq 0) { "si" } else { "no" }
    if ($r -eq $esperado) { $script:ok++; $m = "OK   "; $c = "Green" } else { $script:fallo++; $m = "FALLA"; $c = "Red" }
    Write-Host ("  [{0}] {1,-58} esperado={2,-2} obtenido={3}" -f $m, $desc, $esperado, $r) -ForegroundColor $c
}
# (comillas simples dentro: PowerShell 5.1 pierde las comillas dobles al llamar programas externos)
$TCP  = 'python3 -c ''import socket,sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=2)'''
$TCPP = 'python -c ''import socket,sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=2)'''

Write-Host "== Administración (VLAN 3) observa ambas zonas"
Prueba "monitor -> pista      ping 10.10.10.10"       si zv-monitor "ping -c1 -W2 10.10.10.10"
Prueba "monitor -> cliente3   ping 10.10.10.13"       si zv-monitor "ping -c1 -W2 10.10.10.13"
Prueba "monitor -> spot       ping 10.20.20.21"       si zv-monitor "ping -c1 -W2 10.20.20.21"
Prueba "monitor -> nao        HTTP 10.20.20.23:8080"  si zv-monitor "$TCP 10.20.20.23 8080"
Write-Host "== Aislamiento entre zonas (VLAN 1 <-> VLAN 2)"
Prueba "cliente1 -> spot      ping 10.20.20.21"       no zv-cliente1 "ping -c1 -W2 10.20.20.21"
Prueba "pista    -> pepper    HTTP 10.20.20.22:8080"  no zv-pista "$TCPP 10.20.20.22 8080"
Prueba "nao      -> pista     ping 10.10.10.10"       no zv-nao "ping -c1 -W2 10.10.10.10"
Prueba "spot     -> cliente2  HTTP 10.10.10.12:8080"  no zv-spot "$TCPP 10.10.10.12 8080"
Write-Host "== Las zonas solo pueden hablar con el broker MQTT del plano de administración"
Prueba "pista    -> broker    TCP 10.30.30.20:1883"   si zv-pista "$TCPP 10.30.30.20 1883"
Prueba "pepper   -> broker    TCP 10.30.30.20:1883"   si zv-pepper "$TCPP 10.30.30.20 1883"
Prueba "pista    -> monitor   HTTP 10.30.30.10:8000"  no zv-pista "$TCPP 10.30.30.10 8000"
Prueba "spot     -> monitor   ping 10.30.30.10"       no zv-spot "ping -c1 -W2 10.30.30.10"
Write-Host "== Intento de saltarse el enrutador usando la puerta de enlace de Docker (lo frena la guardia)"
Prueba "cliente1 -> spot vía 10.10.10.1 (no por el enrutador)" no zv-cliente1 `
  'ip route replace 10.20.20.0/24 via 10.10.10.1; ping -c1 -W2 10.20.20.21; r=$?; ip route replace 10.20.20.0/24 via 10.10.10.254; exit $r'
Write-Host "== Dentro de cada zona hay comunicación normal"
Prueba "cliente2 -> pista     ping 10.10.10.10"       si zv-cliente2 "ping -c1 -W2 10.10.10.10"
Prueba "spot     -> nao       ping 10.20.20.23"       si zv-spot "ping -c1 -W2 10.20.20.23"
Write-Host ""
Write-Host "Resultado: $ok correctas, $fallo con falla"
if ($fallo -gt 0) { exit 1 }
