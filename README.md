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

- **API Service** (`src/kilimanjaro/api/`): Flask-based REST API for job management
- **Agent** (`src/kilimanjaro/agent/`): Background service that polls for jobs and executes backtests
- **Web UI** (`web/`): JavaScript frontend built with Whirlpool framework ([see web/README.md](web/README.md))

For detailed feature specification, see [spec.md](spec.md).

## Prerequisites

- Python 3.11+
- Node.js 18+ (for web UI development)
- Git (for installing core library dependency)
- Access to potential-panda-core repository
- Development server for running backtests

## Installation & Development Workflow

This project depends on the [potential-panda-core](https://github.com/potential-panda/potential-panda-core) library via git+ssh. There are two modes of operation:

### Mode 1: Production / CI Mode (Default)

This mode is used for production deployments, CI pipelines, and when you only need to work on the app without modifying the core library.

**Installation:**

```bash
# 1. Clone the repository
git clone git@github.com:potential-panda/potential-panda-kilimanjaro.git
cd potential-panda-kilimanjaro

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install dependencies (installs core library from GitHub)
pip install -r requirements.txt
```

The core library (`potpanda`) is automatically installed from GitHub using the version specified in `requirements.txt`:
```
potential-panda-core @ git+ssh://git@github.com/potential-panda/potential-panda-core.git@main
```

**Requirements:**
- SSH access to the core repository
- SSH keys configured for GitHub (`~/.ssh/id_rsa` or `~/.ssh/id_ed25519`)
- Test SSH access: `ssh -T git@github.com`

**Changing Core Version:**

To use a different branch, tag, or commit, edit `requirements.txt`:
```
# Use develop branch
potential-panda-core @ git+ssh://git@github.com/potential-panda/potential-panda-core.git@develop

# Use specific tag
potential-panda-core @ git+ssh://git@github.com/potential-panda/potential-panda-core.git@v0.1.7

# Use specific commit
potential-panda-core @ git+ssh://git@github.com/potential-panda/potential-panda-core.git@abc1234
```

Then reinstall:
```bash
pip install -r requirements.txt --force-reinstall
```

---

### Mode 2: Local Development Mode (Editable Install)

Use this mode when you need to develop both the app and the core library simultaneously. Changes to either repository take effect immediately without commits or reinstalls.

**Setup:**

Both repositories should be in the same parent directory:
```
workspace/
├── potential-panda-core/        # Core library
└── potential-panda-kilimanjaro/ # This application
```

**Step-by-Step Instructions:**

```bash
# 1. Clone both repositories side-by-side
cd ~/workspace  # or your preferred workspace directory
git clone git@github.com:potential-panda/potential-panda-core.git
git clone git@github.com:potential-panda/potential-panda-kilimanjaro.git

# 2. Set up the application
cd potential-panda-kilimanjaro
python -m venv .venv
source .venv/bin/activate

# 3. Install all dependencies (including core from GitHub)
pip install -r requirements.txt

# 4. Override with local editable install
pip install -e ../potential-panda-core
```

**How It Works:**

- Step 3 installs all dependencies, including `potpanda` from GitHub
- Step 4 **replaces** the GitHub version with a local editable install
- Editable install creates a `.egg-link` file in `site-packages` pointing to your local `potential-panda-core/src` directory
- Any changes to `potential-panda-core/src/potpanda/` take effect immediately—no reinstall needed

**Development Workflow:**

```bash
# Edit core library
vim ../potential-panda-core/src/potpanda/boot/env.py
# Changes are immediately available in the app

# Edit application
vim src/kilimanjaro/api/api.py
# Changes take effect immediately

# Run the API service
cd src
python -m kilimanjaro.api.api
# Uses your local core library changes
```

**Verify Editable Install:**

```bash
pip show potential-panda-core
# Look for:
#   Location: /path/to/workspace/potential-panda-core/src
#   Editable project location: /path/to/workspace/potential-panda-core
```

**Important Notes:**

- ⚠️ **Do NOT commit** the editable install state—it's local only
- ⚠️ **Do NOT modify** `requirements.txt` while in development mode
- ✅ CI and production always use the git+ssh dependency from `requirements.txt`
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
# Edit requirements.txt to reference the new tag:
# potential-panda-core @ git+ssh://git@github.com/potential-panda/potential-panda-core.git@v0.1.7
git add requirements.txt
git commit -m "Update core dependency to v0.1.7"
git push
```

**Switching Back to Production Mode:**

To remove the editable install and use the GitHub version:

```bash
pip uninstall potential-panda-core
pip install -r requirements.txt
```

---

### Quick Reference

| Mode | When to Use | Installation |
|------|-------------|--------------|
| **Production/CI** | Production, CI, app-only development | `pip install -r requirements.txt` |
| **Local Dev** | Developing both app and core | `pip install -r requirements.txt && pip install -e ../potential-panda-core` |

| Task | Command |
|------|---------|
| Verify editable install | `pip show potential-panda-core \| grep Editable` |
| Switch to production mode | `pip uninstall potential-panda-core && pip install -r requirements.txt` |
| Update core from GitHub | `pip install -r requirements.txt --force-reinstall` |
| Check import source | `python -c "import potpanda; print(potpanda.__file__)"` |

---

## Configuration

The application requires:

**Environment Directory** (`~/env/KILIMANJARO_API/` and `~/env/KILIMANJARO_AGENT/`):
```bash
# Create API environment
cd src
python -m kilimanjaro.api.api --setup

# Create Agent environment
python -m kilimanjaro.agent.agent --setup
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
cd src
python -m kilimanjaro.api.api

# Or use the provided script (if available)
./run_api.sh
```

**Agent Service:**
```bash
# Run the agent
cd src
python -m kilimanjaro.agent.agent
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
- **Backtest Environment**: Configurable per job or uses default `QIN_TENGFEI`

## Development

### Project Structure

```
potential-panda-kilimanjaro/
├── src/
│   └── kilimanjaro/
│       ├── api/                    # Flask API backend
│       │   ├── api.py             # Main API server
│       │   └── data_access.py     # File-based job storage
│       ├── agent/                 # Background agent
│       │   └── agent.py           # Job polling and execution
│       ├── spec.md                # Feature specification
│       └── web.md                 # Web UI specification
├── web/                           # JavaScript frontend (separate from Python)
│   ├── src/
│   ├── public/
│   ├── package.json
│   └── webpack.*.js
├── requirements.txt               # Python dependencies
├── Dockerfile                     # Docker image definition
├── docker-compose.yml            # Docker orchestration
└── README.md                     # This file
```

### Dependencies

This application depends on [potential-panda-core](https://github.com/potential-panda/potential-panda-core), which provides:

- `potpanda.boot.env` - Environment management
- `potpanda.boot.profile` - Strategy profile configuration
- `potpanda.boot.run_log` - Run log reading and processing

**Development Modes:**

See the [Installation & Development Workflow](#installation--development-workflow) section above for detailed instructions on:
- **Production/CI Mode**: Using the core library from GitHub (default)
- **Local Development Mode**: Using an editable local install for simultaneous app and core development

**Quick setup for local development:**
```bash
# Install from GitHub (production mode)
pip install -r requirements.txt

# OR override with local editable install (development mode)
pip install -r requirements.txt
pip install -e ../potential-panda-core
```

### Making Changes

**App-Only Changes:**
1. Create a feature branch
2. Make your changes to `src/kilimanjaro/`
3. Test locally: `cd src && python -m kilimanjaro.api.api`
4. Submit a pull request

**App + Core Changes:**
1. Set up local development mode (see workflow section)
2. Make changes to both repositories
3. Test with editable install: `pip install -e ../potential-panda-core`
4. Commit and push core changes first
5. Update `requirements.txt` if needed
6. Commit and push app changes

### Testing

```bash
# Run API service
cd src
python -m kilimanjaro.api.api

# In another terminal, run agent
cd src
python -m kilimanjaro.agent.agent

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
   cd src
   python -m kilimanjaro.agent.agent --setup
   python -m kilimanjaro.agent.agent
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
