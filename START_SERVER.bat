@echo off
echo ========================================
echo  Customs Invoice Tool - Local Server
echo ========================================
echo.
echo Checking Python installation...
python --version
if errorlevel 1 (
    echo ERROR: Python not found!
    echo Please install Python from Microsoft Store first.
    pause
    exit
)

echo.
echo Installing required packages...
pip install -r requirements.txt

echo.
echo Starting the Customs Invoice Tool...
echo.
echo The app will open in your browser automatically.
echo Your colleagues can access it at: http://%COMPUTERNAME%:8501
echo.
echo Press Ctrl+C to stop the server.
echo.
streamlit run app.py --server.port 8501 --server.address 0.0.0.0
