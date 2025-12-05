# potential-panda-kilimanjaro

Remote backtesting job management system for running and monitoring quantitative trading strategy backtests from anywhere.

Part of the [potential-panda](https://github.com/potential-panda) trading system ecosystem.

## Features

- **Remote Job Submission**: Submit backtesting jobs via web UI from anywhere
- **Background Agent**: Agent runs on development server, polls for jobs and executes backtests
- **Real-time Status Tracking**: Monitor job progress (pending, running, done, error)
- **Results Management**: View and analyze completed backtest results with sortable columns
- **Strategy Templates**: Dynamic form generation based on strategy configuration
- **Parameter Grid**: Configure parameter optimization grids for backtesting

## Problem Statement

Running backtests on a development server has several challenges:
- Backtesting takes significant time to complete
- Consumes almost all system resources during execution
- Cannot easily start backtests when away from the development machine

**Solution**: Kilimanjaro provides a web UI hosted on a public server for job submission, and a lightweight agent on the development server that polls for jobs and executes them in the background.

## Architecture

- **API Service** (`kilimanjaro/api.py`): Flask-based REST API for job management
- **Agent** (`kilimanjaro/agent.py`): Background service that polls for jobs and executes backtests
- **Web UI** (`web/`): JavaScript frontend built with Whirlpool framework ([see web/README.md](web/README.md))

For detailed feature specification, see [spec.md](spec.md).

## Prerequisites

- Python 3.11+
- Node.js 18+ (for web UI development)
- Git (for installing core library dependency)
- Access to potential-panda-core repository
- Development server for running backtests

## Installation & Development Workflow

This project depends on the qate core library via git+ssh (declared in `pyproject.toml`). Two common setups:

### Local development (editable installs)

Use when you develop Kilimanjaro and core side-by-side.

```bash
cd ~/workspace
git clone git@github.com:potential-panda/potential-panda-core.git
git clone git@github.com:potential-panda/potential-panda-kilimanjaro.git

cd potential-panda-kilimanjaro
# activate your env (e.g., pyenv local panda-313 or python -m venv .venv && source .venv/bin/activate)
pip install -e ../potential-panda-core   # qate core, editable
pip install -e . --no-deps               # kilimanjaro app, editable
```

Run from the repo root so imports resolve:
```bash
python -m kilimanjaro.api    # start API
python -m kilimanjaro.agent  # start agent
```

### Production / CI

Install from the git-pinned dependency:
```bash
pip install .
```

To pin a specific core ref, edit the dependency in `pyproject.toml` (branch/tag/commit) and reinstall.

### Notes

- Keep `kilimanjaro/` as the package root; prefer running with `python -m kilimanjaro.api` or `python -m kilimanjaro.agent`.
- Verify editable installs with `pip show potential-panda-core` (Location should point to your local repo).

**Important Notes:**

- ⚠️ **Do NOT commit** the editable install state—it's local only
- ⚠️ **Do NOT modify** `pyproject.toml` for ad-hoc local changes; keep dependency pins intentional
- ✅ CI and production use the git+ssh dependency from `pyproject.toml`
- ✅ The editable install only affects your local environment

**Publishing Core Changes:**

When your core library changes are ready:

```bash
# 1. Commit and push core changes
cd ../potential-panda-core
git add .
git commit -m "Add new feature"
git push origin main

# 2. (Optional) Tag a release
git tag -a v0.1.7 -m "Release v0.1.7"
git push origin v0.1.7

# 3. Update app dependency (if using tags)
cd ../potential-panda-kilimanjaro
# Edit pyproject.toml dependency to reference the new tag/commit
git add pyproject.toml
git commit -m "Update core dependency to v0.1.7"
git push
```

**Switching Back to Production Mode:**

To remove the editable install and use the GitHub version:

```bash
pip uninstall potential-panda-core
pip install .
```

---

### Quick Reference

| Mode | When to Use | Installation |
|------|-------------|--------------|
| **Production/CI** | Production, CI, app-only development | `pip install .` |
| **Local Dev** | Developing both app and core | `pip install -e ../potential-panda-core && pip install -e . --no-deps` |

| Task | Command |
|------|---------|
| Verify editable install | `pip show potential-panda-core \| grep Editable` |
| Switch to production mode | `pip uninstall potential-panda-core && pip install .` |
| Update core from GitHub | `pip install . --force-reinstall --no-cache-dir` |
| Check import source | `python -c "import qate; print(qate.__file__)"` |

---

## Configuration

The application requires:

**Environment Directory** (`~/env/KILIMANJARO_API/` and `~/env/KILIMANJARO_AGENT/`):
```bash
# Create API environment
python -m kilimanjaro.api --setup

# Create Agent environment
python -m kilimanjaro.agent --setup
```

This creates the necessary directory structure for configuration and data storage.

**Web UI Dependencies** (Optional):
```bash
cd web
npm install
```

## Usage

### Running with Docker (Recommended)

```bash
# Build and start the service
docker-compose up -d

# View logs
docker-compose logs -f

# Stop the service
docker-compose down
```

The API will be available at `http://localhost:8766`

### Running Locally

**API Service:**
```bash
# Run directly with Python module
python -m kilimanjaro.api
```

**Agent Service:**
```bash
# Run the agent
python -m kilimanjaro.agent
```

**Web UI Development:**
```bash
cd web

# Development server with hot reload (port 8080)
npm run dev

# Production build
npm run build
```

## API Endpoints

- `POST /job/new` - Create new backtesting job
- `DELETE /job/delete/<job_id>` - Delete a job
- `POST /job/acquire` - Agent acquires next pending job
- `GET /job/list` - List all jobs
- `POST /job/update/<job_id>` - Update job progress/status
- `GET /job/<job_id>` - Get job details
- `GET /result/list` - List all completed run logs
- `DELETE /result/<bucket_name>` - Delete a result
- `GET /health` - Health check

## Application Configuration

### Port Configuration

- **API Service**: 8766
- **Web Development Server**: 8080

### Data Storage

- **Job Files**: `~/env/KILIMANJARO_API/jobs/` (JSONL format)
  - `.json` - Pending jobs
  - `.json.acquired` - Jobs being processed
  - `.json.done` - Completed jobs
  - `.json.error` - Failed jobs
- **Results**: `~/env/KILIMANJARO_API/results/` (Individual JSON files per run log)

### Agent Configuration

- **Job Acquisition Interval**: 12 seconds (polls API for new jobs)
- **Progress Check Interval**: 600 seconds (10 minutes, updates job status)
- **Backtest Environment**: Configurable per job or uses default `BENJAMIN_TATUM`

## Development

### Project Structure

```
potential-panda-kilimanjaro/
├── kilimanjaro/
│   ├── api.py             # Flask API backend
│   ├── data_access.py     # File-based job storage
│   └── agent.py           # Background agent
├── web/                           # JavaScript frontend (separate from Python)
│   ├── src/
│   ├── public/
│   ├── package.json
│   └── webpack.*.js
├── pyproject.toml                 # Python package/dep config
├── Dockerfile                     # Docker image definition
├── docker-compose.yml            # Docker orchestration
└── README.md                     # This file
```

### Dependencies

This application depends on [potential-panda-core](https://github.com/potential-panda/potential-panda-core), which provides:

- `qate.boot.env` - Environment management
- `qate.boot.profile` - Strategy profile configuration
- `qate.boot.run_log` - Run log reading and processing

**Development Modes:**

See the [Installation & Development Workflow](#installation--development-workflow) section above for detailed instructions on:
- **Production/CI Mode**: Using the core library from GitHub (default)
- **Local Development Mode**: Using an editable local install for simultaneous app and core development

### Making Changes

**App-Only Changes:**
1. Create a feature branch
2. Make your changes to `kilimanjaro/`
3. Test locally: `python -m kilimanjaro.api`
4. Submit a pull request

**App + Core Changes:**
1. Set up local development mode (see workflow section)
2. Make changes to both repositories
3. Test with editable install: `pip install -e ../potential-panda-core`
4. Commit and push core changes first
5. Update `pyproject.toml` if you need to pin a new core ref
6. Commit and push app changes

### Testing

```bash
# Run API service
python -m kilimanjaro.api

# In another terminal, run agent
python -m kilimanjaro.agent

# In another terminal, test endpoints
curl http://localhost:8766/health
curl -X POST http://localhost:8766/job/new -H "Content-Type: application/json" -d '{"strategy_name":"orion","variant":"v0","from_dt":"2024-01-01","to_dt":"2024-12-31"}'
```

## Deployment

### Production Deployment

1. **Set up server environment**:
   - Ensure Python 3.11+ is installed
   - Install git for core library access
   - Configure development server with backtest environment

2. **Deploy with Docker**:
   ```bash
   git clone git@github.com:potential-panda/potential-panda-kilimanjaro.git
   cd potential-panda-kilimanjaro
   docker-compose up -d
   ```

3. **Configure reverse proxy** (nginx example):
   ```nginx
   server {
       listen 80;
       server_name backtest.example.com;

       location / {
           proxy_pass http://localhost:8766;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
       }
   }
   ```

4. **Set up agent on development server**:
   ```bash
   python -m kilimanjaro.agent --setup
   python -m kilimanjaro.agent
   ```

### Environment Variables

- `PYTHONUNBUFFERED=1` - Enable real-time logging in Docker
- `PYTHON_GIL=0` - Disable GIL for backtest execution (Python 3.13+)

## Troubleshooting

### Agent Cannot Acquire Jobs

- Check that API service is running and accessible
- Verify `API_BASE_URL` in agent configuration matches API service location
- Check network connectivity between agent and API service

### Backtest Execution Fails

- Verify backtest environment is properly configured in `~/env/`
- Check agent logs for error messages
- Ensure development server has required resources (CPU, memory)

### Web UI Not Connecting to API

- Verify API service is running on port 8766
- Check browser console for CORS errors
- Ensure Flask-CORS is installed and enabled

## License

MIT

## Documentation

- **[spec.md](spec.md)** - Detailed feature specification
- **[web.md](web.md)** - Web UI specification and implementation details
- **[web/README.md](web/README.md)** - Frontend architecture and development guide

## Related Repositories

- [potential-panda-core](https://github.com/potential-panda/potential-panda-core) - Core trading engine library
- [potential-panda](https://github.com/potential-panda) - Organization homepage

## Support

For issues and questions, please open an issue on GitHub.
