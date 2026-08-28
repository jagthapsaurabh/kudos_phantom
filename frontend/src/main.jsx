import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import './index.css';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Backtest from './pages/Backtest';
import PaperTrade from './pages/PaperTrade';
import LiveTrade from './pages/LiveTrade';
import Terminal from './pages/Terminal';
import Strategies from './pages/Strategies';
import PhantomStrategy from './pages/PhantomStrategy';
import BrokerSettings from './pages/BrokerSettings';
import ChartPage from './pages/Chart';
import AdminPanel from './pages/AdminPanel';
import Navbar from './components/Navbar';

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

const root = createRoot(document.getElementById('root'));
root.render(
  <BrowserRouter>
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<ProtectedRoute><Navbar /><Dashboard /></ProtectedRoute>} />
      <Route path="/backtest" element={<ProtectedRoute><Navbar /><Backtest /></ProtectedRoute>} />
      <Route path="/paper" element={<ProtectedRoute><Navbar /><PaperTrade /></ProtectedRoute>} />
      <Route path="/live" element={<ProtectedRoute><Navbar /><LiveTrade /></ProtectedRoute>} />
      <Route path="/terminal" element={<ProtectedRoute><Navbar /><Terminal /></ProtectedRoute>} />
      <Route path="/chart" element={<ProtectedRoute><Navbar /><ChartPage /></ProtectedRoute>} />
      <Route path="/strategies" element={<ProtectedRoute><Navbar /><Strategies /></ProtectedRoute>} />
      <Route path="/strategy" element={<ProtectedRoute><Navbar /><PhantomStrategy /></ProtectedRoute>} />
      <Route path="/broker" element={<ProtectedRoute><Navbar /><BrokerSettings /></ProtectedRoute>} />
      <Route path="/admin" element={<AdminRoute><Navbar /><AdminPanel /></AdminRoute>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  </BrowserRouter>
);
