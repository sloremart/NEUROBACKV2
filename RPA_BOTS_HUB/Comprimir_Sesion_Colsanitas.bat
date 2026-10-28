@echo off
set SESSION_DIR=%~dp0data\.session_colsanitas
set ZIP_OUT=%~dp0data\session_colsanitas_nuevo.zip

echo Comprimiendo sesion de Colsanitas...

if not exist "%SESSION_DIR%" (
    echo ERROR: No existe la carpeta de sesion: %SESSION_DIR%
    pause
    exit /b 1
)

tar -a -c -f "%ZIP_OUT%" -C "%SESSION_DIR%" .

if exist "%ZIP_OUT%" (
    echo.
    echo ZIP creado exitosamente: %ZIP_OUT%
    echo.
    echo Ahora sube este archivo en: http://192.168.1.211:5055/upload_sesion
    explorer /select,"%ZIP_OUT%"
) else (
    echo ERROR: No se pudo crear el ZIP
)
pause
