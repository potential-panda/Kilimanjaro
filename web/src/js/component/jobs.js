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
        if (!data) {
          alert('Error fetching job details');
          return;
        }

        const jobData = data.job;
        let details = `${JSON.stringify(jobData, null, 2)}`;
        // details += `Strategy: ${jobData.strategy_name}\n`;
        // details += `Variant: ${jobData.variant}\n`;
        // details += `From Date: ${jobData.from_dt}\n`;
        // if (jobData.to_dt) details += `To Date: ${jobData.to_dt}\n`;
        // details += `Optimize: ${jobData.optimize}\n`;
        // details += `Retest: ${jobData.retest}\n`;

        // if (jobData.updates && jobData.updates.length > 0) {
        //   details += '\nUpdates:\n';
        //   jobData.updates.forEach((update, i) => {
        //     details += `${i + 1}. Progress: ${update.progress}\n`;
        //     if (update.result) {
        //       details += `   Result: ${JSON.stringify(update.result, null, 2)}\n`;
        //     }
        //   });
        // }
        alert(details);
      });
    });

    cb();
  }
});
