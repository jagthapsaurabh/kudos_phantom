import React from 'react';

/* ============================================================================
   PHANTOM Strategy — Explained
   A plain-language, formula-by-formula breakdown of how the strategy works:
   what each setting means, how the value is calculated, where it is used in
   the pipeline and what happens when you change it.
   ========================================================================= */

const Card = ({ title, color, children }) => (
  <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
    <h3 className={`text-sm font-bold uppercase tracking-wider mb-4 ${color}`}>{title}</h3>
    <div className="space-y-4 text-sm text-gray-300">{children}</div>
  </div>
);

const K = ({ children }) => (
  <span className="font-mono text-blue-300 text-xs font-bold">{children}</span>
);

const Formula = ({ children }) => (
  <div className="bg-gray-950/80 border border-gray-700/60 rounded-lg px-4 py-2.5 font-mono text-[12px] text-emerald-300 leading-relaxed">
    {children}
  </div>
);

const Note = ({ children }) => (
  <div className="bg-yellow-900/10 border border-yellow-800/40 rounded-lg px-4 py-2.5 text-xs text-yellow-200/90 leading-relaxed">
    {children}
  </div>
);

const Item = ({ name, formula, children }) => (
  <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50 space-y-1.5">
    <div className="flex items-center gap-2 flex-wrap">
      <K>{name}</K>
    </div>
    <div className="text-gray-400 text-xs leading-relaxed">{children}</div>
    {formula && <Formula>{formula}</Formula>}
  </div>
);

/* A small live calculator: shows what margin / notional / lots look like for a
   given capital, margin %, leverage, price and lot size. */
const SizeCalculator = () => {
  const [capital, setCapital] = React.useState(20000);
  const [marginPct, setMarginPct] = React.useState(25);
  const [leverage, setLeverage] = React.useState(2);
  const [price, setPrice] = React.useState(100000);
  const [conv, setConv] = React.useState(85.0);
  const lotSize = 0.001;

  const margin = capital * (marginPct / 100);
  const notionalUsd = (margin * leverage) / conv;
  const lotsRaw = notionalUsd / price;
  const ql = Math.floor(lotsRaw / lotSize);
  const lots = ql * lotSize;
  const finalNotional = lots * price;
  const finalMargin = (finalNotional / leverage) * conv;

  const f = 'w-full bg-gray-900 p-2 rounded-lg border border-gray-700 text-white text-sm';

  return (
    <Card title="🧮 Live sizing calculator — try your own numbers" color="text-cyan-400">
      <p className="text-xs text-gray-500">
        These are the exact formulas used in <K>OrderManager.create_order</K>. Change the
        inputs to see how margin, notional and lots respond.
      </p>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div><label className="text-[10px] text-gray-500 uppercase block mb-1">Capital (₹)</label>
          <input type="number" value={capital} onChange={e => setCapital(+e.target.value)} className={f} /></div>
        <div><label className="text-[10px] text-gray-500 uppercase block mb-1">Margin %</label>
          <input type="number" value={marginPct} onChange={e => setMarginPct(+e.target.value)} className={f} /></div>
        <div><label className="text-[10px] text-gray-500 uppercase block mb-1">Leverage ×</label>
          <input type="number" value={leverage} onChange={e => setLeverage(+e.target.value)} className={f} /></div>
        <div><label className="text-[10px] text-gray-500 uppercase block mb-1">BTC price ($)</label>
          <input type="number" value={price} onChange={e => setPrice(+e.target.value)} className={f} /></div>
        <div><label className="text-[10px] text-gray-500 uppercase block mb-1">₹ / $ rate</label>
          <input type="number" value={conv} onChange={e => setConv(+e.target.value)} className={f} /></div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 pt-1">
        <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
          <div className="text-[10px] text-gray-500 uppercase">Margin per trade</div>
          <div className="font-mono text-lg text-yellow-300">₹{margin.toFixed(0)}</div>
        </div>
        <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
          <div className="text-[10px] text-gray-500 uppercase">Notional (raw, USD)</div>
          <div className="font-mono text-lg text-cyan-300">${notionalUsd.toFixed(2)}</div>
        </div>
        <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
          <div className="text-[10px] text-gray-500 uppercase">Lots (quantized)</div>
          <div className="font-mono text-lg text-emerald-300">{lots.toFixed(4)} BTC</div>
        </div>
        <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
          <div className="text-[10px] text-gray-500 uppercase">Final notional (USD)</div>
          <div className="font-mono text-lg text-cyan-300">${finalNotional.toFixed(2)}</div>
        </div>
        <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
          <div className="text-[10px] text-gray-500 uppercase">Final margin (₹)</div>
          <div className="font-mono text-lg text-yellow-300">₹{finalMargin.toFixed(0)}</div>
        </div>
        <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
          <div className="text-[10px] text-gray-500 uppercase">Signal valid?</div>
          <div className="font-mono text-lg">{ql >= 1 ? <span className="text-green-400">YES ✓</span> : <span className="text-red-400">NO — LOT_TOO_SMALL</span>}</div>
        </div>
      </div>
      <Note>
        With capital ₹20,000, margin 25% and leverage 2 (the shipped low-drawdown profile) the
        margin per trade is <b>₹5,000</b> → notional <b>≈ ₹10,000 / ₹85 ≈ $117.6</b>. At BTC
        ≈ $100,000 that's <b>0.00117 BTC</b>, which quantizes down to the minimum <b>0.001 BTC</b> lot.
        If the quantized lot is <b>0</b> the trade is rejected and counted as <K>LOT_TOO_SMALL</K>.
      </Note>
    </Card>
  );
};

