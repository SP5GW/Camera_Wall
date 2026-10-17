@echo off
rem CamWall for Windows 10/11. Edit cameras.yaml / .env, then run this again.
cd /d "%~dp0"

set PY=python
where python >nul 2>nul || set PY=py

%PY% -c "import yaml" 2>nul || (
  echo Installing PyYAML...
  %PY% -m pip install --user pyyaml
)

%PY% camwall.py %*
pause
