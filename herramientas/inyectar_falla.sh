#!/bin/sh
# Inyección de fallas de red (Linux / macOS / Git Bash). Ver inyectar_falla.ps1 para la explicación.
#   sh herramientas/inyectar_falla.sh nao 80 30 3      sh herramientas/inyectar_falla.sh nao quitar
c="zv-$1"; r=$2; j=${3:-0}; l=${4:-0}
case "$r" in
  quitar)   docker exec "$c" tc qdisc del dev eth0 root 2>/dev/null; echo "Red de $c normal."; exit 0;;
  caer)     docker stop "$c" >/dev/null; echo "$c detenido."; exit 0;;
  levantar) docker start "$c" >/dev/null; echo "$c iniciado."; exit 0;;
esac
a="delay ${r}ms ${j}ms"; [ "$l" != "0" ] && a="$a loss ${l}%"
docker exec "$c" sh -c "tc qdisc replace dev eth0 root netem $a" && echo "Aplicado a $c: netem $a"
