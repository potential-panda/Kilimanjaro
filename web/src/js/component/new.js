import { getAvailableStrategies, getStrategyTemplate } from '../strategy_tmpl';
import { API_CONFIG } from '../common.js';

function getFormData($container) {
  const $form = $container.find('form');
  const data = {};

  // Update basic form fields
  data.strategy_name = $form.find('select[name="strategy_name"]').val();
  data.variant = $form.find('input[name="variant"]').val();
  data.from_dt = $form.find('input[name="from_dt"]').val();
  data.to_dt = $form.find('input[name="to_dt"]').val();
  data.optimize = $form.find('input[name="optimize"]').is(':checked');
  data.retest = $form.find('input[name="retest"]').is(':checked');

  // Update config
  data.config = [];
  const configElements = $form.find('input[name="config_value[]"], select[name="config_value[]"]');

  configElements.each(function() {
    const $element = $(this);
    const name = $element.data('config-name');
    const value = $element.val();

    if (name && value !== undefined) {
      // Check if this is a select element to determine if it should be an array
      const isSelect = $element.is('select');
      data.config.push({
        name: name,
        is_select: isSelect,
        default_value: value,
        options: isSelect ? $element.find('option').map(function() { return $(this).val(); }).get() : undefined
      });
    }
  });

  // Update parameter grid
  data.param_grid = [];
  const paramNames = $form.find('input[name="param_name[]"]');
  const paramDefaults = $form.find('input[name="param_default[]"]');
  const paramValues = $form.find('input[name="param_values[]"]');
  const paramFactors = $form.find('input[name="param_factor[]"]');

  for (let i = 0; i < paramNames.length; i++) {
    const name = $(paramNames[i]).val().trim();
    if (name) {
      data.param_grid.push({
        name: name,
        default_value: $(paramDefaults[i]).val(),
        values: $(paramValues[i]).val(),
        factor: $(paramFactors[i]).val()
      });
    }
  }

  return data;
}

