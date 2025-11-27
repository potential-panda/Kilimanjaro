# Kilimanjaro WebUI

This is the web interface for the Kilimanjaro remote backtesting system.

## Files Structure

- `index.html` - Main HTML page with navigation
- `kilimanjaro_main.js` - Main application controller and routing
- `kilimanjaro_new.js` - Job creation form component
- `kilimanjaro_new.html` - Job creation form template
- `kilimanjaro_list.js` - Job list table component
- `kilimanjaro_list.html` - Job list table template

## Features

### Job Creation Form (`/#new`)
- Strategy name and variant selection
- Date range configuration (from/to dates)
- Optimization and retest options
- Dynamic parameter grid management
- Form submission to API

### Job List Table (`/#list`)
- Real-time job status display
- Auto-refresh every 30 seconds
- Job actions (view details, delete)
- Status color coding (pending, running, done, error)

## Usage

### Development Setup

1. **Install dependencies**:
   ```bash
   npm install
   ```

2. **Configure API URL** (optional):
   - Copy `.env.example` to `.env`
   - Set `API_URL` if your API is not on localhost:8766
   - Example: `API_URL=https://api.yourdomain.com`

3. **Build the application**:
   ```bash
   npm run build    # Production build
   npm start        # Development server with hot reload
   ```

4. **Navigate**:
   - `/#jobs` - View active jobs (default page)
   - `/#new` - Create new job
   - `/#results` - View completed backtest results

## Dependencies

- Bootstrap 5.2.3 (CSS framework)
- jQuery 3.6.0 (DOM manipulation)
- Whirlpool.js (Component framework)

## API Integration

The web UI connects to the Kilimanjaro API backend. By default, it uses `http://localhost:8766`, but this can be overridden using the `API_URL` environment variable.

### Environment Variables

- `API_URL` - API base URL (default: `http://localhost:8766`)
  - Set this in `.env` file before building
  - Example: `API_URL=https://backtest-api.example.com`

### API Endpoints

- `POST /job/new` - Create job
- `GET /job/list` - List jobs
- `GET /job/{id}` - Get job details
- `DELETE /job/delete/{id}` - Delete job
- `GET /result/list` - List completed results
- `DELETE /result/{bucket_name}` - Delete result

All components handle API errors gracefully with user-friendly messages.