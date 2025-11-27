// API configuration
// Uses environment variable API_URL set at build time, defaults to localhost
const API_BASE_URL = process.env.API_URL || 'http://localhost:8766';

export const API_CONFIG = {
  // API root URL - configured via process.env.API_URL at build time
  ROOT: API_BASE_URL.endsWith('/') ? API_BASE_URL : `${API_BASE_URL}/`,
  a: "1"
};

// Grafana configuration
export const GRAFANA_CONFIG = {
  // Grafana dashboard base URL for Orion Compare dashboard
  DASHBOARD_BASE_URL: "http://localhost:3000/d/cc34f1d6-0d49-4539-b5ba-5a28fba22fbb/orion-compare?orgId=1&var-bucket="
};
