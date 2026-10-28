@echo off
set SESSION_DIR=%~dp0data\.session_colsanitas
echo Abriendo Chrome con sesion de Colsanitas...
echo Carpeta de sesion: %SESSION_DIR%
echo.
echo INSTRUCCIONES:
echo 1. Se abrira Chrome. Navega a: https://portal.colsanitas.com
echo 2. Inicia sesion con usuario: 1121926977.prest
echo 3. Si aparece pantalla de Radware CAPTCHA, resuelvela manualmente
echo 4. Asegurate de llegar al Validador de Derechos
echo 5. Cierra Chrome completamente (todas las ventanas)
echo 6. Ejecuta el script Comprimir_Sesion_Colsanitas.bat
echo.
pause

start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="%SESSION_DIR%" --no-first-run --no-default-browser-check "https://portal.colsanitas.com/sso/login?service=https%%3A%%2F%%2Fappcore.colsanitas.com%%2FValidadorDerechos%%2Fpages%%2Fgestion%%2FValidacionDerechos.seam%%3FflagConsulta%%3D1"
