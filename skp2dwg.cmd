@echo off
setlocal
if exist "%~dp0.venv\Scripts\skp2dwg.exe" (
  "%~dp0.venv\Scripts\skp2dwg.exe" %*
) else (
  python -m skp2dwg %*
)
