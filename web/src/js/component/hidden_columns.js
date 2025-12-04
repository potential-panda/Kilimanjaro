import { hiddenColumns } from "../data";

W.component('hidden_columns', {
  init: function() {
    this.app.data.fetch('hiddenColumns');
    this.app.data.on('hiddenColumns', () => {
      this.load();
    }, this);
  },
  getData: function(cb) {
    const columns = this.app.data.get('hiddenColumns');
    cb({
      hiddenColumns: columns,
      hasHiddenColumns: columns.length > 0
    });
  },
  rendered: function(cb) {
    const $container = $(this.el);
    $container.find('.show-column').on('click', (ev) => {
      const column = $(ev.target).data('column');
      if (column) {
        hiddenColumns.remove(column);
        this.app.data.fetch('hiddenColumns');
      }
    });
    cb();
  },
});
