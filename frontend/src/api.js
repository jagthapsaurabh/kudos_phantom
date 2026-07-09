import axios from 'axios';

// Use environment variable for the API base URL to avoid hardcoding and allow easy port changes
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://72.60.195.95:8001';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add a request interceptor to attach the Bearer token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export default api;