const StrategyExplainedTab = ({ champion }) => {
  const cfg = champion?.config || {};
  const c = (k, d) => (cfg[k] !== undefined && cfg[k] !== null ? cfg[k] : d);
  const lev = Number(c('leverage', 2));
  const mp = Number(c('margin_pct', 0.15));
  const priceRef = 100000;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {/* ----------------------------------------------------------- Capital -- */}
      <Card title="💰 Capital & Margin Deployment" color="text-yellow-400">
        <Item name="Initial capital" formula="Equity (₹) = the account balance used to size every trade">
          The starting account equity. In the admin panel this defaults to <K>₹20,000</K> and is stored on
          the admin (and each client) as <K>initial_capital</K>. Every backtest, paper and live instance
          starts from this figure and compounds it trade after trade.
        </Item>
        <Item name="Margin deployment %" formula="Margin per trade = Equity × margin_pct   (e.g. 25% of ₹20,000 = ₹5,000)">
          The share of current equity used as <b>margin</b> (collateral) on a single position.
          It is <em>not</em> the position size itself — leverage multiplies it into notional.
          Configured as <K>margin_deployment_pct</K> (in % on the client, <K>margin_pct</K> as a fraction
          e.g. 0.25 in the engine). A higher value puts more equity at risk per trade.
        </Item>
      </Card>

      {/* ---------------------------------------------------- Trend & Regime -- */}
      <Card title="📊 Trend & Regime (when is the market tradeable?)" color="text-blue-400">
        <Item name="Trend EMA" formula="EMA50(4h)_t = α·Close_t + (1−α)·EMA_{t−1},  α = 2/(50+1)  →  Trend = UP if Close(1h) > EMA50(4h)">
          How far back the <b>4-hour trend</b> looks. The strategy maps the 4h EMA50 onto every 1h
          bar (as-of) and only takes <b>longs when price is above</b> it and <b>shorts when below</b>.
          Higher period = slower, fewer trades; lower = more responsive, more trades. Uses
          <K>trend_ema_period</K> (default 50).
        </Item>
        <Item name="Min ATR floor (regime)" formula="ATR(14) ≥ atr_regime_ratio × SMA(ATR,50)   (e.g. 0.5 × SMA)">
          Requires volatility to be alive before a trade. ATR is compared against its own 50-bar
          average; lower ratio = more trades (includes quiet markets); higher = only volatile,
          fast-moving regimes. Directional caps can also exclude the top-volatility quartile.
          Uses <K>atr_regime_ratio</K> (and optional <K>atr_regime_max</K>).
        </Item>
        <Item name="Cooldown bars" formula="Skip new entries for cooldown_bars candles after a trade closes">
          After a closed trade the engine refuses new entries until this many candles have passed —
          prevents re-entering the same wobbly area immediately. Uses <K>cooldown_bars</K>.
        </Item>
      </Card>

      {/* ------------------------------------------------------------ Entries -- */}
      <Card title="🎯 Entries (v3) — Setup A: RSI Reversal" color="text-green-400">
        <p className="text-xs text-gray-500">All five filters must pass on the same 1h candle; entry fills on the <b>next candle's open</b>.</p>
        <Item name="RSI oversold (long) / overbought (short)"
          formula="RSI(14) = 100 − 100/(1 + RS),  RS = EMA(gains,14) / EMA(losses,14)">
          A reversal long fires after RSI dropped below <K>{c('rsi_oversold', 40)}</K> and the candle closes green;
          a short after RSI rose above <K>{c('rsi_overbought', 60)}</K> and the candle closes red. Uses
          <K>rsi_oversold</K> / <K>rsi_overbought</K>.
        </Item>
        <Item name="Min ADX" formula="ADX(14) = EMA( 100·|+DI − −DI| / (+DI + −DI), 14 )">
          Skips choppy, directionless markets. Only entries where trend strength
          <K>ADX ≥ adx_min</K> (default {c('adx_min', 10)}). Higher = only strong trends, fewer trades.
        </Item>
        <Item name="MACD hist min (momentum size)" formula="hist = MACD_line − Signal_line,  MACD_line = EMA(12) − EMA(26)">
          Longs need <K>hist ≥ macd_hist_min</K>; shorts <K>hist ≤ −macd_hist_min</K> (signed when direction
          conditions are ON). Ensures enough momentum. Uses <K>macd_hist_min</K>.
        </Item>
        <Item name="MACD confirmation" formula="Long: hist_t > hist_{t−1}   ·   Short: hist_t < hist_{t−1}">
          The histogram must be <em>expanding</em> in the trade direction on the signal candle.
        </Item>
      </Card>

      <Card title="⚡ Entries (v3) — Setup B: Momentum Continuation" color="text-purple-400">
        <p className="text-xs text-gray-500">Adds trend-continuation trades so the strategy trades far more often than reversal-only. Currently <b>{c('enable_momentum_entry', false) ? 'ON' : 'OFF'}</b>.</p>
        <Item name="DI confirmation" formula="Long: +DI > −DI   ·   Short: −DI > +DI">
          Directional movement indices agree with the trend — the move is backed by real directional pressure.
        </Item>
        <Item name="MACD zero-cross" formula="Long: hist_{t−1} ≤ 0 and hist_t > 0   ·   Short: hist_{t−1} ≥ 0 and hist_t < 0">
          A fresh momentum burst: the histogram crosses the zero line in the trend direction on this candle.
        </Item>
        <Item name="RSI agreement" formula="Long: RSI ≥ momentum_rsi_min (default 50)   ·   Short: RSI ≤ 100 − momentum_rsi_min">
          RSI must sit on the correct side of neutral so we are not fighting momentum.
        </Item>
        <Item name="Momentum entries toggle" formula="enable_momentum_entry = true/false">
          Master switch. Turn it OFF to restore the classic reversal-only behaviour.
        </Item>
      </Card>

      {/* ------------------------------------------------------- Risk & Exit -- */}
      <Card title="🛡️ Risk & Exit Model" color="text-red-400">
        <Item name="Stop loss (ATR)" formula="SL_distance = max( stop_loss_atr × ATR(14),  sl_floor_pct × Price )  →  SL = Entry ∓ SL_distance">
          Distance of the hard stop from entry measured in ATRs (so it adapts to volatility). A floor of
          <K>{c('sl_floor_pct', 0.016) * 100}%</K> of price stops it getting too tight in dead-quiet markets.
          Higher = wider stop = survives noise but risks more. Uses <K>stop_loss_atr</K>.
        </Item>
        <Item name="Take profit (ATR)" formula="TP = Entry ± take_profit_atr × ATR(14)">
          Profit target in ATRs. TP fills are treated as <b>maker</b> (lower fee). Uses <K>take_profit_atr</K>.
        </Item>
        <Item name="Trail activation" formula="Activate trailing once PnL ≥ trail_activation_atr × ATR(14)">
          The trailing stop is switched on only after this much profit — early trades are not trailed.
          Uses <K>trail_activation_atr</K>.
        </Item>
        <Item name="Trail distance" formula="Long:  trail_stop = max(trail_stop, Peak − trail_distance_atr × ATR)   ·   Short: mirror">
          How tightly the trailing stop follows price. Smaller = tighter trail (locks more profit, exits sooner).
          Uses <K>trail_distance_atr</K>.
        </Item>
        <Item name="Breakeven after" formula="Once PnL ≥ breakeven_atr × ATR(14), ratchet the hard stop to the entry price">
          Winners can no longer become losers — the stop moves to entry. Uses <K>breakeven_atr</K> (0 = disabled).
        </Item>
        <Item name="Time stop" formula={'If bars_held ≥ timeout_bars → close at market ("MH")'}>
          Prevents capital being stuck in a trade forever. Uses <K>timeout_bars</K>.
        </Item>
      </Card>

      {/* --------------------------------------------------- Sizing & DD Guard -- */}
      <Card title="⚖️ Sizing & Drawdown Guard" color="text-cyan-400">
        <Item name="Leverage" formula="Notional (USD) = (Margin_₹ × leverage) / conversion_rate   ·   Notional (₹) = Margin × leverage">
          Position notional = margin × leverage. The shipped low-DD profile uses <K>{lev}×</K>. Higher
          leverage = bigger position for the same margin = bigger swings. Uses <K>leverage</K>.
        </Item>
        <Item name="Lot amount (position size)" formula="lots_raw = Notional_USD / Price   →   ql = floor(lots_raw / 0.001)   →   lots = ql × 0.001 BTC">
          Converts notional into a real, exchange-tradeable quantity of BTC, quantized to
          <K>{'lot_size_btc'} (0.001 BTC)</K> minimum lots. If <K>ql &lt; 1</K> the trade is rejected
          (<K>LOT_TOO_SMALL</K>). Final notional/margin are recomputed from the quantized lots.
        </Item>
        <Item name="Margin % of equity" formula="margin_pct_now = reduced_margin_pct (if DD ≥ dd_soft_pct) else margin_pct">
          Share of equity used as margin per trade (0.15 = 15%). At the soft-drawdown threshold this is cut to
          <K>reduced_margin_pct</K>. Uses <K>margin_pct</K> / <K>reduced_margin_pct</K>.
        </Item>
        <Item name="Soft drawdown %" formula="DD = (PeakEquity − Equity) / PeakEquity × 100   →  if DD ≥ dd_soft_pct, shrink position size">
          Past this equity drawdown, position size (margin) is reduced to protect the account.
          Uses <K>dd_soft_pct</K>.
        </Item>
        <Item name="Halt drawdown %" formula="if DD ≥ dd_halt_pct → new entries stop entirely">
          Past this level no new entries are opened. <K>100</K> = guard effectively off.
          Uses <K>dd_halt_pct</K>.
        </Item>
        <Item name="Resume drawdown %" formula="if halted and DD ≤ dd_resume_pct → entries resume">
          New entries start again only once drawdown falls back below this level.
          Uses <K>dd_resume_pct</K>.
        </Item>
      </Card>

      {/* ------------------------------------------------------- Formulas ref -- */}
      <div className="lg:col-span-2">
        <Card title="🧮 Indicator formulas used by the engine" color="text-violet-400">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
              <K>Exponential Moving Average (EMA, period N)</K>
              <Formula>α = 2 / (N + 1) &nbsp;&nbsp; EMA_t = α·P_t + (1 − α)·EMA[t−1]</Formula>
              <div className="text-gray-400 text-xs mt-1">Used for EMA50 trend, RSI smoothing, ATR, MACD and ADX.</div>
            </div>
            <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
              <K>Simple Moving Average (SMA, period N)</K>
              <Formula>SMA_t = (P[t−N+1] + … + P[t]) / N</Formula>
              <div className="text-gray-400 text-xs mt-1">Used for the ATR regime baseline SMA(ATR,50).</div>
            </div>
            <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
              <K>Average True Range (ATR, 14)</K>
              <Formula>TR = max(H−L, |H−Cₚ|, |L−Cₚ|) &nbsp;→&nbsp; ATR = EMA(TR,14)</Formula>
              <div className="text-gray-400 text-xs mt-1">Measures volatility. Sizes stops, targets and trailing.</div>
            </div>
            <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
              <K>RSI (14)</K>
              <Formula>RS = EMA(gains,14)/EMA(losses,14) &nbsp; RSI = 100 − 100/(1+RS)</Formula>
              <div className="text-gray-400 text-xs mt-1">Reversal trigger and momentum agreement.</div>
            </div>
            <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
              <K>MACD (12, 26, 9)</K>
              <Formula>MACD = EMA(12) − EMA(26) &nbsp; Signal = EMA(MACD,9) &nbsp; Hist = MACD − Signal</Formula>
              <div className="text-gray-400 text-xs mt-1">Momentum magnitude filter + zero-cross confirmation.</div>
            </div>
            <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
              <K>ADX &amp; DI (14)</K>
              <Formula>DX = 100·|+DI − −DI|/(+DI + −DI) &nbsp; ADX = EMA(DX,14)</Formula>
              <div className="text-gray-400 text-xs mt-1">Trend-strength gate (adx_min) + direction confirmation (+DI/−DI).</div>
            </div>
          </div>
          <Note>
            <b>Where every value is used:</b> indicators → <K>compute_indicators</K> → <K>StrategyService._compute</K>
            (signal generation) → <K>OrderManager.create_order/update_trade</K> (sizing, SL/TP/trail/breakeven) →
            <K>BacktestEngine.run</K> (drawdown guard, fee &amp; PnL accounting). The exact parameter values of the
            shipped champion are shown in the <em>Phantom Strategy</em> tab. At ₹{priceRef.toLocaleString()} per BTC
            and {mp * 100}% margin × {lev}× leverage, the example above is what a single position looks like.
          </Note>
        </Card>
      </div>

      <div className="lg:col-span-2">
        <SizeCalculator />
      </div>
    </div>
  );
};

export default StrategyExplainedTab;
