import { hiddenColumns } from '../data.js';
import { getUrlParam, getPage } from '../util/url.js';


const importantColumns = new Set([
  'result_trading_pnl0',
  'result_trading_pnl5',
  'result_total_cnt0',
  'result_total_cnt5',
  'result_slippage_avg0',
  'result_slippage_avg5',
  'bucket_name',
]);

function sortData(data, column, direction) {
  if (!column) {
    return data;
  }

  return data.sort((a, b) => {
    let aVal = a[column];
    let bVal = b[column];

    // Handle null/undefined values
    if (aVal == null) aVal = '';
    if (bVal == null) bVal = '';

    // Try to parse as numbers
    const aNum = parseFloat(aVal);
    const bNum = parseFloat(bVal);

    let comparison = 0;
    if (!isNaN(aNum) && !isNaN(bNum)) {
      // Both are numbers
      comparison = aNum - bNum;
    } else {
      // String comparison
      comparison = String(aVal).localeCompare(String(bVal));
    }

    return direction === 'asc' ? comparison : -comparison;
  });
}


function filterTable(searchTerm, $container) {
    const $tableRows = $container.find('tbody tr');
    const $filterStatus = $container.find('.filter-status');

    // Split searchTerm by spaces if it contains spaces
    const searchTerms = searchTerm.trim().split(/\s+/).filter(term => term.length > 0);

    let visibleCount = 0;
    $tableRows.each(function() {
      const $row = $(this);
      const rowText = $row.text().toLowerCase();

      // Check if row matches all search terms
      const matchesAll = searchTerms.every(term => rowText.includes(term));

      if (matchesAll) {
        $row.show();
        visibleCount++;
      } else {
        $row.hide();
        // Uncheck hidden rows
        $row.find('.row-select').prop('checked', false);
      }
    });

    // Update filter status
    const totalCount = $tableRows.length;
    $filterStatus.text(`Showing ${visibleCount} of ${totalCount} results`);
}


class ManageCheckBoxes {
  constructor(component) {
    this.component = component;
    this.$container = $(this.component.el);

    const $selectAll = this.$container.find('.select-all');
    $selectAll.on('change', () => {
      const isChecked = $selectAll.is(':checked');
      this.$container.find('tbody tr:visible .row-select').prop('checked', isChecked);
      this.publish();
    });

    this.$container.find('.row-select').on('change', () => {
      $selectAll.prop('checked', this.isSelectedAll());
      this.publish();
    });

    this.$selectAll = $selectAll;
  }

  publish() {
    const selectedResults = [];
    this.$container.find('.row-select:checked').each((_, el) => {
      const name = $(el).data('bucket-name')
      selectedResults.push(name);
    });
    this.component.app.ev.emit('data.selectedResults.updated', { selectedResults: selectedResults });
  };

  isSelectedAll() {
    const $visibleRows = this.$container.find('tbody tr:visible');
    const $visibleCheckboxes = $visibleRows.find('.row-select');
    const checkedVisible = $visibleCheckboxes.filter(':checked').length;
    if (checkedVisible === 0) {
      return false;
    }
    if (checkedVisible === $visibleCheckboxes.length) {
      return true;
    }
    return false;
  }

  clear() {
    this.$selectAll.prop('checked', false);
    this.$container.find('tbody tr:visible .row-select').prop('checked', false);
    this.publish();
  }
}

function loadResults(component) {
  component.app.data.client.fetchResults((data) => {
    component.app.data.set('results', data);
  }, component.currentDir);
}

