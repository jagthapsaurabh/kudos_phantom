import { useState, useCallback } from 'react';

let toastId = 0;

/**
 * useToast - simple toast notification hook
 * Provides: toasts array, addToast, removeToast, clearToasts
 * 
 * Toast shape: { id, type: 'info'|'success'|'warning'|'error', message, code, details }
 */
export default function useToast() {
  const [toasts, setToasts] = useState([]);

  const removeToast = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const addToast = useCallback((message, opts = {}) => {
    const id = ++toastId;
    const toast = {
      id,
      type: opts.type || 'info',
      message: message || 'Unknown error',
      code: opts.code || null,
      details: opts.details || null,
      hint: opts.hint || null,
      duration: opts.duration ?? 5000,
    };
    setToasts(prev => [...prev, toast]);
    if (toast.duration > 0) {
      setTimeout(() => removeToast(id), toast.duration);
    }
    return id;
  }, [removeToast]);

  const clearToasts = useCallback(() => setToasts([]), []);

  // Helper: parse error envelope from backend
  const toastFromError = useCallback((error, fallback = 'Request failed') => {
    if (!error) return addToast(fallback, { type: 'error' });
    
    // Axios error with response
    if (error.response) {
      const data = error.response.data;
      const status = error.response.status;
      
      // New error envelope: { error, code, timestamp, details, hint }
      if (data && typeof data === 'object' && data.error) {
        return addToast(data.error, {
          type: status >= 500 ? 'error' : status === 429 ? 'warning' : 'error',
          code: data.code,
          details: data.details,
          hint: data.hint,
          duration: status >= 500 ? 8000 : 5000,
        });
      }
      
      // Legacy: { detail: string }
      if (data && data.detail) {
        const msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
        return addToast(msg, { type: status >= 500 ? 'error' : 'error', code: `HTTP_${status}` });
      }
      
      return addToast(`HTTP ${status}: ${fallback}`, { type: 'error', code: `HTTP_${status}` });
    }
    
    // Network error
    if (error.request) {
      return addToast('Network error — server unreachable', { type: 'error', code: 'NETWORK_ERROR', duration: 8000 });
    }
    
    // Generic
    const msg = error.message || fallback;
    return addToast(msg, { type: 'error' });
  }, [addToast]);

  return { toasts, addToast, removeToast, clearToasts, toastFromError };
}
