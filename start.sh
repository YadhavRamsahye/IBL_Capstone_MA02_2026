#!/bin/bash
echo "Starting AI Traffic Bottleneck Detection System..."
echo "Open http://localhost:8000 in your browser"
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
