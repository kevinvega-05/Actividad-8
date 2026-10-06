#!/bin/sh
# Construye y sube las imágenes a Docker Hub (Linux / macOS / Git Bash). Edita .env antes.
set -e
[ -f .env ] || { echo "Falta .env (copia .env.example)"; exit 1; }
USUARIO=$(sed -n 's/^DOCKERHUB_USUARIO=//p' .env | tr -d '\r ')
[ -n "$USUARIO" ] && [ "$USUARIO" != "tuusuario" ] || { echo "Pon tu usuario en DOCKERHUB_USUARIO (.env)"; exit 1; }
docker login -u "$USUARIO"
docker compose build
docker compose push router guardia monitor pista cliente1 spot
echo "Listo. Para usarlas sin construir:  docker compose pull && docker compose up -d --no-build"
