import React, { useEffect, useState } from 'react';
import { LayoutDashboard, PlayCircle, Activity, Settings, LogOut, TrendingUp, Users, LineChart, Radio, Menu, X, ChevronLeft, ChevronRight, BookOpen, TerminalSquare } from 'lucide-react';
import { Link, useLocation, useNavigate } from 'react-router-dom';

const ConfirmModal = ({ open, title, message, confirmLabel, confirmColor, onCancel, onConfirm }) => {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onCancel}>
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
  const location = useLocation();
  const role = localStorage.getItem('role') || 'client';
  const canLive = localStorage.getItem('can_live') === '1' || role === 'admin';
  const [showLogout, setShowLogout] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window === 'undefined') return false;
    const saved = localStorage.getItem('sidebar_collapsed');
    if (saved != null) return saved === '1';
    return window.innerWidth < 1280;
  });

  useEffect(() => {
    document.documentElement.classList.toggle('sidebar-collapsed', collapsed);
    localStorage.setItem('sidebar_collapsed', collapsed ? '1' : '0');
  }, [collapsed]);

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    const onResize = () => {
      if (window.innerWidth < 768) setMobileOpen(false);
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const handleLogout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('role');
    localStorage.removeItem('username');
    localStorage.removeItem('can_live');
    navigate('/login');
  };

  const navItems = [
    { name: 'Dashboard', path: '/', icon: LayoutDashboard },
    { name: 'Market Chart', path: '/chart', icon: LineChart },
    { name: 'Backtest', path: '/backtest', icon: TrendingUp },
    { name: 'Paper Trade', path: '/paper', icon: Activity },
    ...(canLive ? [{ name: 'Live Trade', path: '/live', icon: Radio },
                   { name: 'Terminal', path: '/terminal', icon: TerminalSquare }] : []),
    { name: 'Strategies', path: '/strategies', icon: PlayCircle },
    { name: 'Kudos Strategy', path: '/strategy', icon: BookOpen },
    { name: 'Broker', path: '/broker', icon: Settings },
    ...(role === 'admin' ? [{ name: 'Admin', path: '/admin', icon: Users }] : []),
  ];

  const isActive = (path) => (path === '/' ? location.pathname === '/' : (location.pathname === path || location.pathname.startsWith(path + '/')));
  const slim = collapsed && !mobileOpen;

  const sidebar = (
    <nav className={`bg-gray-950 border-r border-gray-800 text-gray-400 h-full p-2.5 flex flex-col ${slim ? 'w-[68px]' : 'w-[200px]'}`}>
      <div className={`mb-6 mt-1 flex items-center ${slim ? 'justify-center px-0' : 'gap-2 px-2'}`}>
        <TrendingUp size={22} className="text-blue-500 shrink-0" />
        {!slim && <div className="text-lg font-bold text-blue-500 leading-tight tracking-tight">Kudos</div>}
      </div>
      <div className="flex-1 space-y-0.5 overflow-y-auto">
        {navItems.map(item => {
          const Icon = item.icon;
          const active = isActive(item.path);
          return (
            <Link
              key={item.path}
              to={item.path}
              title={item.name}
              className={`flex items-center rounded-lg transition-all ${slim ? 'justify-center p-2.5' : 'gap-2.5 px-2.5 py-2'} ${
                active ? 'bg-blue-600/20 text-white border border-blue-500/30' : 'hover:bg-gray-800 hover:text-white border border-transparent'
              }`}
            >
              <Icon size={18} className={active ? 'text-blue-400' : ''} />
              {!slim && <span className="text-[13px] font-medium truncate">{item.name}</span>}
            </Link>
          );
        })}
      </div>
      {!slim && (
        <div className="px-2 py-2 text-[10px] text-gray-600 border-t border-gray-800 mt-2">
          <span className="text-gray-400 font-semibold">{localStorage.getItem('username') || '—'}</span>
          <span className={`ml-1.5 px-1.5 py-0.5 rounded text-[9px] font-bold uppercase ${role === 'admin' ? 'bg-purple-900/50 text-purple-400' : 'bg-blue-900/50 text-blue-400'}`}>{role}</span>
        </div>
      )}
      <button
        onClick={() => setShowLogout(true)}
        title="Logout"
        className={`flex items-center rounded-lg hover:bg-red-900/20 hover:text-red-400 transition-all text-left ${slim ? 'justify-center p-2.5' : 'gap-2.5 px-2.5 py-2'}`}
      >
        <LogOut size={18} />
        {!slim && <span className="text-[13px] font-medium">Logout</span>}
      </button>
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="hidden md:flex items-center justify-center mt-1 p-1.5 rounded-lg text-gray-500 hover:text-white hover:bg-gray-800"
        title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
      </button>
    </nav>
  );

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

      {/* Mobile top bar */}
      <div className="md:hidden fixed top-0 left-0 right-0 z-50 h-12 bg-gray-950/95 border-b border-gray-800 flex items-center px-3 gap-2 backdrop-blur">
        <button onClick={() => setMobileOpen(true)} className="p-1.5 rounded-lg text-gray-300 hover:bg-gray-800" aria-label="Open menu">
          <Menu size={20} />
        </button>
        <TrendingUp size={18} className="text-blue-500" />
        <span className="text-sm font-bold text-blue-400">Kudos</span>
      </div>

      {/* Desktop / tablet sidebar */}
      <div className="hidden md:block fixed left-0 top-0 h-full z-40">
        {sidebar}
      </div>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="md:hidden fixed inset-0 z-[60]">
          <div className="absolute inset-0 bg-black/60" onClick={() => setMobileOpen(false)} />
          <div className="absolute left-0 top-0 h-full shadow-2xl">
            <button onClick={() => setMobileOpen(false)} className="absolute top-3 right-3 z-10 p-1 text-gray-400 hover:text-white">
              <X size={18} />
            </button>
            {sidebar}
          </div>
        </div>
      )}
    </>
  );
};

export default Navbar;
