const API_BASE_URL = process.env.API_URL || 'http://localhost:8766';

export const API_CONFIG = {
  ROOT: API_BASE_URL.endsWith('/') ? API_BASE_URL : `${API_BASE_URL}/`,
};

// Grafana configuration
export const GRAFANA_CONFIG = {
  ORION_BASE_URL: "http://localhost:3000/d/cc34f1d6-0d49-4539-b5ba-5a28fba22fbb/orion-compare?orgId=1&var-bucket=",
  LYRA_BASE_URL: "http://localhost:3000/d/9d1188ff-f658-491b-9fa7-1db4a71d9f83/lyra?orgId=1&var-bucket=",
  GEMINI_BASE_URL: "http://localhost:3000/d/79d2fb04-821b-4330-a34f-eace346d95a6/gemini?orgId=1&var-bucket=",
};
