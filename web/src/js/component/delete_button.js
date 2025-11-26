import { client, data } from '../data.js';

W.component('delete_button', {
  init: function() {
    this.selectedResults = [];

    // Subscribe to results.selected from results component
    this.app.ev.on('data.selectedResults.updated', (data) => {
      // Keep the received data inside the component
      this.selectedResults = data.selectedResults || [];
      // Reload to update button visibility
      this.load();
    });
  },

  getData: function(cb) {
    cb({
      hasSelection: this.selectedResults.length > 0,
      selectedCount: this.selectedResults.length
    });
  },

  rendered: function(cb) {
    const $container = $(this.el);

    // Delete selected button handler
    $container.find('.delete-selected').on('click', () => {
      if (this.selectedResults.length > 0) {
        client.deleteResults(this.selectedResults, () => {
          this.selectedResults = [];
          this.app.data.refresh('results');
        });
      }
    });
    cb();
  },

});
