@echo off
setlocal
cd /d "%~dp0"
python -m PyInstaller --noconfirm --onefile --windowed --name CALCMO-Pro --add-data "web;web" --add-data "assets;assets" run.py
echo.
echo Si PyInstaller n'est pas installe :
echo   python -m pip install pyinstaller
echo puis relancer build_exe.bat
pause
