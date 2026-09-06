@echo off
cd /d "%~dp0"
python -m pip install -q -r requirements.txt
python -m streamlit run app.py --server.headless true
pause
