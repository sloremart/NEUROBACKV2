@echo off
echo Exportando cookies de Colsanitas...
cd /d "%~dp0"
python Exportar_Cookies_Colsanitas.py
echo.
echo Listo. Sube el archivo data\colsanitas_state.json en:
echo http://192.168.1.211:5055/upload_sesion
pause
