// Utility for managing hidden columns in localStorage

const STORAGE_KEY = 'kilimanjaro_hidden_columns';

export const hiddenColumnsStorage = {
  /**
   * Get hidden columns from localStorage
   */
  getHiddenColumns() {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored ? JSON.parse(stored) : [];
    } catch (error) {
      console.error('Error reading hidden columns from localStorage:', error);
      return [];
    }
  },

  /**
   * Set hidden columns in localStorage
   */
  setHiddenColumns(columns) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(columns));
    } catch (error) {
      console.error('Error saving hidden columns to localStorage:', error);
    }
  },

  /**
   * Add a column to hidden columns list
   */
  addHiddenColumn(columnKey) {
    const hiddenColumns = this.getHiddenColumns();
    if (!hiddenColumns.includes(columnKey)) {
      hiddenColumns.push(columnKey);
      this.setHiddenColumns(hiddenColumns);
    }
  },

  /**
   * Remove a column from hidden columns list
   */
  removeHiddenColumn(columnKey) {
    const hiddenColumns = this.getHiddenColumns();
    const filtered = hiddenColumns.filter(col => col !== columnKey);
    this.setHiddenColumns(filtered);
  },

  /**
   * Clear all hidden columns
   */
  clearHiddenColumns() {
    this.setHiddenColumns([]);
  }
};
