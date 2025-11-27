// API client for Kilimanjaro backend
const API_BASE_URL = 'http://localhost:8766';

export const api = {
  async fetchJobs(cb) {
    try {
      const response = await fetch(`${API_BASE_URL}/job/list`);
      const data = await response.json();
      cb(data);
    } catch (error) {
      console.error('Error fetching jobs:', error);
      cb([]);
    }
  },

  async fetchResults(cb) {
    try {
      const response = await fetch(`${API_BASE_URL}/result/list`);
      const data = await response.json();
      cb(data);
    } catch (error) {
      console.error('Error fetching results:', error);
      cb([]);
    }
  },

  async createJob(jobData) {
    try {
      const response = await fetch(`${API_BASE_URL}/job/new`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(jobData)
      });
      return await response.json();
    } catch (error) {
      console.error('Error creating job:', error);
      throw error;
    }
  },

  async deleteJob(jobId) {
    try {
      const response = await fetch(`${API_BASE_URL}/job/delete/${jobId}`, {
        method: 'DELETE'
      });
      return await response.json();
    } catch (error) {
      console.error('Error deleting job:', error);
      throw error;
    }
  },

  async deleteResult(bucketName) {
    try {
      const response = await fetch(`${API_BASE_URL}/result/${bucketName}`, {
        method: 'DELETE'
      });
      return await response.json();
    } catch (error) {
      console.error('Error deleting result:', error);
      throw error;
    }
  },

  async getJob(jobId) {
    try {
      const response = await fetch(`${API_BASE_URL}/job/${jobId}`);
      return await response.json();
    } catch (error) {
      console.error('Error fetching job:', error);
      throw error;
    }
  }
};
