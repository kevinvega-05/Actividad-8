#!/bin/sh
# =============================================================================
#  Validación de la segmentación (Linux / macOS / Git Bash en Windows)
#  Ejecuta, desde dentro de los contenedores, lo que DEBE funcionar y lo que
#  DEBE estar bloqueado por el enrutador inter-VLAN.   Uso:  sh herramientas/validar_red.sh
# =============================================================================
ok=0; fallo=0
prueba() {  # prueba <descripción> <esperado: si|no> <contenedor> <comando...>
  desc=$1; esperado=$2; cont=$3; shift 3
  if docker exec "$cont" sh -c "$*" >/dev/null 2>&1; then r=si; else r=no; fi
  if [ "$r" = "$esperado" ]; then ok=$((ok+1)); marca="OK   "; else fallo=$((fallo+1)); marca="FALLA"; fi
  printf "  [%s] %-58s esperado=%-2s obtenido=%s\n" "$marca" "$desc" "$esperado" "$r"
}
TCP="python3 -c 'import socket,sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=2)'"
TCPP="python -c 'import socket,sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=2)'"

echo "== Administración (VLAN 3) observa ambas zonas"
prueba "monitor -> pista      ping 10.10.10.10"       si zv-monitor "ping -c1 -W2 10.10.10.10"
prueba "monitor -> cliente3   ping 10.10.10.13"       si zv-monitor "ping -c1 -W2 10.10.10.13"
prueba "monitor -> spot       ping 10.20.20.21"       si zv-monitor "ping -c1 -W2 10.20.20.21"
prueba "monitor -> nao        HTTP 10.20.20.23:8080"  si zv-monitor "$TCP 10.20.20.23 8080"
echo "== Aislamiento entre zonas (VLAN 1 <-> VLAN 2)"
prueba "cliente1 -> spot      ping 10.20.20.21"       no zv-cliente1 "ping -c1 -W2 10.20.20.21"
prueba "pista    -> pepper    HTTP 10.20.20.22:8080"  no zv-pista "$TCPP 10.20.20.22 8080"
prueba "nao      -> pista     ping 10.10.10.10"       no zv-nao "ping -c1 -W2 10.10.10.10"
prueba "spot     -> cliente2  HTTP 10.10.10.12:8080"  no zv-spot "$TCPP 10.10.10.12 8080"
echo "== Las zonas solo pueden hablar con el broker MQTT del plano de administración"
prueba "pista    -> broker    TCP 10.30.30.20:1883"   si zv-pista "$TCPP 10.30.30.20 1883"
prueba "pepper   -> broker    TCP 10.30.30.20:1883"   si zv-pepper "$TCPP 10.30.30.20 1883"
prueba "pista    -> monitor   HTTP 10.30.30.10:8000"  no zv-pista "$TCPP 10.30.30.10 8000"
prueba "spot     -> monitor   ping 10.30.30.10"       no zv-spot "ping -c1 -W2 10.30.30.10"
echo "== Intento de saltarse el enrutador usando la puerta de enlace de Docker (lo frena la guardia)"
prueba "cliente1 -> spot vía 10.10.10.1 (no por el enrutador)" no zv-cliente1 \
  "ip route replace 10.20.20.0/24 via 10.10.10.1; ping -c1 -W2 10.20.20.21; r=\$?; ip route replace 10.20.20.0/24 via 10.10.10.254; exit \$r"
echo "== Dentro de cada zona hay comunicación normal"
prueba "cliente2 -> pista     ping 10.10.10.10"       si zv-cliente2 "ping -c1 -W2 10.10.10.10"
prueba "spot     -> nao       ping 10.20.20.23"       si zv-spot "ping -c1 -W2 10.20.20.23"
echo
echo "Resultado: $ok correctas, $fallo con falla"
[ "$fallo" -eq 0 ]
