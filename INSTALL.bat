@echo off
setlocal
cd /d "%~dp0"
title Clean Flag - install
echo ============================================================
echo  Clean Flag for Assassin's Creed Black Flag Resynced
echo  Removes swearing from the English voice lines and subtitles.
echo ============================================================
echo.
call :findpy
if not defined PY (
  echo Python is needed and isn't installed. Installing it now with winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  call :findpy
)
if not defined PY (
  echo.
  echo Couldn't install Python automatically.
  echo Install it from https://www.python.org/downloads/ - tick "Add python.exe to PATH" -
  echo then run this again.
  pause & exit /b 1
)
echo Close the game (and Ubisoft Connect's "Verify files", if running), then press a key.
pause >nul
echo.
echo Getting the audio helpers (one time, small)...
%PY% -m pip install --user --quiet --disable-pip-version-check numpy av
echo.
%PY% -m cleanflag setup
echo.
pause
exit /b

:findpy
set PY=
py -3 --version >nul 2>nul && (set "PY=py -3" & exit /b)
python --version >nul 2>nul && (set "PY=python" & exit /b)
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set PY="%%D\python.exe"
exit /b
