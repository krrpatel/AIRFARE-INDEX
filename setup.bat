@echo off
setlocal
cd /d "%~dp0"
echo ========================================
echo  Airfare Price Index Setup
echo ========================================

where python >nul 2>&1
if errorlevel 1 (
  echo Python was not found. Install Python 3.11 or newer and try again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" python -m venv .venv
call ".venv\Scripts\activate.bat"
echo Updating Python tooling...
python -m pip install --upgrade pip wheel setuptools
echo Installing application dependencies...
pip install -r requirements.txt
pip install -r requirements-scrapers.txt

echo Verifying Python and Selenium...
python --version
python -c "import selenium; print('Selenium', selenium.__version__)"

set "CHROME_EXE="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not defined CHROME_EXE if exist "%ProgramFiles%\Chromium\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles%\Chromium\Application\chrome.exe"
if not defined CHROME_EXE if exist "%LocalAppData%\Chromium\Application\chrome.exe" set "CHROME_EXE=%LocalAppData%\Chromium\Application\chrome.exe"
if not defined CHROME_EXE for /f "delims=" %%C in ('where chrome 2^>nul') do if not defined CHROME_EXE set "CHROME_EXE=%%C"
if not defined CHROME_EXE for /f "delims=" %%C in ('where chromium 2^>nul') do if not defined CHROME_EXE set "CHROME_EXE=%%C"
if not defined CHROME_EXE (
  echo Chrome or Chromium was not found. Install one before running Ixigo.
  pause
  exit /b 1
)
set CHROME_BINARY=%CHROME_EXE%
echo Browser: %CHROME_EXE%
"%CHROME_EXE%" --version

echo Checking Selenium Manager and Chrome startup...
python -c "from selenium import webdriver; from selenium.webdriver.chrome.options import Options; o=Options(); o.add_argument('--headless=new'); o.add_argument('--no-sandbox'); o.add_argument('--disable-dev-shm-usage'); o.add_argument('--disable-gpu'); d=webdriver.Chrome(options=o); print('Selenium Manager: SUCCESS'); print('Browser', d.capabilities.get('browserVersion')); d.quit()"
if errorlevel 1 (
  echo Selenium could not start Chrome. Check the Chrome installation and try again.
  pause
  exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
  echo npm was not found. Install Node.js LTS before setting up the frontend.
  pause
  exit /b 1
)
echo Node: & node --version
echo npm: & npm --version
if not exist "frontend\node_modules" (
  cd frontend
  call npm install
  cd ..
)

if not exist "data\runtime" mkdir "data\runtime"
echo Setup complete.
echo Start the application with start.bat
echo ========================================
pause