W.component('new', {
  init: function() {
    // Check if there's cloned job data in sessionStorage
    const clonedJobDataStr = sessionStorage.getItem('cloneJobData');
    if (clonedJobDataStr) {
      // Load cloned job data
      const clonedJobData = JSON.parse(clonedJobDataStr);
      sessionStorage.removeItem('cloneJobData'); // Clear after reading
      this.initializeWithClonedData(clonedJobData);
      return;
    }

    // Normal initialization with defaults
    this.initializeWithDefaults();
  },

  initializeWithDefaults: function() {
    // Get yesterday's date in YYYY-MM-DD format
    const today = new Date();
    const fromDay = new Date();
    const toDay = new Date();
    fromDay.setDate(today.getDate() - 8);
    toDay.setDate(today.getDate() - 1);
    const fromDt = fromDay.toISOString().split('T')[0];
    const toDt = toDay.toISOString().split('T')[0];

    const strategies = getAvailableStrategies();
    const defaultStrategy = strategies[0];
    const strategyTemplate = getStrategyTemplate(defaultStrategy);

    this.data = {
      strategy_name: defaultStrategy,
      variant: strategyTemplate.variant,
      from_dt: fromDt,
      to_dt: toDt,
      optimize: true,
      retest: false,
      config: strategyTemplate.config,
      param_grid: strategyTemplate.param_grid,
      available_strategies: strategies
    };

    // Initialize jobData to be synced with the form
    const config = strategyTemplate.config.reduce((acc, configItem) => {
      acc[configItem.name] = configItem.default_value;
      return acc;
    }, {});
    config.params = {};

    this.jobData = {
      strategy_name: defaultStrategy,
      variant: strategyTemplate.variant,
      from_dt: fromDt,
      to_dt: toDt,
      optimize: true,
      retest: false,
      config: config,
      param_grid: strategyTemplate.param_grid.map(param => ({
        name: param.name,
        default_value: param.default_value,
        values: param.values.split(',').map(v => v.trim()),
        factor: parseInt(param.factor) || 1000
      }))
    };
  },

  initializeWithClonedData: function(jobData) {
    const strategies = getAvailableStrategies();

    // Convert config object to array format for template
    const configArray = [];
    for (const [key, value] of Object.entries(jobData.config || {})) {
      if (key !== 'params') {
        configArray.push({
          name: key,
          default_value: value,
          is_select: false
        });
      }
    }

    // Convert param_grid to template format
    const paramGridArray = (jobData.param_grid || []).map(param => ({
      name: param.name,
      default_value: param.default_value,
      values: Array.isArray(param.values) ? param.values.join(', ') : param.values,
      factor: param.factor
    }));

    this.data = {
      strategy_name: jobData.strategy_name,
      variant: jobData.variant,
      from_dt: jobData.from_dt,
      to_dt: jobData.to_dt || '',
      optimize: jobData.optimize,
      retest: jobData.retest,
      config: configArray,
      param_grid: paramGridArray,
      available_strategies: strategies
    };

    // Set jobData
    this.jobData = JSON.parse(JSON.stringify(jobData));
  },

  loadClonedJob: function(jobData) {
    // Update data with cloned job settings
    this.data.strategy_name = jobData.strategy_name;
    this.data.variant = jobData.variant;
    this.data.from_dt = jobData.from_dt;
    this.data.to_dt = jobData.to_dt || '';
    this.data.optimize = jobData.optimize;
    this.data.retest = jobData.retest;

    // Convert config object to array format for template
    const configArray = [];
    for (const [key, value] of Object.entries(jobData.config)) {
      if (key !== 'params') {
        configArray.push({
          name: key,
          default_value: value,
          is_select: false
        });
      }
    }
    this.data.config = configArray;

    // Convert param_grid to template format
    this.data.param_grid = jobData.param_grid.map(param => ({
      name: param.name,
      default_value: param.default_value,
      values: Array.isArray(param.values) ? param.values.join(', ') : param.values,
      factor: param.factor
    }));

    // Update jobData
    this.jobData = JSON.parse(JSON.stringify(jobData));

    // Reload the component with cloned data
    this.load();
  },

  getData: function(cb) {
    cb(this.data);
  },

  rendered: function(cb) {
    const $container = $(this.el);

    // Sync basic form fields with jobData
    const syncBasicFields = () => {
      this.jobData.strategy_name = $container.find('select[name="strategy_name"]').val();
      this.jobData.variant = $container.find('input[name="variant"]').val();
      this.jobData.from_dt = $container.find('input[name="from_dt"]').val();
      this.jobData.to_dt = $container.find('input[name="to_dt"]').val();
      this.jobData.optimize = $container.find('input[name="optimize"]').is(':checked');
      this.jobData.retest = $container.find('input[name="retest"]').is(':checked');
    };

    // Sync config with jobData
    const syncConfig = () => {
      const config = {};
      const configElements = $container.find('input[name="config_value[]"], select[name="config_value[]"]');

      configElements.each(function() {
        const $element = $(this);
        const name = $element.data('config-name');
        const value = $element.val();
        if (name && value !== undefined) {
          config[name] = value;
        }
      });
      config.params = {};
      this.jobData.config = config;
    };

    // Sync param_grid with jobData
    const syncParamGrid = () => {
      const paramGrid = [];
      const paramNames = $container.find('input[name="param_name[]"]');
      const paramDefaults = $container.find('input[name="param_default[]"]');
      const paramValues = $container.find('input[name="param_values[]"]');
      const paramFactors = $container.find('input[name="param_factor[]"]');

      for (let i = 0; i < paramNames.length; i++) {
        const name = $(paramNames[i]).val().trim();
        if (name) {
          paramGrid.push({
            name: name,
            default_value: $(paramDefaults[i]).val(),
            values: $(paramValues[i]).val().split(',').map(v => v.trim()),
            factor: parseInt($(paramFactors[i]).val()) || 1000
          });
        }
      }
      this.jobData.param_grid = paramGrid;
    };

    // Set initial value
    $container.find('select[name="strategy_name"]').val(this.data.strategy_name);

    // Strategy selection handler
    $container.find('select[name="strategy_name"]').on('change', (ev) => {
      const selectedStrategy = $(ev.target).val();
      const strategyTemplate = getStrategyTemplate(selectedStrategy);

      this.data.strategy_name = selectedStrategy;
      this.data.variant = strategyTemplate.variant;
      this.data.config = strategyTemplate.config;
      this.data.param_grid = strategyTemplate.param_grid;

      // Update jobData config
      const config = strategyTemplate.config.reduce((acc, configItem) => {
        acc[configItem.name] = configItem.default_value;
        return acc;
      }, {});
      config.params = {};
      this.jobData.config = config;
      this.jobData.strategy_name = selectedStrategy;
      this.jobData.variant = strategyTemplate.variant;
      this.jobData.param_grid = strategyTemplate.param_grid.map(param => ({
        name: param.name,
        default_value: param.default_value,
        values: param.values.split(',').map(v => v.trim()),
        factor: parseInt(param.factor) || 1000
      }));

      this.load();
    });

    // Add event listeners for basic form fields
    $container.find('input[name="variant"]').on('input', syncBasicFields);
    $container.find('input[name="from_dt"]').on('change', syncBasicFields);
    $container.find('input[name="to_dt"]').on('change', syncBasicFields);
    $container.find('input[name="optimize"]').on('change', syncBasicFields);
    $container.find('input[name="retest"]').on('change', syncBasicFields);

    // Add event listeners for config fields
    $container.find('input[name="config_value[]"], select[name="config_value[]"]').on('input change', syncConfig);

    // Add event listeners for param_grid fields
    $container.find('input[name="param_name[]"], input[name="param_default[]"], input[name="param_values[]"], input[name="param_factor[]"]').on('input', syncParamGrid);

    $container.find('.add-param').on('click', () => {
      const paramGrid = this.data.param_grid;
      paramGrid.push({
        name: '',
        default_value: '',
        values: '',
        factor: '1000'
      });

      // Also update jobData
      this.jobData.param_grid.push({
        name: '',
        default_value: '',
        values: [],
        factor: 1000
      });

      this.load();
    });

    // Remove param set handler
    $container.find('.remove-param').on('click', (ev) => {
      const index = $(ev.target).data('index');
      this.data.param_grid.splice(index, 1);
      this.jobData.param_grid.splice(index, 1);
      this.load();
    });

    // Submit form handler
    $container.find('.submit-job').on('click', (ev) => {
      // Sync all fields one final time before submitting
      syncBasicFields();
      syncConfig();
      syncParamGrid();

      // Submit to API using this.jobData
      $.ajax({
        url: API_CONFIG.ROOT + 'job/new',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(this.jobData),
        success: function(response) {
          // Show success message
          const $alert = $container.find('.success-alert');
          const $message = $container.find('.success-message');

          $message.text('Job created successfully!');
          $alert.removeClass('d-none');

          // Scroll to top to show the alert
          $container[0].scrollIntoView({ behavior: 'smooth', block: 'start' });

          // Auto-dismiss after 3 seconds
          setTimeout(() => {
            $alert.addClass('d-none');
          }, 3000);
        },
        error: function(xhr) {
          alert('Error creating job: ' + (xhr.responseJSON?.error || 'Unknown error'));
        }
      });
    });

    cb();
  },
});