W.component('results_table', {
  init: function() {
    this.sortOption = {
      col: null,
      direction: 'desc',
    };
    this.searchTerm = '';
    this.currentDir = getUrlParam('dir') || 'default';

    loadResults(this);

    // Listen for data updates from other components
    this.app.data.on('results', (data, source) => {
      this.load();
    }, this);

    this.app.data.on('hiddenColumns', (data, source) => {
      this.load();
    }, this);

    this.app.nav.on(() => {
      if (getPage() === 'results') {
        const dir = getUrlParam('dir') || 'default';
        if (dir !== this.currentDir) {
          this.currentDir = dir;
          loadResults(this);
        }
      }
    }, this)

    this.app.data.fetch('hiddenColumns');
  },

  getData: function(cb) {
    const results = this.app.data.get('results');
    if (!results) {
      cb({
        results: [],
        visibleColumns: [],
      });
      return;
    }

    var run_logs = results.results;
    const allColumns = new Set();
    run_logs.forEach(log => {
      Object.keys(log).forEach(key => allColumns.add(key));
    });

    const hColumns = this.app.data.get('hiddenColumns');
    const visibleColumns = Array.from(allColumns).sort().filter(col =>
      !hColumns.includes(col) && !col.startsWith('__')
    );
    const columns = visibleColumns.map((col) => {
      return {
        name: col,
        displayName: col.replace(/_/g, ' '),
        isImportant: importantColumns.has(col),
        sortIndicator: (col === this.sortOption.col) ? (this.sortOption.direction === 'asc' ? '↑' : '↓') : '',
      };
    });


    if (run_logs.length > 0) {
      run_logs = sortData(run_logs, this.sortOption.col, this.sortOption.direction);
    }

    // Add PNL class for row coloring and parse bucket_name for timestamp
    run_logs = run_logs.map(log => {
      const pnl5 = parseFloat(log.result_pnl5);
      let pnlClass = '';
      if (!isNaN(pnl5)) {
        if (pnl5 > 0) {
          pnlClass = 'pnl-positive';
        } else if (pnl5 < 0) {
          pnlClass = 'pnl-negative';
        }
      }

      // Extract timestamp from bucket_name (format: xxx_timestamp_xxxx)
      let timestamp = '';
      let bucketNameFormatted = log.bucket_name;
      if (log.bucket_name) {
        const parts = log.bucket_name.split('_');
        if (parts.length >= 2) {
          timestamp = parts[1]; // Second part is the timestamp
          // Create formatted bucket name with clickable timestamp
          bucketNameFormatted = parts[0] + '_<span class="timestamp-link" data-timestamp="' + timestamp + '">' + timestamp + '</span>' + (parts.length > 2 ? '_' + parts.slice(2).join('_') : '');
        }
      }

      return { ...log, __pnl_class: pnlClass, __timestamp: timestamp, __bucket_name_formatted: bucketNameFormatted };
    });

    // Store modified run_logs for use in rendered()
    this.processedRunLogs = run_logs;

    cb({
      run_logs: run_logs,
      visibleColumns: columns,
      searchTerm: this.searchTerm,
    });
  },

  rendered: function(cb) {
    const $container = $(this.el);
    const checkboxes = new ManageCheckBoxes(this);

    // Manual refresh handler
    $container.find('.refresh-results').on('click', () => {
      loadResults(this);
    });

    filterTable(this.searchTerm, $container);

    // Format bucket_name cells with clickable timestamps
    if (this.processedRunLogs) {
      // Create a map of bucket_name to formatted version
      const bucketMap = {};
      this.processedRunLogs.forEach(log => {
        if (log.bucket_name && log.__bucket_name_formatted) {
          bucketMap[log.bucket_name] = log.__bucket_name_formatted;
        }
      });

      $container.find('tbody tr').each((_, row) => {
        const $row = $(row);
        const bucketName = $row.find('.row-select').data('bucket-name');
        const $bucketCell = $row.find('td[data-column="bucket_name"]');
        if ($bucketCell.length > 0 && bucketName && bucketMap[bucketName]) {
          $bucketCell.find('.result-cell').html(bucketMap[bucketName]);
        }
      });
    }

    // Timestamp link click handler
    $container.find('.timestamp-link').on('click', (ev) => {
      ev.stopPropagation(); // Prevent row/column highlighting
      const timestamp = String($(ev.target).attr('data-timestamp'));
      if (timestamp) {
        // Populate the filter box
        const $searchFilter = $container.find('.search-filter');
        $searchFilter.val(timestamp);
        // Trigger the filter
        this.searchTerm = timestamp;
        filterTable(timestamp, $container);
      }
    });

    // Search/filter functionality
    $container.find('.search-filter').on('input', (ev) => {
      const searchTerm = $(ev.target).val().toLowerCase();
      if (!searchTerm) {
        this.searchTerm = '';
        filterTable('', $container);
        return;
      }
      if (searchTerm.length < 3) {
        return;
      }
      checkboxes.clear();
      this.searchTerm = searchTerm;
      filterTable(searchTerm, $container);

    });


    // Column sort handler
    $container.find('.sortable-header').on('click', (ev) => {
      const $header = $(ev.currentTarget);
      const column = $header.data('column');

      if (this.sortOption.col === column) {
        this.sortOption.direction = this.sortOption.direction === 'asc' ? 'desc' : 'asc';
      } else {
        this.sortOption = {
          col: column,
          direction: 'desc',
        }
      }
      this.load();
    });

    // Column hide handler
    $container.find('.hide-column').on('click', (ev) => {
      ev.stopPropagation(); // Prevent sort trigger
      const column = $(ev.target).closest('th').data('column');
      hiddenColumns.add(column);
      this.app.data.fetch('hiddenColumns');
    });


    // Cell click handler for row and column highlighting
    $container.find('tbody td').on('click', (ev) => {
      const $clickedCell = $(ev.currentTarget);
      const cellIndex = $clickedCell.index();
      if (cellIndex < 2) { // Skip checkbox column (0) and Grafana icon column (1)
        return;
      }

      const $table = $clickedCell.closest('table');
      const $clickedRow = $clickedCell.closest('tr');

      // Remove previous highlights
      $table.find('tr').removeClass('highlighted-row');
      $table.find('td, th').removeClass('highlighted-column');

      // Highlight the clicked row
      $clickedRow.addClass('highlighted-row');

      // Highlight the clicked column (including header)
      $table.find('tr').each(function() {
        $(this).find('td, th').eq(cellIndex).addClass('highlighted-column');
      });
    });

    cb();
  },

});
