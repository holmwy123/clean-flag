@echo off
setlocal
cd /d "%~dp0"
title Clean Flag - uninstall
call :findpy
if not defined PY ( echo Python wasn't found. & pause & exit /b 1 )
echo This puts the game's original voice files and subtitles back.
echo Close the game, then press a key.
pause >nul
%PY% -m cleanflag remove
echo.
pause
exit /b

:findpy
set PY=
py -3 --version >nul 2>nul && (set "PY=py -3" & exit /b)
python --version >nul 2>nul && (set "PY=python" & exit /b)
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set PY="%%D\python.exe"
exit /b
