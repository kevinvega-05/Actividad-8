#!/bin/sh
# ===========================================================================
#  ENRUTADOR INTER-VLAN  (contenedor Alpine conectado a las 3 VLAN)
#
#    VLAN 1  Zona Gamer        10.10.10.0/24   enrutador 10.10.10.254
#    VLAN 2  Zona Robótica     10.20.20.0/24   enrutador 10.20.20.254
#    VLAN 3  Administración    10.30.30.0/24   enrutador 10.30.30.254
#
#  Política (cadena FORWARD, por defecto DROP):
#    1. Respuestas de conexiones ya establecidas            -> PERMITIR
#    2. Administración (VLAN 3) -> VLAN 1 y VLAN 2           -> PERMITIR (observar)
#    3. VLAN 1 / VLAN 2 -> broker MQTT 10.30.30.20:1883/tcp  -> PERMITIR (telemetría)
#    4. VLAN 1 <-> VLAN 2                                    -> BLOQUEAR (aislamiento)
#    5. Cualquier otra cosa                                  -> BLOQUEAR
#  NAT: la telemetría hacia el broker sale con la IP 10.30.30.254 (ver abajo)
# ===========================================================================
set -e

BROKER=10.30.30.20
NET_G=10.10.10.0/24
NET_R=10.20.20.0/24
NET_A=10.30.30.0/24

# Nombre de la interfaz que tiene una IP dada (Docker no garantiza el orden eth0/1/2)
iface_de() { ip -o -4 addr show | awk -v ip="$1" '$4 ~ "^"ip"/" {print $2}' | head -n1; }

echo "[router] esperando interfaces..."
for i in $(seq 1 30); do
  IF_G=$(iface_de 10.10.10.254); IF_R=$(iface_de 10.20.20.254); IF_A=$(iface_de 10.30.30.254)
  [ -n "$IF_G" ] && [ -n "$IF_R" ] && [ -n "$IF_A" ] && break
  sleep 1
done
echo "[router] VLAN1=$IF_G  VLAN2=$IF_R  VLAN3=$IF_A"

sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true
echo "[router] ip_forward=$(cat /proc/sys/net/ipv4/ip_forward)"

iptables -F FORWARD
iptables -P FORWARD DROP
iptables -N VLAN_STATS 2>/dev/null || iptables -F VLAN_STATS

# 1. respuestas
iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
# 2. administración observa ambas zonas
iptables -A FORWARD -i "$IF_A" -s $NET_A -o "$IF_G" -d $NET_G -m comment --comment ADMIN_A_VLAN1 -j ACCEPT
iptables -A FORWARD -i "$IF_A" -s $NET_A -o "$IF_R" -d $NET_R -m comment --comment ADMIN_A_VLAN2 -j ACCEPT
# 3. telemetría de las zonas hacia el broker (solo ese destino y puerto)
iptables -A FORWARD -i "$IF_G" -s $NET_G -o "$IF_A" -d $BROKER -p tcp --dport 1883 -m comment --comment VLAN1_A_MQTT -j ACCEPT
iptables -A FORWARD -i "$IF_R" -s $NET_R -o "$IF_A" -d $BROKER -p tcp --dport 1883 -m comment --comment VLAN2_A_MQTT -j ACCEPT
# 4. aislamiento entre zonas (se cuenta aparte para el tablero)
iptables -A FORWARD -i "$IF_G" -o "$IF_R" -m comment --comment BLOQ_VLAN1_A_VLAN2 -j DROP
iptables -A FORWARD -i "$IF_R" -o "$IF_G" -m comment --comment BLOQ_VLAN2_A_VLAN1 -j DROP
# 5. el resto (p. ej. una zona intentando entrar al monitor)
iptables -A FORWARD -m comment --comment BLOQ_OTROS -j DROP

# El broker no tiene ruta de regreso hacia las zonas (usa la puerta de enlace de
# Docker). Para que la respuesta vuelva por ESTE enrutador, la telemetría que va
# al broker sale con la IP del enrutador en la VLAN 3 (SNAT / masquerade).
iptables -t nat -F POSTROUTING
iptables -t nat -A POSTROUTING -o "$IF_A" -d $BROKER -p tcp --dport 1883 -j MASQUERADE

echo "[router] reglas aplicadas:"
iptables -nvL FORWARD --line-numbers | sed 's/^/[router]   /'

# ---------------------------------------------------------------------------
# Publica cada 2 s los contadores de cada regla en MQTT (lab/router)
# para que el plano de administración vea lo permitido y lo bloqueado.
# ---------------------------------------------------------------------------
while true; do
  JSON=$(iptables -nvxL FORWARD | awk '
    /\/\* / { match($0, /\/\* [A-Z0-9_]+ \*\//); c=substr($0, RSTART+3, RLENGTH-6);
              printf "%s\"%s\":{\"paquetes\":%s,\"bytes\":%s}", (n++?",":""), c, $1, $2 }')
  mosquitto_pub -h "$BROKER" -t lab/router -r \
    -m "{\"vivo\":true,\"t\":$(date +%s),\"interfaces\":{\"vlan1\":\"$IF_G\",\"vlan2\":\"$IF_R\",\"vlan3\":\"$IF_A\"},\"reglas\":{$JSON}}" \
    2>/dev/null || true
  sleep 2
done
