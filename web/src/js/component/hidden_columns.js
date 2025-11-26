import { HiddenColumns } from "../data";

W.component('hidden_columns', {
  init: function() {
    this.accessor = new HiddenColumns();
  },

  getData: function(cb) {
    const columns = this.accessor.get();
    cb({
      hiddenColumns: columns,
      hasHiddenColumns: columns.length > 0
    });
  },

  rendered: function(cb) {
    const $container = $(this.el);

    // Column show handler
    $container.find('.show-column').on('click', (ev) => {
      const column = $(ev.target).data('column');
      if (column) {
        this.accessor.remove(column);
        this.load();
        this.app.data.refresh('hiddenColumns');
      }
    });
    cb();
  },
});
