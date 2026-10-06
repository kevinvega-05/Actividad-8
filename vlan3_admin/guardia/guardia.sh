#!/bin/sh
# ===========================================================================
#  GUARDIA DE VLAN  (segunda capa de aislamiento, en el motor de Docker)
#
#  Cada VLAN es un bridge Linux en el motor Docker (br-vlan1, br-vlan2, br-vlan3).
#  El host podría encaminar paquetes de un bridge a otro si un contenedor usara
#  la puerta de enlace de Docker (10.x.x.1) en vez del enrutador inter-VLAN.
#  Esta guardia lo impide: en la cadena DOCKER-USER descarta todo paquete que
#  ENTRE por el bridge de una VLAN y SALGA por el de otra. Así el único camino
#  entre zonas es el enrutador (10.x.x.254), que aplica la política.
#
#  El tráfico que el enrutador reenvía no se ve afectado: para el host ese
#  tráfico entra y sale por el MISMO bridge. Los puertos publicados para las
#  ESP32 tampoco: entran por la interfaz del host, no por otro bridge.
# ===========================================================================
BRIDGES="br-vlan1 br-vlan2 br-vlan3"

elegir_iptables() {
  for ipt in iptables-nft iptables-legacy; do
    if $ipt -n -L DOCKER-USER >/dev/null 2>&1; then echo $ipt; return; fi
  done
}

echo "[guardia] esperando a que Docker cree los bridges de las VLAN..."
while true; do
  faltan=0
  for b in $BRIDGES; do ip link show "$b" >/dev/null 2>&1 || faltan=1; done
  IPT=$(elegir_iptables)
  [ $faltan -eq 0 ] && [ -n "$IPT" ] && break
  sleep 2
done
echo "[guardia] usando $IPT"

aplicar() {
  for a in $BRIDGES; do
    for b in $BRIDGES; do
      [ "$a" = "$b" ] && continue
      if ! $IPT -C DOCKER-USER -i "$a" -o "$b" -m comment --comment "zv-guardia" -j DROP 2>/dev/null; then
        $IPT -I DOCKER-USER -i "$a" -o "$b" -m comment --comment "zv-guardia" -j DROP
        echo "[guardia] bloqueado ruteo del host $a -> $b"
      fi
    done
  done
}

limpiar() {
  echo "[guardia] retirando reglas"
  while $IPT -S DOCKER-USER | grep -q zv-guardia; do
    regla=$($IPT -S DOCKER-USER | grep zv-guardia | head -n1 | sed 's/^-A /-D /')
    eval "$IPT $regla" || break
  done
  exit 0
}
trap limpiar TERM INT

# se re-aplica cada 15 s por si el motor de Docker se reinicia
while true; do
  aplicar
  sleep 15 &
  wait $!
done
