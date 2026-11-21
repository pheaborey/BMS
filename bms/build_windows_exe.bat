@echo off
setlocal
REM Build the BoreyBMS Windows setup executable (Python 3.10+ and Inno Setup 6).
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :failed

py -3 -m PyInstaller --clean --noconfirm BoreyBMS.spec
if errorlevel 1 goto :failed

set "ISCC="
for /f "delims=" %%I in ('where ISCC.exe 2^>nul') do if not defined ISCC set "ISCC=%%I"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
  echo Inno Setup 6 was not found. Install it, then run this script again.
  exit /b 1
)

"%ISCC%" BoreyBMS.iss
if errorlevel 1 goto :failed
echo.
echo Built dist\BoreyBMS-Setup.exe
echo The installer includes the application and its Python dependencies.
exit /b 0

:failed
echo.
echo Build failed. See the error above.
exit /b 1
