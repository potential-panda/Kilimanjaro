import { hiddenColumns } from "../data";

W.component('hidden_columns', {
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
