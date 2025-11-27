// Utility for flattening nested JSON objects for table display

export const dataFlattener = {
  /**
   * Flatten a nested object into a single-level object with underscore-separated keys
   * Example: {x: {y: 3}} => {x_y: 3}
   */
  flattenObject(obj, prefix = '') {
    const result = {};

    for (const [key, value] of Object.entries(obj)) {
      const newKey = prefix ? `${prefix}_${key}` : key;

      if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
        // Recursively flatten nested objects
        Object.assign(result, this.flattenObject(value, newKey));
      } else {
        // Assign primitive values or arrays
        result[newKey] = value;
      }
    }

    return result;
  },

  /**
   * Flatten an array of objects
   */
  flattenArray(arr) {
    return arr.map(obj => this.flattenObject(obj));
  },

  /**
   * Get all unique column keys from an array of flattened objects
   */
  getColumns(flattenedArray) {
    const columns = new Set();
    flattenedArray.forEach(obj => {
      Object.keys(obj).forEach(key => columns.add(key));
    });
    return Array.from(columns);
  },

  /**
   * Sort columns with bucket_name first, then alphabetically
   */
  sortColumns(columns, idColumn = 'bucket_name') {
    return columns.sort((a, b) => {
      if (a === idColumn) return -1;
      if (b === idColumn) return 1;
      return a.localeCompare(b);
    });
  },

  /**
   * Format column name for display (replace underscores with spaces, capitalize)
   */
  formatColumnName(columnKey) {
    return columnKey
      .split('_')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ');
  }
};
