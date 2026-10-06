# =============================================================================
#  Construye y sube las imágenes del laboratorio a Docker Hub (Windows PowerShell)
#  Antes: edita .env (DOCKERHUB_USUARIO, TAG, QIBULLET_ACEPTO_LICENCIA=si)
#  Uso:   powershell -ExecutionPolicy Bypass -File herramientas\publicar_dockerhub.ps1
# =============================================================================
$ErrorActionPreference = "Stop"
if (-not (Test-Path .env)) { Write-Host "Falta el archivo .env (copia .env.example)." -ForegroundColor Red; exit 1 }
$usuario = (Select-String -Path .env -Pattern '^DOCKERHUB_USUARIO=(.*)$').Matches[0].Groups[1].Value.Trim()
if ($usuario -eq "" -or $usuario -eq "tuusuario") { Write-Host "Pon tu usuario real en DOCKERHUB_USUARIO dentro de .env" -ForegroundColor Red; exit 1 }

Write-Host "1/3  Iniciando sesión en Docker Hub como $usuario"
docker login -u $usuario
Write-Host "2/3  Construyendo imágenes"
docker compose build
Write-Host "3/3  Subiendo imágenes (zv-router, zv-guardia, zv-monitor, zv-pista, zv-cliente, zv-robot)"
docker compose push router guardia monitor pista cliente1 spot
Write-Host ""
Write-Host "Listo. Cualquiera puede usarlas sin construir con:" -ForegroundColor Green
Write-Host "  docker compose pull ; docker compose up -d --no-build"
