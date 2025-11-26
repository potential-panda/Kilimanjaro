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

1. **Setup**: Copy files to a web server directory with whirlpool.min.js
2. **API**: Ensure Kilimanjaro API is running on localhost:8766
3. **Navigate**:
   - `/#list` or `/` - View jobs
   - `/#new` - Create new job

## Dependencies

- Bootstrap 5.2.3 (CSS framework)
- jQuery 3.6.0 (DOM manipulation)
- Whirlpool.js (Component framework)

## API Integration

- `POST /job/new` - Create job
- `GET /job/list` - List jobs
- `GET /job/{id}` - Get job details
- `DELETE /job/delete/{id}` - Delete job

All components handle API errors gracefully with user-friendly messages.