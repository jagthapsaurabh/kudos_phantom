import axios from 'axios';

// Standardized API Base URL: env override wins; otherwise reuse the current
// host on port 8000 (works for localhost dev AND hosted port-based previews).
const resolveApiUrl = () => {
  if (import.meta.env.VITE_API_URL) return import.meta.env.VITE_API_URL;
  if (typeof window === 'undefined' || !window.location) return 'http://localhost:8000';
  const { protocol, hostname } = window.location;
  // Hosted preview pattern: "<port>-<id>.e2b.app" -> swap the leading port
  const m = hostname.match(/^\d+-(.+)$/);
  if (m) return `${protocol}//8000-${m[1]}`;
  return `${protocol}//${hostname}:8000`;
};
const API_URL = resolveApiUrl();

const api = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add a request interceptor to attach the Bearer token automatically to all requests
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export { API_URL };
export default api;
