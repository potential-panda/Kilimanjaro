# Kilimanjaro

## Problem
Start backtesting at anytime and from anywhere.
- I run backtesting on my home development server.
- The backtesting takes time to run.
- Backtesting consumes almost all resources, when it's running I can hardly do anything else at the same time.
- I want to start backtesting at any time when I'm outside and don't have access to my PC, when I get back I can see the results.


## Goals
- A web UI hosted on a public server where I can specify backtesting parameters
- A small agent program that runs on my home development server, polls for new backtesting jobs and starts the backtesting
- The agent program should be able to register the status (running, done, error, progress %) of the backtest to the web UI service
- The web UI should be able to display the status and a summary of the results


## Components

### Kilimanjaro API

Please build the API service by following a similar pattern to w.py, it may import some libraries from qate.
Performance is not a concern here, please use a global lock to only allow 1 API call at a time.
Please extract the data access part, the current data solution is file-based, later we may change to use any data store like a database.

- POST /job/new
  - save whatever is available passed from the creation form in JSON format
  - generate a UUID as the job ID
  - save the JSON in a local file $job_dir/$job_id.json
- DELETE /job/delete/$job_id
  - remove the job json file
- POST /job/acquire
  - find the oldest job file, rename it to $job_dir/$job_id.json.acquired
  - return the job ID and JSON content
  - return empty {} when there is no job
- GET /job/list
  - list all job files $job_dir/*
- POST /job/update/$job_id
  - update the status of the job
  - the post body should contain
    - "progress"
       - when it is an integer, it means the percentage of the progress
       - when it is ERROR, it means the job failed
       - when it is DONE, it means the job is finished, and the post body should also contain a "result" object
  - the job JSON should contain an "updates" list-type field
    - when created, the updates list is empty
    - whenever the API is called, append the post body to the updates list and save the file

### Kilimanjaro Agent

It is a standalone long-running process
Please build the Agent by following a similar pattern to d.py
Please implement 2 scheduled jobs:

- Keep calling /job/acquire every $X minutes
  - if /job/acquire returns a job:
    - setup an ENV for running the backtest (a.py setup ...)
      - should create profile.json, config.json, param_grid.json in the env/$env_name folder
    - run the backtest (a.py backtest ...)
  - the Agent should run 1 job at a time
    - if there is a running job, don't call /job/acquire
- Check running job progress every $Y minutes
  - finish quickly if no job is running
  - if there is a job running, go to the env folder, grep "progress" in the env/$env_name/env.log
  - post the progress to /job/update/$job_id
  - if the job failed, post {"progress": "ERROR"} to /job/update/$job_id
  - if the job finished, post {"progress": "DONE"} to /job/update/$job_id

### Kilimanjaro WebUI

See [web.md](web.md) for detailed WebUI specifications.

### Implementation Steps
1. Implement API in /src/app/kilimanjaro/api
2. Implement Agent in /src/app/kilimanjaro/agent
3. Implement WebUI in /src/app/kilimanjaro/web
