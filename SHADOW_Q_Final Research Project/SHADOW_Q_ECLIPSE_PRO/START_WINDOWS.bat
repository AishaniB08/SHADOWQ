@echo off
cd /d "%~dp0"
echo =====================================================
echo    SHADOW-Q ECLIPSE - QUANTUM RESEARCH OS
 echo =====================================================
if not exist ".venv\Scripts\python.exe" (
 py -3 -m venv .venv
 if errorlevel 1 (echo Python 3 not found & pause & exit /b 1)
)
".venv\Scripts\python.exe" -c "import qiskit, qiskit_aer, numpy" >nul 2>&1
if errorlevel 1 (
 echo Installing Qiskit dependencies. Internet access required on first run.
 ".venv\Scripts\python.exe" -m pip install -r requirements.txt
 if errorlevel 1 (echo Installation failed. See README for offline reuse of previous environment. & pause & exit /b 1)
)
echo Open http://localhost:8771
".venv\Scripts\python.exe" server.py --port 8771
pause
