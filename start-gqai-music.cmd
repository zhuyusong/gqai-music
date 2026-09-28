@echo off
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%.venv\Scripts\python.exe" (
  echo Please run install-local.ps1 first.
  exit /b 1
)
"%ROOT%.venv\Scripts\python.exe" -m gqai_music

