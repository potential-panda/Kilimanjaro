import { API_CONFIG, GRAFANA_CONFIG } from './common.js';

function generateGrafanaUrl(bucketName, fromDate, toDate) {
  // Convert date strings to ISO format for Grafana
  const formatDateForGrafana = (dateStr, isEndDate = false) => {
    if (!dateStr) return '';
    try {
      let date;
      // Handle YYYYMMDD_hhmm format (e.g., "20250919_1430")
      if (/^\d{8}_\d{4}$/.test(dateStr)) {
        const year = dateStr.substring(0, 4);
        const month = dateStr.substring(4, 6);
        const day = dateStr.substring(6, 8);
        const hour = dateStr.substring(9, 11);
        const minute = dateStr.substring(11, 13);
        date = new Date(`${year}-${month}-${day}T${hour}:${minute}:00`);
      }
      // Handle YYYYMMDD format (e.g., "20250919")
      else if (/^\d{8}$/.test(dateStr)) {
        const year = dateStr.substring(0, 4);
        const month = dateStr.substring(4, 6);
        const day = dateStr.substring(6, 8);
        date = new Date(`${year}-${month}-${day}`);
      } else {
        // Handle other date formats
        date = new Date(dateStr);
        if (isNaN(date.getTime())) {
          console.error('Invalid date format:', dateStr);
          return '';
        }
      }

      // Set time based on whether it's start or end date
      // Only apply for YYYYMMDD format (not for YYYYMMDD_hhmm which already has time)
      if (!/^\d{8}_\d{4}$/.test(dateStr)) {
        if (isEndDate) {
          // End of day: 23:59:59.999
          date.setHours(23, 59, 59, 999);
        } else {
          // Start of day: 00:00:00.000
          date.setHours(0, 0, 0, 0);
        }
      }

      return date.toISOString();
    } catch (e) {
      console.error('Invalid date format:', dateStr);
      return '';
    }
  };

  const fromFormatted = formatDateForGrafana(fromDate, false);
  const toFormatted = formatDateForGrafana(toDate, true);

  let url = GRAFANA_CONFIG.DASHBOARD_BASE_URL + encodeURIComponent(bucketName);

  if (fromFormatted) {
    url += '&from=' + encodeURIComponent(fromFormatted);
  }
  if (toFormatted) {
    url += '&to=' + encodeURIComponent(toFormatted);
  }

  return url;
}


function flattenObject(obj, prefix = '', result = {}) {
  for (let key in obj) {
    if (obj.hasOwnProperty(key)) {
      const newKey = prefix ? `${prefix}_${key}` : key;
      if (obj[key] && typeof obj[key] === 'object' && !Array.isArray(obj[key])) {
        flattenObject(obj[key], newKey, result);
      } else {
        result[newKey] = obj[key];
      }
    }
  }
  return result;
}

function formatFloats(obj) {
  const result = {};
  for (const [key, value] of Object.entries(obj)) {
    if (typeof value === "number" && !Number.isInteger(value)) {
      result[key] = Number(value.toFixed(6));
    } else {
      result[key] = value;
    }
  }
  return result;
}

function formatData(data) {
  if (!data || !Array.isArray(data)) {
    return [];
  }

  const run_logs = data.map(runLog => {
    const flattened = flattenObject(runLog);
    if (flattened.bucket_name) {
      flattened.__grafana_url = generateGrafanaUrl(
        flattened.bucket_name,
        flattened.from_date || flattened.from_dt,
        flattened.to_date || flattened.to_dt
      );
    }
    return formatFloats(flattened);
  });

  return run_logs;
}



export class Client {
  fetchResults(cb) {
    // Fetch run_logs directly from API
    $.get(API_CONFIG.ROOT + 'result/list')
      .done(data => {
        cb({results: formatData(data)});
      })
      .fail(() => {
        cb({ results: [], error: 'Failed to fetch results' });
      });
  }

  deleteResults(results, cb) {
    let completed = 0;
    let failed = 0;
    const total = results.length
    results.forEach(bucketName => {
      $.ajax({
        url: API_CONFIG.ROOT + 'result/' + encodeURIComponent(bucketName),
        method: 'DELETE',
        success: () => {
          completed++;
          if (completed + failed === total) {
            cb();
          }
        },
        error: () => {
          failed++;
          if (completed + failed === total) {
            cb();
          }
        }
      });
    });
  }

  fetchJobs(cb) {
    $.get(API_CONFIG.ROOT + 'job/list')
      .done(data => {
        // Filter to only active jobs (pending, acquired, error)
        const jobs = data
          .filter(job => job.status.toLowerCase() !== 'done')
          .map(job => {
            return {
              job_id: job.job_id,
              status: job.status,
              created_time: new Date(job.created_time * 1000).toLocaleString(),
              file_name: job.file_name
            };
          });
        cb({ jobs: jobs });
      })
      .fail(() => {
        cb({ jobs: [], error: 'Failed to fetch jobs' });
      });
  }

  fetchJob(jobId, cb) {
    $.get(API_CONFIG.ROOT + `job/${jobId}`)
      .done(data => {
        cb({'job': data})
      })
      .fail(cb)
  }

  deleteJob(jobId, cb) {
      $.ajax({
      url: API_CONFIG.ROOT + `job/delete/${jobId}`,
      method: 'DELETE',
      success: () => {
        cb();
      },
      error: (xhr) => {
        cb(xhr);
      }
    });
 }
}




function _load() {
    const stored = localStorage.getItem('kilimanjaro_hidden_columns');
    return stored ? JSON.parse(stored) : [];
}

function _save(columns) {
    localStorage.setItem('kilimanjaro_hidden_columns', JSON.stringify(columns));
}

export class HiddenColumns {
  fetch(cb) {
    cb(_load());
  }

  add(column) {
    const columns = _load();
    if (columns.includes(column)) {
      return;
    }
    columns.push(column);
    _save(columns);
  }

  remove(column) {
    var columns = _load();
    columns = columns.filter(col => col !== column);
    _save(columns);
  }
}

export const client = new Client();
export const hiddenColumns = new HiddenColumns();

