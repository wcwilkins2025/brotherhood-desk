@echo off
rem Brotherhood Desk daily run: collect -> analyze -> build -> publish
cd /d C:\Projects\brotherhood-desk
if not exist logs mkdir logs
set LOG=logs\%date:~-4%-%date:~4,2%-%date:~7,2%.log
for /f "usebackq tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"
echo ===== %date% %time% ===== >> %LOG%
python collector\collect.py >> %LOG% 2>&1
python analyst\analyze.py >> %LOG% 2>&1
python analyst\build_desk.py >> %LOG% 2>&1
python publish.py >> %LOG% 2>&1
echo ===== done %time% ===== >> %LOG%
