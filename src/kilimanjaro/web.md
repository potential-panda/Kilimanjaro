# Kilimanjaro WebUI

Please use the JavaScript web framework https://github.com/lulurun/whirlpool
I hope you can learn how to use the framework from the GitHub repo and from the existing implementation in kilimanjaro/web

## Pages and layout

### Layout

1. there are 3 pages.
  - job creation. /index.html#new
  - job list. listing the jobs that are pemding, running or failed. /index.html#jobs
  - result list. listing the results that are extracted from the completed jobs. /index.html#results
2. the pages have same layout
  - header. display logo and the 3 links to the pages. should highlight and disable the link to the current page.
  - body. display the content for each page.
3. please implement header component and then a component for the body of each page.
  - header.js, header.html
  - new.js, new.html
  - jobs.js, jobs.html
  - results.js, results.html

### Page #new

**Fully implemented** job creation form with comprehensive functionality:
- Strategy selection dropdown with dynamic template loading
- Date range picker (from/to dates) with yesterday as default
- Configuration section that dynamically updates based on selected strategy
- Parameter grid with add/remove capability for optimization parameters
- Form validation and AJAX submission to `/job/new` API endpoint
- Uses Bootstrap styling with responsive design

### Page #jobs

**Fully implemented** active job management interface:
- Displays table of pending, acquired, and error status jobs (filters out completed jobs)
- Shows job ID, status badge, creation timestamp, and action buttons
- View button opens job details in popup with full job configuration and progress updates
- Delete button removes jobs via `/job/delete/{id}` API call
- Manual refresh functionality to reload job list
- Empty state with link to create first job

### Page #results

**Fully implemented** results viewer for completed jobs:
- Displays run logs extracted from completed jobs' result data
- Shows table with bucket name, strategy, parameters summary, date range, execution time
- View button displays detailed result JSON in popup
- Fetches completed jobs and extracts run_logs from job updates
- Handles multiple runs per job and aggregates all results
- Empty state with link to create first job when no results exist

#### Improvements

1. **Enhanced run_log display with dynamic columns**:
   - A run_log is a JSON object that may contain nested values like `{x:{y: 3}}`
   - Flatten the JSON object so the previous example becomes `{x_y: 3}`
   - Display all flattened fields as columns in the table (one run_log per row)
   - Replace underscores with spaces in column names for display
   - Make the table sortable by clicking column headers
     - Sort numeric values as numbers
     - Sort non-numeric values as strings
   - Add an 'x' icon next to each column header to hide columns
     - Store hidden column names in browser localStorage as a string array
   - Add a component below the table showing hidden columns
     - Display each hidden column name with a '+' icon to restore it