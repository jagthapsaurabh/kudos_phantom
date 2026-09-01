import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import './index.css';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Backtest from './pages/Backtest';
import PaperTrade from './pages/PaperTrade';
import LiveTrade from './pages/LiveTrade';
import Sessions from './pages/Sessions';
import Strategies from './pages/Strategies';
import PhantomStrategy from './pages/PhantomStrategy';
import BrokerSettings from './pages/BrokerSettings';
import ChartPage from './pages/Chart';
import AdminPanel from './pages/AdminPanel';
import Navbar from './components/Navbar';
import ErrorBoundary from './components/ErrorBoundary';
import ToastContainer from './components/ToastContainer';

const ProtectedRoute = ({ children }) => {
  const token = localStorage.getItem('token');
  return token ? <>{children}</> : <Navigate to="/login" replace />;
};

const AdminRoute = ({ children }) => {
  const token = localStorage.getItem('token');
  const role = localStorage.getItem('role');
  if (!token) return <Navigate to="/login" replace />;
  return role === 'admin' ? <>{children}</> : <Navigate to="/" replace />;
};

// Global toast listener for api.js interceptor events (401, 429, 403)
const GlobalToastListener = () => {
  const [toasts, setToasts] = useState([]);

  useEffect(() => {
    const push = (msg, type, code, hint, duration) => {
      const id = Date.now() + Math.random();
      setToasts(prev => [...prev, { id, message: msg, type, code, hint, duration }]);
      if (duration) setTimeout(() => setToasts(prev => prev.filter(x => x.id !== id)), duration);
    };
    const onExpired = (e) => push(e.detail?.message || 'Session expired — please login again', 'warning', 'AUTH_EXPIRED', null, 3000);
    const onRateLimited = (e) => push(e.detail?.message || 'Rate limited', 'warning', 'RATE_LIMITED', e.detail?.hint || (e.detail?.retryAfter ? `Retry after ${e.detail.retryAfter}s` : null), 6000);
    const onForbidden = (e) => push(e.detail?.message || 'Access denied', 'error', 'FORBIDDEN', null, 4000);
    window.addEventListener('auth:expired', onExpired);
    window.addEventListener('api:rate-limited', onRateLimited);
    window.addEventListener('api:forbidden', onForbidden);
    return () => {
      window.removeEventListener('auth:expired', onExpired);
      window.removeEventListener('api:rate-limited', onRateLimited);
      window.removeEventListener('api:forbidden', onForbidden);
    };
  }, []);

  return <ToastContainer toasts={toasts} onRemove={(id) => setToasts(prev => prev.filter(t => t.id !== id))} />;
};

const root = createRoot(document.getElementById('root'));
root.render(
  <ErrorBoundary fallbackMessage="Application crashed. Please reload the page.">
    <BrowserRouter>
      <GlobalToastListener />
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<ProtectedRoute><Navbar /><Dashboard /></ProtectedRoute>} />
        <Route path="/backtest" element={<ProtectedRoute><Navbar /><Backtest /></ProtectedRoute>} />
        <Route path="/paper" element={<ProtectedRoute><Navbar /><PaperTrade /></ProtectedRoute>} />
        <Route path="/live" element={<ProtectedRoute><Navbar /><LiveTrade /></ProtectedRoute>} />
        {/* The terminal is now a tab inside Live Trading; keep the old URL
            working so existing bookmarks land in the right place. */}
        <Route path="/terminal" element={<Navigate to="/live" replace />} />
        <Route path="/sessions" element={<ProtectedRoute><Navbar /><Sessions /></ProtectedRoute>} />
        <Route path="/chart" element={<ProtectedRoute><Navbar /><ChartPage /></ProtectedRoute>} />
        <Route path="/strategies" element={<ProtectedRoute><Navbar /><Strategies /></ProtectedRoute>} />
        <Route path="/strategy" element={<ProtectedRoute><Navbar /><PhantomStrategy /></ProtectedRoute>} />
        <Route path="/broker" element={<ProtectedRoute><Navbar /><BrokerSettings /></ProtectedRoute>} />
        <Route path="/admin" element={<AdminRoute><Navbar /><AdminPanel /></AdminRoute>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  </ErrorBoundary>
);
