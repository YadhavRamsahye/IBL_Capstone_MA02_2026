@echo off
echo Starting AI Traffic Bottleneck Detection System...
echo Open http://localhost:8000 in your browser
echo.
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
pause
