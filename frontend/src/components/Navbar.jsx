import React, { useState } from 'react';
import { LayoutDashboard, PlayCircle, Activity, Settings, LogOut, TrendingUp, ShieldCheck, Users } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';

const ConfirmModal = ({ open, title, message, confirmLabel, confirmColor, onCancel, onConfirm }) => {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onCancel}>
      <div className="bg-gray-800 border border-gray-700 rounded-2xl shadow-2xl p-6 max-w-md w-full mx-4" onClick={e => e.stopPropagation()}>
        <h3 className="text-lg font-bold text-white mb-2">{title}</h3>
        <p className="text-sm text-gray-400 mb-6">{message}</p>
        <div className="flex justify-end gap-3">
          <button onClick={onCancel} className="px-4 py-2 rounded-lg text-sm font-semibold text-gray-300 hover:text-white bg-gray-700 hover:bg-gray-600 transition">Cancel</button>
          <button onClick={onConfirm} className={`px-4 py-2 rounded-lg text-sm font-bold text-white transition ${confirmColor}`}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
};

const Navbar = () => {
  const navigate = useNavigate();
  const role = localStorage.getItem('role') || 'client';
  const [showLogout, setShowLogout] = useState(false);

  const handleLogout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('role');
    localStorage.removeItem('username');
    localStorage.removeItem('can_live');
    navigate('/login');
  };

  const navItems = [
    { name: 'Dashboard', path: '/', icon: <LayoutDashboard size={20} /> },
    { name: 'Backtesting', path: '/backtest', icon: <TrendingUp size={20} /> },
    { name: 'Paper Trade', path: '/paper', icon: <Activity size={20} /> },
    { name: 'Strategies', path: '/strategies', icon: <PlayCircle size={20} /> },
    { name: 'Broker', path: '/broker', icon: <Settings size={20} /> },
    ...(role === 'admin' ? [{ name: 'Admin Panel', path: '/admin', icon: <Users size={20} /> }] : []),
  ];

  return (
    <>
      <ConfirmModal
        open={showLogout}
        title="Log Out?"
        message="Are you sure you want to end your session? Any running paper trade instances will continue in the background."
        confirmLabel="Yes, Log Out"
        confirmColor="bg-red-600 hover:bg-red-500"
        onCancel={() => setShowLogout(false)}
        onConfirm={handleLogout}
      />
      <nav className="bg-gray-900 border-b border-gray-800 text-gray-400 w-64 fixed left-0 top-0 h-full p-4 flex flex-col">
        <div className="text-2xl font-bold text-blue-500 mb-10 px-2 flex items-center gap-2">
          <TrendingUp size={28} /> PHANTOM v3
        </div>
        <div className="flex-1 space-y-2">
          {navItems.map(item => (
            <Link key={item.path} to={item.path}
                  className="flex items-center gap-3 p-3 rounded-lg hover:bg-gray-800 hover:text-white transition-all">
              {item.icon} {item.name}
            </Link>
          ))}
        </div>
        <div className="px-3 py-2 text-xs text-gray-600 border-t border-gray-800 mt-2 pt-3">
          Signed in as <span className="text-gray-400 font-semibold">{localStorage.getItem('username') || '—'}</span>
          <span className={`ml-2 px-2 py-0.5 rounded text-[10px] font-bold uppercase ${role === 'admin' ? 'bg-purple-900/50 text-purple-400' : 'bg-blue-900/50 text-blue-400'}`}>{role}</span>
        </div>
        <button onClick={() => setShowLogout(true)} className="flex items-center gap-3 p-3 rounded-lg hover:bg-red-900/20 hover:text-red-400 transition-all text-left">
          <LogOut size={20} /> Logout
        </button>
      </nav>
    </>
  );
};

export default Navbar;
