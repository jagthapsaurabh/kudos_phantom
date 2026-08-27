import React, { useState } from 'react';
import { API_URL } from '../api';
import { TrendingUp, Eye, EyeOff } from 'lucide-react';

const Login = () => {
  const [form, setForm] = useState({ username: '', password: '' });
  const [error, setError] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    
    const formData = new FormData();
    formData.append('username', form.username);
    formData.append('password', form.password);

    try {
      const res = await fetch(`${API_URL}/token`, { 
        method: 'POST', 
        body: formData 
      });
      
      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || 'Invalid credentials');
      }
      
      const data = await res.json();
      localStorage.setItem('token', data.access_token);
      localStorage.setItem('role', data.role || 'client');
      localStorage.setItem('username', data.username || form.username);
      localStorage.setItem('can_live', String(data.can_live ?? 0));
      window.location.href = (data.role === 'admin') ? '/admin' : '/';
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  };

  return (
    <div className="min-h-screen bg-gray-900 flex items-center justify-center p-4 font-sans">
      <div className="bg-gray-800 p-8 rounded-2xl shadow-2xl border border-gray-700 w-full max-w-md">
        <div className="text-center mb-8">
          <div className="flex items-center justify-center gap-2 mb-2">
            <TrendingUp size={32} className="text-blue-500" />
            <h1 className="text-4xl font-extrabold text-blue-500">Kudos</h1>
          </div>
        </div>
        
        <form onSubmit={handleSubmit} className="space-y-6">
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">Username</label>
            <input 
              type="text" 
              className="w-full bg-gray-700 p-3 rounded-lg border border-gray-600 text-white focus:ring-2 focus:ring-blue-500 outline-none transition-all"
              value={form.username} 
              onChange={e => setForm({...form, username: e.target.value})} 
              required
              autoFocus
            />
          </div>
          <div className="relative">
            <label className="block text-sm font-medium text-gray-400 mb-1">Password</label>
            <input 
              type={showPw ? 'text' : 'password'}
              className="w-full bg-gray-700 p-3 rounded-lg border border-gray-600 text-white focus:ring-2 focus:ring-blue-500 outline-none transition-all pr-10"
              value={form.password} 
              onChange={e => setForm({...form, password: e.target.value})} 
              required
            />
            <button type="button" onClick={() => setShowPw(!showPw)} className="absolute right-3 bottom-3 text-gray-500 hover:text-gray-300">
              {showPw ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          
          {error && (
            <div className="bg-red-900/30 border border-red-500 text-red-400 p-3 rounded-lg text-sm text-center">
              {error}
            </div>
          )}
          
          <button 
            type="submit"
            disabled={loading}
            className="w-full bg-blue-600 hover:bg-blue-500 text-white py-3 rounded-lg font-bold text-lg transition-all shadow-lg shadow-blue-900/20 active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? 'Authenticating…' : 'Initialize Session'}
          </button>
        </form>
        
        <div className="mt-8 text-center text-gray-500 text-xs">
          Secure Access Only &bull; Kudos v3
        </div>
      </div>
    </div>
  );
};

export default Login;
