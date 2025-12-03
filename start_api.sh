#!/bin/bash

# Navigate to the script's directory
cd "$(dirname "$0")"

# Run the API as a daemon
nohup python src/api.py > kilimanjaro_api.log 2>&1 &

echo "Kilimanjaro API started with PID $!"
