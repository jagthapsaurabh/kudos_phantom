import React from 'react';
import { CheckCircle2, XCircle, KeyRound, Plug } from 'lucide-react';

/**
 * Renders `GET /broker-connections/diagnose` for one broker and one login.
 *
 * "API keys not configured for Binance" used to cover five different states —
 * no connection on this login, a connection saved under the display name
 * instead of the registry code, a hand-inserted row with `is_active` NULL, a
 * connection switched off, or keys saved on another account — and the browser
 * could not tell them apart. This panel shows what the server actually found,
 * so the split between the **Exchange Registry** (admin: registers the
 * integration, holds no credentials) and a **broker connection** (this login's
 * API key/secret) is visible instead of guessed at.
 */
const ConnectionCheck = ({ report, loading = false }) => {
  if (loading) {
    return <p className="text-xs text-gray-500">Checking connection…</p>;
  }
  if (!report) {
    return <p className="text-xs text-gray-500">No connection check yet.</p>;
  }

  const { broker, account, definition, connections = [], legacy_account_keys: legacy = false,
          ready = false, problems = [] } = report;

  return (
    <div className="rounded-xl border border-gray-700 bg-gray-900/60 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className={`flex items-center gap-1.5 rounded border px-2 py-0.5 text-[10px] font-bold uppercase ${
          ready ? 'border-green-800 bg-green-900/20 text-green-300'
                : 'border-red-800 bg-red-900/20 text-red-300'}`}>
          {ready ? <CheckCircle2 size={11} /> : <XCircle size={11} />}
          {ready ? 'Ready to trade' : 'Not ready'}
        </span>
        <span className="font-mono text-[11px] text-gray-300">{broker}</span>
        <span className="text-[11px] text-gray-500">· account {account}</span>
      </div>

      {/* Exchange Registry = the integration itself. No credentials live here. */}
      <div className="mb-2 flex items-start gap-1.5 text-[11px] text-gray-400">
        <Plug size={12} className="mt-0.5 shrink-0 text-blue-400" />
        <span>
          Exchange Registry:{' '}
          {definition
            ? <>{definition.name} <span className="text-gray-600">({definition.kind}{definition.is_builtin ? ', built-in' : ''})</span>
                {definition.enabled ? '' : <span className="text-red-400"> — disabled</span>}</>
            : <span className="text-red-400">not registered</span>}
        </span>
      </div>

      {/* Broker connections = this login's API keys. */}
      <div className="mb-2 space-y-1">
        {connections.map((c) => (
          <div key={c.id ?? `${c.stored_code}-${c.label}`}
               className="flex flex-wrap items-center gap-1.5 text-[11px] text-gray-400">
            <KeyRound size={12} className="shrink-0 text-yellow-400" />
            <span className="text-gray-200">{c.label}</span>
            <span className={c.is_active ? 'text-green-400' : 'text-red-400'}>
              {c.is_active ? 'on' : 'off'}
            </span>
            <span>{c.api_key || 'no key'}</span>
            <span className={c.has_secret ? 'text-green-400' : 'text-red-400'}>
              {c.has_secret ? 'secret saved' : 'NO SECRET'}
            </span>
            {c.is_testnet && <span className="text-amber-400">testnet</span>}
            {c.stored_code !== c.resolved_code && (
              <span className="text-gray-600" title="Saved under another spelling; it still resolves to the registry code">
                ({c.stored_code} → {c.resolved_code})
              </span>
            )}
          </div>
        ))}
        {connections.length === 0 && (
          <div className="flex items-center gap-1.5 text-[11px] text-gray-500">
            <KeyRound size={12} className="shrink-0" />
            No broker connection saved on this login{legacy ? ' (legacy account keys are being used)' : ''}.
          </div>
        )}
      </div>

      {problems.length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-gray-800 pt-2">
          {problems.map((p, i) => (
            <li key={i} className="text-[11px] text-red-300">{p}</li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default ConnectionCheck;
