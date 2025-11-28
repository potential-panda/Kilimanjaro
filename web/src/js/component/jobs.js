import { client } from '../data.js';

W.component('jobs', {
  init: function() {
    this.client = client;
  },
  getData: function(cb) {
    this.client.fetchJobs(cb);
  },
  rendered: function(cb) {
    const $container = $(this.el);

    // Manual refresh handler
    $container.find('.refresh-jobs').on('click', () => {
      this.load();
    });

    // Clone job handler
    $container.find('.clone-job').on('click', (ev) => {
      const jobId = $(ev.target).closest('button').data('job-id');
      this.client.fetchJob(jobId, (data) => {
        if (!data || !data.job) {
          alert('Error fetching job details');
          return;
        }

        // Store job data in sessionStorage for the new component to pick up
        sessionStorage.setItem('cloneJobData', JSON.stringify(data.job));

        // Navigate to new job page
        window.location.hash = '#new';
      });
    });

    // Delete job handler
    $container.find('.delete-job').on('click', (ev) => {
      const jobId = $(ev.target).data('job-id');
      this.client.deleteJob(jobId, (error) => {
        if (!error) {
          this.load();
          return;
        }
        alert('Error deleting job: ' + (error.responseJSON?.error || 'Unknown error'));
      });
    });

    // View job details handler for clickable job ID
    $container.find('.clickable-job-id').on('click', (ev) => {
      const jobId = $(ev.target).data('job-id');
      this.client.fetchJob(jobId, (data) => {
        if (!data || !data.job) {
          alert('Error fetching job details');
          return;
        }

        const jobData = data.job;

        // Build formatted job details HTML
        let detailsHtml = `
          <div class="row mb-3">
            <div class="col-md-6">
              <strong>Job ID:</strong>
              <div class="mt-1"><code>${jobData.job_id || 'N/A'}</code></div>
            </div>
            <div class="col-md-6">
              <strong>Status:</strong>
              <div class="mt-1"><span class="badge bg-secondary">${jobData.status || 'N/A'}</span></div>
            </div>
          </div>

          <div class="row mb-3">
            <div class="col-md-6">
              <strong>Strategy:</strong>
              <div class="mt-1">${jobData.strategy_name || 'N/A'}</div>
            </div>
            <div class="col-md-6">
              <strong>Variant:</strong>
              <div class="mt-1">${jobData.variant || 'N/A'}</div>
            </div>
          </div>

          <div class="row mb-3">
            <div class="col-md-6">
              <strong>From Date:</strong>
              <div class="mt-1">${jobData.from_dt || 'N/A'}</div>
            </div>
            <div class="col-md-6">
              <strong>To Date:</strong>
              <div class="mt-1">${jobData.to_dt || 'N/A'}</div>
            </div>
          </div>

          <div class="row mb-3">
            <div class="col-md-6">
              <strong>Optimize:</strong>
              <div class="mt-1">${jobData.optimize ? 'Yes' : 'No'}</div>
            </div>
            <div class="col-md-6">
              <strong>Retest:</strong>
              <div class="mt-1">${jobData.retest ? 'Yes' : 'No'}</div>
            </div>
          </div>

          <div class="mb-3">
            <strong>Configuration:</strong>
            <pre class="bg-light p-3 mt-1 rounded"><code>${JSON.stringify(jobData.config || {}, null, 2)}</code></pre>
          </div>

          <div class="mb-3">
            <strong>Parameter Grid:</strong>
            <pre class="bg-light p-3 mt-1 rounded"><code>${JSON.stringify(jobData.param_grid || [], null, 2)}</code></pre>
          </div>
        `;

        if (jobData.updates && jobData.updates.length > 0) {
          detailsHtml += `
            <div class="mb-3">
              <strong>Updates:</strong>
              <div class="mt-2">
          `;
          jobData.updates.forEach((update, i) => {
            detailsHtml += `
              <div class="card mb-2">
                <div class="card-body py-2">
                  <small><strong>Update ${i + 1}:</strong> Progress: ${update.progress || 'N/A'}</small>
                  ${update.result ? `<pre class="bg-light p-2 mt-1 mb-0 rounded"><code>${JSON.stringify(update.result, null, 2)}</code></pre>` : ''}
                </div>
              </div>
            `;
          });
          detailsHtml += '</div></div>';
        }

        // Insert into modal and show
        $container.find('.job-details-content').html(detailsHtml);
        const modal = new bootstrap.Modal($container.find('#jobDetailsModal')[0]);
        modal.show();
      });
    });

    cb();
  }
});
