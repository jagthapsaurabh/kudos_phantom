import React, { useEffect, useState } from 'react';
import { Activity, Info } from 'lucide-react';
import { API_URL } from '../api';
import {
  isPhantomBuiltin, macdLineRuleText, setupModeLabel, tradeDirectionLabel,
} from '../utils/phantomPresets';

// "What will this strategy actually trade on?" — the MACD periods, the MACD
// line / signal rules and the setup / direction of the selected strategy,
// shown under the strategy dropdown on the Paper and Live pages.
//
// Built-in ids (PhantomV2 and its presets) are read from /phantom/config;
// saved strategies are read from the `strategies` list the page already has.
// Self-contained: fetches on its own, never blocks the page, renders nothing
// for Chartink-style rule strategies that have no Phantom parameters.
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
    <div className={`rounded-xl border border-gray-700 bg-gray-800/70 p-3 text-[11px] text-gray-300 ${className}`}
         data-testid="strategy-config-summary">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">
        <Activity size={12} className="text-blue-400" /> Strategy settings{title ? <span className="normal-case tracking-normal text-gray-300">· {title}</span> : null}
      </div>
      <div className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2 xl:grid-cols-4">
        <div>
          <div className="text-[9px] font-bold uppercase text-gray-500">Setup</div>
          <div className="text-white">{setupText}</div>
        </div>
        <div>
          <div className="text-[9px] font-bold uppercase text-gray-500">Direction</div>
          <div className="text-white">{tradeDirectionLabel(direction)}</div>
        </div>
        <div>
          <div className="text-[9px] font-bold uppercase text-gray-500">MACD periods (fast/slow/signal)</div>
          <div className="font-mono text-white">
            {perSidePeriods ? <>L {periods('long')} · S {periods('short')}</> : periods('long')}
          </div>
        </div>
        <div>
          <div className="text-[9px] font-bold uppercase text-gray-500">MACD hist threshold</div>
          <div className="font-mono text-white">Long ≥ {histLong} · Short ≤ {histShort}</div>
        </div>
        <div className="sm:col-span-2 xl:col-span-4">
          <div className="text-[9px] font-bold uppercase text-gray-500">MACD line / signal line rules</div>
          {lineActive ? (
            <div className="font-mono text-white">
              <span className="text-green-400">Long:</span> {lineLong}
              <span className="mx-2 text-gray-600">|</span>
              <span className="text-red-400">Short:</span> {lineShort}
            </div>
          ) : (
            <div className="flex items-center gap-1 text-gray-500">
              <Info size={11} /> Off — only the histogram threshold and the MACD confirmation / zero-cross checks apply (original behaviour).
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default StrategyConfigSummary;
