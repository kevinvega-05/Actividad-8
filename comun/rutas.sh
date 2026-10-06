#!/bin/sh
# ---------------------------------------------------------------------------
# Punto de entrada de los contenedores de simulación.
# Agrega las rutas hacia las OTRAS VLAN a través del enrutador inter-VLAN
# (su IP en esta VLAN viene en $ROUTER_IP) y luego arranca el programa.
#
#   VLAN 1 (gamer)    10.10.10.0/24
#   VLAN 2 (robótica) 10.20.20.0/24
#   VLAN 3 (admin)    10.30.30.0/24
#
# Así el tráfico entre zonas pasa SIEMPRE por el enrutador, que es quien
# decide qué se permite (administración -> zonas, zonas -> broker MQTT) y
# qué se bloquea (VLAN 1 <-> VLAN 2).
# ---------------------------------------------------------------------------
set -e
if [ -n "$ROUTER_IP" ]; then
  for red in 10.10.10.0/24 10.20.20.0/24 10.30.30.0/24; do
    # no tocar la red propia (ya es directamente conectada)
    if ! ip route show | grep -q "^$red dev"; then
      ip route replace "$red" via "$ROUTER_IP" || echo "[rutas] no se pudo agregar $red"
    fi
  done
  echo "[rutas] tabla de rutas:"
  ip route show | sed 's/^/[rutas]   /'
fi
exec "$@"
