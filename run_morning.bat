@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
cd /d "C:\Auto Stock"
".\.venv\Scripts\python.exe" "C:\Auto Stock\morning_routine.py" >> "C:\Auto Stock\data\morning_routine.log" 2>&1

