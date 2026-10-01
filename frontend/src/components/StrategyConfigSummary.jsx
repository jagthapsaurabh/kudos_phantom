import React, { useEffect, useState } from 'react';
import { Activity, Crosshair, Info, Shield } from 'lucide-react';
import { API_URL } from '../api';
import {
  isPhantomBuiltin, macdLineRuleText, setupModeLabel, tradeDirectionLabel,
} from '../utils/phantomPresets';
import { riskExitModelFor, riskExitModelLabel, riskExitText } from '../utils/riskExit';

// "What will this strategy actually trade on?" — the MACD periods, the MACD
// line / signal rules and the setup / direction of the selected strategy,
// shown under the strategy dropdown on the Paper and Live pages.
//
// Built-in ids (PhantomV2 and its presets) are read from /phantom/config;
// saved strategies are read from the `strategies` list the page already has.
// Self-contained: fetches on its own, never blocks the page, renders nothing
// for Chartink-style rule strategies that have no Phantom parameters.
// One label + value tile, so every screen reads the same way.
const SummaryTile = ({ label, children, mono = false }) => (
  <div className="rounded-xl border border-gray-700/60 bg-gray-900/40 px-3 py-2">
    <div className="text-[9px] font-bold uppercase tracking-wider text-gray-500">{label}</div>
    <div className={`mt-0.5 text-xs text-white ${mono ? 'font-mono' : ''}`}>{children}</div>
  </div>
);

const SectionTitle = ({ icon: Icon, tone = 'text-blue-400', children }) => (
  <div className="mb-2 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">
    <Icon size={12} className={tone} /> {children}
  </div>
);

const StrategyConfigSummary = ({ strategyId, strategies = [], className = '' }) => {
  const [builtin, setBuiltin] = useState(null);

  useEffect(() => {
    let alive = true;
    setBuiltin(null);
    if (!isPhantomBuiltin(strategyId)) return undefined;
    const url = `${API_URL}/phantom/config?strategy_id=${encodeURIComponent(strategyId)}`;
    fetch(url, { headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } })
      .then(r => (r.ok ? r.json() : null))
      .then(data => { if (alive && data) setBuiltin(data); })
      .catch(() => {});
    return () => { alive = false; };
  }, [strategyId]);

  let params = null;
  let title = null;
  if (isPhantomBuiltin(strategyId)) {
    if (!builtin) return null;
    params = builtin.config || null;
    title = builtin.strategy_name || null;
  } else {
    const found = (strategies || []).find(s => String(s.id) === String(strategyId));
    const rules = found && found.rules && typeof found.rules === 'object' && !Array.isArray(found.rules)
      ? found.rules : null;
    if (!rules || !('rsi_oversold' in rules || 'entry_conditions' in rules || 'macd_fast' in rules)) return null;
    params = rules;
    title = found.name || null;
  }
  if (!params) return null;

  const ec = params.entry_conditions || {};
  const perSidePeriods = !!ec.use_direction_conditions;
  const periods = (side) => {
    const b = perSidePeriods ? (ec[side] || {}) : {};
    return `${b.macd_fast ?? params.macd_fast ?? 12}/${b.macd_slow ?? params.macd_slow ?? 26}/${b.macd_signal ?? params.macd_signal ?? 9}`;
  };
  const histLong = (ec.use_direction_conditions || ec.use_direction_macd_hist) && ec.long?.macd_hist_min != null
    ? ec.long.macd_hist_min : Math.abs(Number(params.macd_hist_min ?? 0));
  const histShort = (ec.use_direction_conditions || ec.use_direction_macd_hist) && ec.short?.macd_hist_min != null
    ? ec.short.macd_hist_min : -Math.abs(Number(params.macd_hist_min ?? 0));
  const setupMode = params.setup_mode || 'both';
  const direction = params.trade_direction || 'both';
  const setupText = setupMode === 'both' && params.enable_momentum_entry === false
    ? 'Reversal only (momentum entries off)' : setupModeLabel(setupMode);
  const lineLong = macdLineRuleText(params, 1);
  const lineShort = macdLineRuleText(params, -1);
  const lineActive = lineLong !== 'off' || lineShort !== 'off';

  return (
    <div className={`overflow-hidden rounded-2xl border border-gray-700 bg-gray-800/70 text-[11px] text-gray-300 ${className}`}
         data-testid="strategy-config-summary">
      {/* Header: what this panel is, which strategy it describes, and where to change it. */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-gray-700/70 bg-gray-800/80 px-4 py-2.5">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-blue-500/10 text-blue-400">
          <Activity size={13} />
        </span>
        <span className="text-[10px] font-bold uppercase tracking-wider text-gray-400">Strategy settings</span>
        {title ? <span className="truncate text-xs font-semibold text-white" title={title}>· {title}</span> : null}
        <span className="ml-auto hidden text-[10px] text-gray-500 lg:inline">
          Change these in the Strategies manager, or in Backtest → Strategy Configuration
        </span>
      </div>

      <div className="space-y-4 p-4">
        <section data-testid="summary-entry">
          <SectionTitle icon={Crosshair} tone="text-emerald-400">Entry conditions</SectionTitle>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
            <SummaryTile label="Setup">{setupText}</SummaryTile>
            <SummaryTile label="Direction">{tradeDirectionLabel(direction)}</SummaryTile>
            <SummaryTile label="MACD periods (fast/slow/signal)" mono>
              {perSidePeriods ? <>L {periods('long')} · S {periods('short')}</> : periods('long')}
            </SummaryTile>
            <SummaryTile label="MACD hist threshold" mono>Long ≥ {histLong} · Short ≤ {histShort}</SummaryTile>
          </div>
        </section>

        <section data-testid="summary-risk-exit">
          <SectionTitle icon={Shield}>Risk &amp; exit model</SectionTitle>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border border-gray-700/60 bg-gray-900/40 px-3 py-2">
            <span className="rounded-full border border-blue-500/30 bg-blue-500/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-blue-300">
              {riskExitModelLabel(riskExitModelFor(params))}
            </span>
            <span className="font-mono text-xs text-white">{riskExitText(params)}</span>
          </div>
        </section>

        <section data-testid="summary-line-rules">
          <SectionTitle icon={Info}>MACD line / signal line rules</SectionTitle>
          {lineActive ? (
            <div className="flex flex-col gap-2 rounded-xl border border-gray-700/60 bg-gray-900/40 px-3 py-2 font-mono text-xs text-white sm:flex-row sm:items-center sm:gap-6">
              <span className="flex items-center gap-2">
                <span className="rounded border border-green-500/30 bg-green-500/10 px-1.5 py-0.5 text-[10px] font-bold text-green-300">Long</span>
                {lineLong}
              </span>
              <span className="flex items-center gap-2">
                <span className="rounded border border-red-500/30 bg-red-500/10 px-1.5 py-0.5 text-[10px] font-bold text-red-300">Short</span>
                {lineShort}
              </span>
            </div>
          ) : (
            <div className="flex items-start gap-1.5 rounded-xl border border-gray-700/60 bg-gray-900/40 px-3 py-2 text-gray-500">
              <Info size={11} className="mt-0.5 shrink-0" /> Off — only the histogram threshold and the MACD confirmation / zero-cross checks apply (original behaviour).
            </div>
          )}
        </section>
      </div>
    </div>
  );
};

export default StrategyConfigSummary;
