@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" vitadock_garmin_sync.py >> sync.log 2>&1
