@echo off
echo [1/4] Installing packages...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto error
echo [2/4] Making icon...
python make_icon.py
if errorlevel 1 goto error
echo [3/4] Building app...
python -m PyInstaller --noconfirm --windowed --onedir --icon assets\icon.ico --add-data "assets;assets" --collect-all winrt --name QTextExtract app.py
if errorlevel 1 goto error
echo [3.5/4] Checking OCR parts inside the built app...
dist\QTextExtract\QTextExtract.exe --selftest selftest.txt
type selftest.txt
echo [4/4] Building installer...
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist %ISCC% (
  %ISCC% installer.iss
  if errorlevel 1 goto error
  echo Done! Installer: installer_output\QTextExtract-Setup.exe
) else (
  echo Inno Setup 6 not found. Install it from https://jrsoftware.org/isdl.php and run this file again.
  echo The app itself is ready: dist\QTextExtract\QTextExtract.exe
)
pause
exit /b 0
:error
echo Build failed. Check that Python is installed and added to PATH.
pause
exit /b 1
