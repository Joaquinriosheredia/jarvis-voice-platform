@echo off
rem Lanza instalar.ps1 sin cambiar la política de ejecución del sistema. Acepta sus mismos
rem parámetros: instalar.cmd -SinDescargas, instalar.cmd -SinConfigurar, instalar.cmd -Secretos <carpeta>
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1" %*
