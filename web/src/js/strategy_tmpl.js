/**
 * Strategy templates with parameter grids for different trading strategies
 */

const STRATEGY_TEMPLATES = {
  orion: {
    variant: "v601",
    config: [
      {
        name: "market",
        value: [
          "GMO:XRP_JPY",
          "GMO:ETH_JPY",
          "GMO:BTC_JPY",
          "GMO:SOL_JPY",
          "GMO:BCH_JPY",
          "GMO:LTC_JPY",
          "COINCHECK:BTC_SPOT",
          "COINCHECK:ETH_SPOT",
          "COINCHECK:XRP_SPOT",
          "HUOBI:BTC_SPOT",
          "HUOBI:ETH_SPOT",
          "HUOBI:XRP_SPOT",
        ],
      },
      {
        name: "ref_market",
        value: [
          "BINANCE:XRP_USDT",
          "BINANCE:ETH_USDT",
          "BINANCE:BTC_USDT",
          "BINANCE:SOL_USDT",
          "BINANCE:BCH_USDT",
          "BINANCE:LTC_USDT",
        ],
      },
      {
        name: "order_size",
        value: 0.01,
      },
      {
        name: "magnifier",
        value: [5.0, 3.0, 10.0],
      },
    ],
    param_grid: [
      {
        name: 'acceleration',
        default_value: '0.02',
        values: '0.02',
        factor: '1000'
      },
      {
        name: 'step',
        default_value: '0.04',
        values: '0.02,0.04',
        factor: '1000'
      },
      {
        name: 'maximum',
        default_value: '0.2',
        values: '0.2',
        factor: '1000'
      },
      {
        name: 'box_size_rate',
        default_value: '0.0015',
        values: '0.001,0.0015,0.002,0.0025,0.003',
        factor: '100000'
      }
    ],
  },
};

/**
 * Get parameter grid template for a strategy
 * @param {string} strategyName - Name of the strategy
 * @returns {Array} Parameter grid array, or default template if strategy not found
 */
export function getParamGridTemplate(strategyName) {
  const template = STRATEGY_TEMPLATES[strategyName];

  if (!template || !template.param_grid) {
    // Return a default template if strategy not found
    return [
      {
        name: 'example_param',
        default_value: '1.0',
        values: '1.0,1.5,2.0',
        factor: '1000'
      }
    ];
  }

  // Return a deep copy to avoid modifying the original template
  return JSON.parse(JSON.stringify(template.param_grid));
}

/**
 * Get strategy template (includes variant, config, and param_grid)
 * @param {string} strategyName - Name of the strategy
 * @returns {Object} Strategy template object
 */
export function getStrategyTemplate(strategyName) {
  const template = STRATEGY_TEMPLATES[strategyName];

  if (!template) {
    return {
      variant: 'v0',
      config: [],
      param_grid: [
        {
          name: 'example_param',
          default_value: '1.0',
          values: '1.0,1.5,2.0',
          factor: '1000'
        }
      ]
    };
  }

  // Transform config for template rendering
  const transformedConfig = template.config ? template.config.map(configItem => {
    if (Array.isArray(configItem.value)) {
      return {
        name: configItem.name,
        is_select: true,
        options: configItem.value,
        default_value: configItem.value[0]
      };
    } else {
      return {
        name: configItem.name,
        is_select: false,
        default_value: configItem.value
      };
    }
  }) : [];

  return {
    variant: template.variant,
    config: transformedConfig,
    param_grid: JSON.parse(JSON.stringify(template.param_grid))
  };
}

/**
 * Get config template for a strategy
 * @param {string} strategyName - Name of the strategy
 * @returns {Array} Config array
 */
export function getConfigTemplate(strategyName) {
  const template = STRATEGY_TEMPLATES[strategyName];
  return template?.config ? JSON.parse(JSON.stringify(template.config)) : [];
}

/**
 * Get list of available strategy names
 * @returns {Array} Array of strategy names
 */
export function getAvailableStrategies() {
  return Object.keys(STRATEGY_TEMPLATES);
}

