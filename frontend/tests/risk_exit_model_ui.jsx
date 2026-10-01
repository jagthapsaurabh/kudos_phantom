import React from 'react';
import { renderToString } from 'react-dom/server';
import { readFileSync } from 'node:fs';

// Browser stubs (effects never run under the server renderer).
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};
globalThis.window = { location: { protocol: 'http:', hostname: 'localhost', search: '' }, addEventListener() {}, removeEventListener() {} };
globalThis.fetch = () => Promise.resolve({ ok: false, json: async () => ([]) });

import {
  DEFAULT_RISK_EXIT, RISK_EXIT_LEVELS, RISK_EXIT_MODELS,
  normalizeRiskExit, applyRiskExitModel, setRiskExitLevelMode,
  riskExitModeFor, riskExitModelFor, riskExitModelLabel, riskExitLevelText, riskExitText,
} from '../src/utils/riskExit.js';
import RiskExitModelEditor from '../src/components/RiskExitModelEditor.jsx';
import Backtest from '../src/pages/Backtest.jsx';

// esbuild bundles this file to CJS, so the source path is resolved from the
// runner's working directory — frontend/ under npm test.
const readSource = (rel) => {
  for (const base of ['', 'frontend/']) {
    try { return readFileSync(`${base}${rel}`, 'utf8'); } catch { /* next */ }
  }
  return '';
};

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; } else { fail++; console.log(`  FAIL: ${name} ${extra}`); }
};
// SSR inserts <!-- --> between text and expressions; strip it before matching.
const flat = (s) => String(s).replace(/<!-- -->/g, '');
const noop = () => {};

// The form's default params, as Backtest.jsx builds them.
const baseParams = {
  stop_loss_atr: 1.2, take_profit_atr: 14.0, trail_activation_atr: 0.8,
  trail_distance_atr: 0.3, breakeven_atr: 0.75,
  risk_exit: { ...DEFAULT_RISK_EXIT },
};
const priceParams = {
  ...baseParams,
  risk_exit: applyRiskExitModel(DEFAULT_RISK_EXIT, 'price'),
};
const mixedParams = {
  ...baseParams,
  risk_exit: setRiskExitLevelMode(DEFAULT_RISK_EXIT, 'stop', 'price'),
};

// ---------------------------------------------------------------- the model --
check('default model is all-ATR', riskExitModelFor({}) === 'atr'
  && ['stop', 'target', 'trail', 'breakeven'].every((l) => riskExitModeFor({}, l) === 'atr'));
check('defaults keep the price percentages ready but unused',
  normalizeRiskExit(undefined).stop_loss_pct === 0.016
  && normalizeRiskExit(undefined).take_profit_pct === 0.03
  && normalizeRiskExit(undefined).trail_activation_pct === 0.015
  && normalizeRiskExit(undefined).trail_distance_pct === 0.005
  && normalizeRiskExit(undefined).breakeven_pct === 0.01);

const price = applyRiskExitModel(DEFAULT_RISK_EXIT, 'price');
check("model='price' switches every level",
  price.model === 'price' && ['stop', 'target', 'trail', 'breakeven']
    .every((l) => price[`${l}_mode`] === 'price'));
check("model='atr' switches every level back",
  applyRiskExitModel(price, 'atr').model === 'atr'
  && ['stop', 'target', 'trail', 'breakeven'].every((l) => applyRiskExitModel(price, 'atr')[`${l}_mode`] === 'atr'));
check("model='both' keeps the per-level choices",
  applyRiskExitModel(price, 'both').stop_mode === 'price'
  && applyRiskExitModel(price, 'both').model === 'both');
check('unknown model values fall back to ATR',
  applyRiskExitModel(DEFAULT_RISK_EXIT, 'wat').model === 'atr');

const onePrice = setRiskExitLevelMode(DEFAULT_RISK_EXIT, 'stop', 'price');
check('switching one level marks the model as the per-level mix',
  onePrice.model === 'both' && onePrice.stop_mode === 'price'
  && onePrice.target_mode === 'atr');
const allPrice = ['stop', 'target', 'trail', 'breakeven']
  .reduce((rx, l) => setRiskExitLevelMode(rx, l, 'price'), DEFAULT_RISK_EXIT);
