import React from 'react';
import { LayoutDashboard, PlayCircle, Activity, Settings, LogOut, TrendingUp, ShieldCheck } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';

const Navbar = () => {
  const navigate = useNavigate();
  const handleLogout = () => {
    localStorage.removeItem('token');
    navigate('/login');
  };

  const navItems = [
    { name: 'Dashboard', path: '/', icon: <LayoutDashboard size={20} /> },
    { name: 'Backtesting', path: '/backtest', icon: <TrendingUp size={20} /> },
    { name: 'Paper Trade', path: '/paper', icon: <Activity size={20} /> },
    { name: 'Live Trade', path: '/live', icon: <ShieldCheck size={20} /> },
    { name: 'Market Chart', path: '/chart', icon: <TrendingUp size={20} /> },
    { name: 'Strategies', path: '/strategies', icon: <PlayCircle size={20} /> },
    { name: 'Broker', path: '/broker', icon: <Settings size={20} /> },
  ];

  return (
    <nav className="bg-gray-900 border-b border-gray-800 text-gray-400 w-64 fixed left-0 top-0 h-full p-4 flex flex-col">
      <div className="text-2xl font-bold text-blue-500 mb-10 px-2 flex items-center gap-2">
        <TrendingUp size={28} /> PHANTOM v2.5
      </div>
      <div className="flex-1 space-y-2">
        {navItems.map(item => (
          <Link key={item.path} to={item.path} 
                className="flex items-center gap-3 p-3 rounded-lg hover:bg-gray-800 hover:text-white transition-all">
            {item.icon} {item.name}
          </Link>
        ))}
      </div>
      <button onClick={handleLogout} className="flex items-center gap-3 p-3 rounded-lg hover:bg-red-900/20 hover:text-red-400 transition-all text-left">
        <LogOut size={20} /> Logout
      </button>
    </nav>
  );
};

export default Navbar;
