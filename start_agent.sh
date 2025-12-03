#!/bin/bash

# Navigate to the script's directory
cd "$(dirname "$0")"

# Run the agent as a daemon
nohup python src/agent.py > kilimanjaro_agent.log 2>&1 &

echo "Kilimanjaro Agent started with PID $!"