check('switching every level to price reports the price model', allPrice.model === 'price');
const backToAtr = ['stop', 'target', 'trail', 'breakeven']
  .reduce((rx, l) => setRiskExitLevelMode(rx, l, 'atr'), allPrice);
check('switching every level back reports the ATR model', backToAtr.model === 'atr');
check('per-level switches never lose the other value',
  setRiskExitLevelMode(DEFAULT_RISK_EXIT, 'stop', 'price').stop_loss_pct === 0.016);

// ------------------------------------------------------------------- labels --
check('model labels include the default',
  riskExitModelLabel('atr').includes('ATR') && riskExitModelLabel('price').includes('Price')
  && RISK_EXIT_MODELS.some((m) => m.value === 'both'));
check('level text: ATR model', riskExitLevelText(baseParams, 'stop') === '1.2×ATR');
check('level text: price model', riskExitLevelText(priceParams, 'stop') === '1.6% price');
check('model text: ATR levels on one line',
  riskExitText(baseParams) === 'Stop 1.2×ATR · TP 14×ATR · Trail 0.8×ATR → 0.3×ATR · BE 0.75×ATR',
  riskExitText(baseParams));
check('model text: price levels on one line',
  riskExitText(priceParams) === 'Stop 1.6% price · TP 3% price · Trail 1.5% → 0.5% · BE 1% price',
  riskExitText(priceParams));
check('model text: breakeven 0 reads as off',
  riskExitText({ ...baseParams, breakeven_atr: 0 }) === 'Stop 1.2×ATR · TP 14×ATR · Trail 0.8×ATR → 0.3×ATR · BE off',
  riskExitText({ ...baseParams, breakeven_atr: 0 }));

// ------------------------------------------------------------------- editor --
const atrHtml = flat(renderToString(<RiskExitModelEditor params={baseParams} setParams={noop} />));
check('editor renders the model as a toggle with all three choices',
  atrHtml.includes('risk-exit-model-toggle') && atrHtml.includes('risk-model-option-atr')
  && atrHtml.includes('risk-model-option-price') && atrHtml.includes('risk-model-option-both')
  && atrHtml.includes('ATR-based (default)') && atrHtml.includes('Price-based (%)')
  && atrHtml.includes('Both — per level'));
check('ATR is the pressed side of the toggle by default',
  /data-testid="risk-model-option-atr"[^>]*aria-pressed="true"/.test(atrHtml)
  && /data-testid="risk-model-option-price"[^>]*aria-pressed="false"/.test(atrHtml),
  'the default is the existing ATR-based risk & exit model');
check('editor shows the ATR inputs by default (unchanged behaviour)',
  atrHtml.includes('risk-atr-stop_loss_atr') && atrHtml.includes('risk-atr-take_profit_atr')
  && atrHtml.includes('risk-atr-trail_activation_atr') && atrHtml.includes('risk-atr-trail_distance_atr')
  && atrHtml.includes('risk-atr-breakeven_atr') && !atrHtml.includes('risk-pct-stop_loss_pct'));
check('editor keeps one ATR | Price % toggle per level',
  RISK_EXIT_LEVELS.every((l) => atrHtml.includes(l.pin)
    && atrHtml.includes(`${l.pin}-atr`) && atrHtml.includes(`${l.pin}-price`)));
check('every level starts on the ATR side of its toggle',
  RISK_EXIT_LEVELS.every((l) => new RegExp(`data-testid="${l.pin}-atr"[^>]*aria-pressed="true"`).test(atrHtml)
    && new RegExp(`data-testid="${l.pin}-price"[^>]*aria-pressed="false"`).test(atrHtml)));
check('editor shows the active model on one line',
  atrHtml.includes('risk-exit-summary') && atrHtml.includes('Stop 1.2×ATR · TP 14×ATR'));

const priceHtml = flat(renderToString(<RiskExitModelEditor params={priceParams} setParams={noop} />));
check('price mode presses the Price side of the toggle',
  /data-testid="risk-model-option-price"[^>]*aria-pressed="true"/.test(priceHtml)
  && /data-testid="risk-model-option-atr"[^>]*aria-pressed="false"/.test(priceHtml)
  && /data-testid="risk-mode-stop-price"[^>]*aria-pressed="true"/.test(priceHtml));
