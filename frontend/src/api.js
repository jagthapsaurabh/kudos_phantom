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
  // Add request ID for tracing (backend logs it)
  config.headers['X-Request-ID'] = `fe-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
  return config;
});

// Response interceptor: consistent error handling across the app
// - 401: token expired/invalid → clear storage and redirect to login
// - 429: rate limited → surface with hint to retry
// - 500+: server error → surface error envelope with code for debugging
// - Network errors → surface as offline/unreachable
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status;
    const data = error?.response?.data;

    // 401 Unauthorized: session expired or invalid credentials
    if (status === 401) {
      // Don't redirect if already on login page or if it's the login request itself
      const isLoginRequest = error.config?.url?.includes('/token') || error.config?.url?.includes('/login');
      const isLoginPage = typeof window !== 'undefined' && window.location.pathname === '/login';
      if (!isLoginRequest && !isLoginPage) {
        localStorage.removeItem('token');
        localStorage.removeItem('role');
        // Use a custom event so components can show a toast before redirect
        if (typeof window !== 'undefined') {
          window.dispatchEvent(new CustomEvent('auth:expired', { detail: { message: data?.error || 'Session expired' } }));
          // Delay redirect slightly to allow toast to show
          setTimeout(() => {
            window.location.href = '/login';
          }, 800);
        }
      }
    }

    // 429 Rate Limited: surface with retry info
    if (status === 429) {
      const retryAfter = error.response?.headers?.['retry-after'] || data?.details?.retry_after || null;
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('api:rate-limited', {
          detail: { retryAfter, message: data?.error || 'Rate limited', hint: data?.hint }
        }));
      }
    }

    // 403 Forbidden: permission denied
    if (status === 403) {
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('api:forbidden', {
          detail: { message: data?.error || data?.detail || 'Access denied' }
        }));
      }
    }

    // 5xx: server error — log for debugging
    if (status >= 500) {
      console.error(`[API] Server error ${status}:`, data);
    }

    // Always reject so calling code can handle with try/catch or .catch()
    return Promise.reject(error);
  }
);

export { API_URL };
export default api;
