import React from 'react';
import { HelpCircle } from 'lucide-react';
import {
  RISK_EXIT_MODELS, RISK_EXIT_LEVELS, RISK_EXIT_META, RISK_EXIT_ATR, RISK_EXIT_PRICE,
  normalizeRiskExit, riskExitModeFor, riskExitText, applyRiskExitModel, setRiskExitLevelMode,
} from '../utils/riskExit';

// v3.6 — "Risk & Exit Model" editor.
//
// One row per protective level. Each row has its own ATR / Price % selector, so
// the client can run every level in ATR units (the default — unchanged), every
// level in % of price, or any mix ("Both — per level"). Both values are kept
// while a level is switched, so toggling back and forth never loses a number.
//
// Pure: it only calls `setParams` with the next params object. The maths that
// decides what ATR vs price means lives on the backend (RiskExitModel in
// app/core/strategy.py) and in src/utils/riskExit.js for the text.

const inputCls = 'w-full rounded border border-gray-700 bg-gray-800 p-1.5 text-xs text-white outline-none focus:border-blue-500';
const selectCls = 'rounded border border-gray-700 bg-gray-800 p-1 text-[10px] font-bold text-white outline-none focus:border-blue-500';

const Field = ({ label, hint, value, onChange, testid, step = '0.01' }) => (
  <div className="flex flex-col">
    <label className="mb-1 flex items-center gap-1 text-[10px] font-semibold text-gray-400">
      {label}
      {hint ? <span title={hint} className="cursor-help text-gray-600 hover:text-blue-400"><HelpCircle size={11} /></span> : null}
    </label>
    <input type="number" step={step} value={value ?? ''} data-testid={testid}
      onChange={onChange} className={inputCls} />
  </div>
);

// Fractions are stored (0.016 = 1.6%), percent numbers are shown.
const asPercent = (v) => (
  v === undefined || v === null || v === '' ? '' : Math.round(Number(v) * 1e6) / 1e4
);
const toFraction = (raw) => (raw === '' ? 0 : Number(raw) / 100);

const RiskExitModelEditor = ({ params = {}, setParams, className = '' }) => {
  const rx = normalizeRiskExit(params.risk_exit);
  const model = RISK_EXIT_MODELS.find((m) => m.value === rx.model) || RISK_EXIT_MODELS[0];

  const setModel = (value) => setParams((prev) => ({
    ...prev, risk_exit: applyRiskExitModel(prev.risk_exit, value),
  }));
  const setLevelMode = (level, mode) => setParams((prev) => ({
    ...prev, risk_exit: setRiskExitLevelMode(prev.risk_exit, level, mode),
  }));
  const setPct = (field, raw) => setParams((prev) => ({
    ...prev,
    risk_exit: { ...normalizeRiskExit(prev.risk_exit), [field]: toFraction(raw) },
  }));
  const setAtr = (field, raw) => setParams((prev) => ({
    ...prev, [field]: raw === '' ? 0 : Number(raw),
  }));

  return (
    <div data-testid="risk-exit-model" className={`space-y-2 ${className}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[10px] font-semibold text-gray-400">Risk &amp; Exit model</span>
        <select data-testid="risk-exit-model-select" value={rx.model} title={model.hint}
          aria-label="Risk and exit model" onChange={(e) => setModel(e.target.value)} className={selectCls}>
          {RISK_EXIT_MODELS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>
      <p className="text-[10px] leading-snug text-gray-500" data-testid="risk-exit-model-hint">{model.hint}</p>

      {RISK_EXIT_LEVELS.map((level) => {
        const mode = riskExitModeFor(params, level.key);
        const priceMode = mode === RISK_EXIT_PRICE;
        return (
          <div key={level.key} data-testid={`risk-level-${level.key}`}
            className="space-y-2 rounded-lg border border-gray-700 bg-gray-900/80 p-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] font-bold text-gray-300">{level.label}</span>
              <select data-testid={level.pin} value={mode} aria-label={`${level.label} model`}
                onChange={(e) => setLevelMode(level.key, e.target.value)}
                className={`${selectCls} ${priceMode ? 'text-amber-300' : 'text-blue-300'}`}>
                <option value={RISK_EXIT_ATR}>ATR</option>
                <option value={RISK_EXIT_PRICE}>Price %</option>
              </select>
            </div>
            <div className={level.distance ? 'grid grid-cols-2 gap-2' : ''}>
              {priceMode ? (
                <>
                  <Field label={RISK_EXIT_META[level.pctField].label} hint={RISK_EXIT_META[level.pctField].hint}
                    testid={`risk-pct-${level.pctField}`} step="0.01"
                    value={asPercent(rx[level.pctField])} onChange={(e) => setPct(level.pctField, e.target.value)} />
                  {level.distance ? (
                    <Field label={RISK_EXIT_META[level.distance.pctField].label} hint={RISK_EXIT_META[level.distance.pctField].hint}
                      testid={`risk-pct-${level.distance.pctField}`} step="0.01"
                      value={asPercent(rx[level.distance.pctField])}
                      onChange={(e) => setPct(level.distance.pctField, e.target.value)} />
                  ) : null}
                </>
              ) : (
                <>
                  <Field label={level.atrLabel} hint="ATR units of the 14-period ATR at entry."
                    testid={`risk-atr-${level.atrField}`} step="0.01"
                    value={params[level.atrField] ?? 0} onChange={(e) => setAtr(level.atrField, e.target.value)} />
                  {level.distance ? (
                    <Field label={level.distance.atrLabel} hint="ATR units — how tightly the trail follows the peak."
                      testid={`risk-atr-${level.distance.atrField}`} step="0.01"
                      value={params[level.distance.atrField] ?? 0}
                      onChange={(e) => setAtr(level.distance.atrField, e.target.value)} />
                  ) : null}
                </>
              )}
            </div>
          </div>
        );
      })}

      <p className="text-[10px] leading-snug text-gray-500">
        Active model — <span className="font-mono text-gray-300" data-testid="risk-exit-summary">{riskExitText(params)}</span>.
        ATR levels are read from the ATR(14) at entry; price levels are read as a % of the entry price.
      </p>
    </div>
  );
};

export default RiskExitModelEditor;
