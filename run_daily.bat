@echo off
rem Brotherhood Desk run: collect -> analyze -> build -> publish (the 6 a.m. daily task and the hourly task both call this)
cd /d C:\Projects\brotherhood-desk
if not exist logs mkdir logs
rem a lock older than 90 minutes is left over from a crashed run; clear it
if exist logs\running.lock powershell -NoProfile -Command "if (((Get-Date) - (Get-Item 'logs\running.lock').LastWriteTime).TotalMinutes -gt 90) { Remove-Item 'logs\running.lock' -Force; Add-Content 'logs\skipped.log' ('stale lock removed ' + (Get-Date)) }"
if exist logs\running.lock (
  echo %date% %time% another run is in progress, skipping >> logs\skipped.log
  exit /b 0
)
echo %date% %time% > logs\running.lock
set LOG=logs\%date:~-4%-%date:~4,2%-%date:~7,2%.log
for /f "usebackq tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"
echo ===== %date% %time% ===== >> %LOG%
python collector\collect.py >> %LOG% 2>&1
python analyst\analyze.py >> %LOG% 2>&1
python analyst\build_desk.py >> %LOG% 2>&1
python publish.py >> %LOG% 2>&1
echo ===== done %time% ===== >> %LOG%
del logs\running.lock >nul 2>&1
