@echo off
REM Builds EasyRecord.exe - one file you can double-click.
REM Needs: Windows + Python 3 (python.org, tick "Add to PATH" when installing).
cd /d "%~dp0"

REM Run python itself: a 'where' check also finds the Microsoft Store stub on PCs without Python.
python -c "import sys" >nul 2>nul || (
  echo Python is not installed. Get it from https://www.python.org/downloads/
  echo When installing, tick "Add python.exe to PATH".
  pause & exit /b 1
)

if not exist ffmpeg.exe (
  echo Downloading FFmpeg, the free recording engine...
  powershell -NoProfile -Command ^
    "$ProgressPreference='SilentlyContinue';" ^
    "Invoke-WebRequest https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip -OutFile ffmpeg.zip;" ^
    "Expand-Archive ffmpeg.zip -DestinationPath ffmpeg_tmp -Force;" ^
    "Copy-Item (Get-ChildItem ffmpeg_tmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1).FullName .;" ^
    "Remove-Item ffmpeg.zip, ffmpeg_tmp -Recurse -Force"
  if not exist ffmpeg.exe ( echo FFmpeg download failed. & pause & exit /b 1 )
)

REM The packing tool goes into a private .venv folder here, not into your main Python.
REM If the Python the .venv was made from is gone, make the .venv again.
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python -c "import sys" >nul 2>nul || rmdir /s /q .venv
)
if not exist .venv\Scripts\python.exe (
  echo Making a private Python folder for the packing tool...
  python -m venv .venv || ( pause & exit /b 1 )
)

echo Installing the packing tool...
.venv\Scripts\python -m pip install --quiet --upgrade pyinstaller || ( pause & exit /b 1 )

echo Building EasyRecord.exe...
.venv\Scripts\python -m PyInstaller --noconfirm --onefile --windowed --name EasyRecord ^
  --icon easyrecord.ico --add-data "easyrecord.ico;." --add-data "icon.png;." ^
  --add-binary "ffmpeg.exe;." easyrecord.py || ( pause & exit /b 1 )

echo.
echo Done! Your app is here: "%~dp0dist\EasyRecord.exe"
explorer dist
pause
