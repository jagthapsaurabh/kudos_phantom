import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import App from './App';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Backtest from './pages/Backtest';
import PaperTrade from './pages/PaperTrade';
import Strategies from './pages/Strategies';
import BrokerSettings from './pages/BrokerSettings';
import Navbar from './components/Navbar';

const ProtectedRoute = ({ children }) => {
  const token = localStorage.getItem('token');
  return token ? <>{children}</> : <Navigate to="/login" />;
};

const root = createRoot(document.getElementById('root'));
root.render(
  <BrowserRouter>
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<ProtectedRoute><Navbar /><Dashboard /></ProtectedRoute>} />
      <Route path="/backtest" element={<ProtectedRoute><Navbar /><Backtest /></ProtectedRoute>} />
      <Route path="/paper" element={<ProtectedRoute><Navbar /><PaperTrade /></ProtectedRoute>} />
      <Route path="/strategies" element={<ProtectedRoute><Navbar /><Strategies /></ProtectedRoute>} />
      <Route path="/broker" element={<ProtectedRoute><Navbar /><BrokerSettings /></ProtectedRoute>} />
    </Routes>
  </BrowserRouter>
);
