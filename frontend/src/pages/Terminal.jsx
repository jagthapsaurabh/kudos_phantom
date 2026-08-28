import React, { useEffect, useState } from 'react';
import { TerminalSquare } from 'lucide-react';
import { API_URL } from '../api';
import LiveTerminal, { perpetualFor } from '../components/LiveTerminal';

// The trading terminal talks straight to the live broker account: positions,
// open orders, stop orders, fills, order history, wallet and margin, rendered
// the way an exchange terminal shows them.
const Terminal = () => {
  const [sources, setSources] = useState([{ code: 'Binance', name: 'Binance Futures' }]);
  const [broker, setBroker] = useState('Binance');
  const [connections, setConnections] = useState([]);
  const [connectionId, setConnectionId] = useState('');

  const headers = () => ({ Authorization: `Bearer ${localStorage.getItem('token')}` });

  useEffect(() => {
    fetch(`${API_URL}/broker-definitions`, { headers: headers() })
      .then((r) => (r.ok ? r.json() : []))
      .then((list) => {
        if (Array.isArray(list) && list.length) {
          setSources(list.map((x) => ({ code: x.code, name: x.name })));
        }
      })
      .catch(() => {});
    fetch(`${API_URL}/broker-connections`, { headers: headers() })
      .then((r) => (r.ok ? r.json() : []))
      .then((list) => setConnections(Array.isArray(list) ? list : []))
      .catch(() => {});
  }, []);

  return (
    <div className="page-shell">
      <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="flex items-center gap-3 text-2xl font-bold text-blue-400 sm:text-3xl">
            <TerminalSquare size={28} /> Live Terminal
          </h1>
          <p className="mt-1 text-sm text-gray-400">
            Real orders, positions, fills and margin on the BTC perpetual — straight from your broker account.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col">
            <label className="mb-1 text-xs font-bold uppercase text-gray-500">Broker</label>
            <select value={broker} onChange={(e) => { setBroker(e.target.value); setConnectionId(''); }}
                    className="rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm outline-none">
              {sources.map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
            </select>
          </div>
          <div className="flex flex-col">
            <label className="mb-1 text-xs font-bold uppercase text-gray-500">Connection</label>
            <select value={connectionId} onChange={(e) => setConnectionId(e.target.value)}
                    className="rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm outline-none">
              <option value="">Primary / legacy keys</option>
              {connections.filter((c) => c.broker_code === broker)
                .map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
          </div>
          <div className="rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 font-mono text-sm text-white">
            {perpetualFor(broker)}
            <span className="ml-1 text-[10px] text-gray-500">perpetual</span>
          </div>
        </div>
      </div>

      <LiveTerminal
        key={`${broker}:${connectionId}`}
        broker={broker}
        connectionId={connectionId ? Number(connectionId) : null}
        refreshMs={10000}
      />
    </div>
  );
};

export default Terminal;