check('editor in price mode shows percent inputs',
  priceHtml.includes('risk-pct-stop_loss_pct') && priceHtml.includes('risk-pct-take_profit_pct')
  && priceHtml.includes('risk-pct-trail_activation_pct') && priceHtml.includes('risk-pct-trail_distance_pct')
  && priceHtml.includes('risk-pct-breakeven_pct') && !priceHtml.includes('risk-atr-stop_loss_atr'));
check('editor in price mode shows the values as percents',
  priceHtml.includes('value="1.6"') && priceHtml.includes('value="3"')
  && priceHtml.includes('Stop 1.6% price · TP 3% price'));

const mixedHtml = flat(renderToString(<RiskExitModelEditor params={mixedParams} setParams={noop} />));
check('editor in mixed mode keeps both editors visible',
  mixedHtml.includes('risk-pct-stop_loss_pct') && mixedHtml.includes('risk-atr-take_profit_atr'));
check('editor tolerates params with no risk_exit at all (older saved runs)',
  flat(renderToString(<RiskExitModelEditor params={{ stop_loss_atr: 1.2, take_profit_atr: 14, trail_activation_atr: 0.8, trail_distance_atr: 0.3, breakeven_atr: 0.75 }} setParams={noop} />))
    .includes('risk-exit-model'));

// ------------------------------------------------------- the pages / wiring --
const backtestSrc = readSource('src/pages/Backtest.jsx');
check('Backtest: defaults carry the all-ATR model',
  backtestSrc.includes("risk_exit: { ...DEFAULT_RISK_EXIT }"));
check('Backtest: the Risk & Exit Model group renders the editor',
  /groupName === 'Risk & Exit Model'[\s\S]{0,200}RiskExitModelEditor/.test(backtestSrc));
check('Backtest: restored runs merge the saved model',
  backtestSrc.includes('risk_exit: { ...base.risk_exit'));
check('Backtest: the parameter meta exposes the percentage fields',
  backtestSrc.includes('...RISK_EXIT_META'));
check('Backtest: the preview chip names the active model',
  backtestSrc.includes("preview.risk_exit.model !== 'atr'"));

const backtestHtml = flat(renderToString(<Backtest />));
check('Backtest page renders the Risk & Exit model editor',
  backtestHtml.includes('risk-exit-model') && backtestHtml.includes('risk-mode-stop')
  && backtestHtml.includes('Risk &amp; Exit Model'));

const rulesSrc = readSource('src/pages/PhantomStrategy.jsx');
check('Kudos Strategy docs: risk rules follow the model',
  rulesSrc.includes('riskExitModeFor') && rulesSrc.includes('riskExitText')
  && rulesSrc.includes('Risk &amp; exit model'));
const explainedSrc = readSource('src/pages/StrategyExplainedTab.jsx');
check('Strategy Explained: formulas switch with the model',
  explainedSrc.includes("riskPrice('stop')") && explainedSrc.includes('stop_loss_pct × Price')
  && explainedSrc.includes('take_profit_pct × Price'));
check('Strategy Explained: shows the active model text', explainedSrc.includes('riskExitText(cfg)'));
const summarySrc = readSource('src/components/StrategyConfigSummary.jsx');
check('Paper / Live strategy summary shows the risk & exit model',
  summarySrc.includes('riskExitModelLabel') && summarySrc.includes('riskExitText'));
const strategiesSrc = readSource('src/pages/Strategies.jsx');
check('Saved strategies carry the inert all-ATR defaults',
  (strategiesSrc.match(/risk_exit: \{ \.\.\.DEFAULT_RISK_EXIT \}/g) || []).length === 2);

check('Kudos Strategy config grid maps each ATR risk key to its % counterpart',
  rulesSrc.includes("stop_loss_atr: ['stop', 'stop_loss_pct']")
  && rulesSrc.includes("trail_distance_atr: ['trail', 'trail_distance_pct']")
  && rulesSrc.includes("breakeven_atr: ['breakeven', 'breakeven_pct']"));
check('Kudos Strategy config grid shows the price value when the level is on price',
  rulesSrc.includes('% price') && rulesSrc.includes('riskExitModeFor(cfg, RISK_KEY[0])'));

console.log(`\nPASSED: ${pass}  FAILED: ${fail}`);
process.exit(fail ? 1 : 0);
