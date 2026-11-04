@echo off
REM ====================================================================
REM  PDF Editor - desktop GUI launcher
REM
REM  Double-click this file (or a shortcut to it on your Desktop) to
REM  start the application, exactly like running "python pdf_gui.py",
REM  but without leaving a terminal window open.
REM
REM  It launches the GUI with pythonw.exe, which starts the app with no
REM  console window attached.
REM ====================================================================

REM Work from the folder this script lives in so that ".venv" and
REM "pdf_gui.py" are found no matter where the shortcut is started from.
cd /d "%~dp0"

REM 1) Preferred: the project's own virtual environment (guaranteed match
REM    with the dependencies installed for this project).
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "pdf_gui.py"
    exit /b
)

REM 2) Fallback: a pythonw.exe available on PATH (still no console window).
where pythonw.exe >nul 2>nul
if not errorlevel 1 (
    start "" pythonw.exe "pdf_gui.py"
    exit /b
)

REM 3) Last resort: python.exe on PATH. This keeps a console window open
REM    so that any start-up error message stays visible.
where python.exe >nul 2>nul
if not errorlevel 1 (
    python "pdf_gui.py"
    exit /b
)

echo.
echo Could not find pythonw.exe or python.exe on PATH.
echo Install Python, or create the virtual environment first by running:
echo     py -m venv .venv
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
pause
