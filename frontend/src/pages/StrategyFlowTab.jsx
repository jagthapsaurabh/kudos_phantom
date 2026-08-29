import React from 'react';

/* ============================================================================
   Kudos Strategy — Platform & Data Flow
   The newest features of the platform, documented as one flow: how an
   exchange goes from a name in the admin registry to live orders on your
   account, and every page's role along the way.
   ========================================================================= */

const Card = ({ title, color, children, wide }) => (
  <div className={`bg-gray-800 p-6 rounded-2xl border border-gray-700 ${wide ? 'lg:col-span-2' : ''}`}>
    <h3 className={`text-sm font-bold uppercase tracking-wider mb-4 ${color}`}>{title}</h3>
    <div className="space-y-4 text-sm text-gray-300">{children}</div>
  </div>
);

const K = ({ children }) => (
  <span className="font-mono text-blue-300 text-xs font-bold">{children}</span>
);

const Note = ({ children, tone = 'yellow' }) => (
  <div className={`rounded-lg px-4 py-2.5 text-xs leading-relaxed border ${
    tone === 'yellow' ? 'bg-yellow-900/10 border-yellow-800/40 text-yellow-200/90'
    : 'bg-blue-900/10 border-blue-800/40 text-blue-200/90'}`}>
    {children}
  </div>
);

/* One numbered step of the pipeline. */
const Step = ({ n, title, where, children }) => (
  <div className="bg-gray-900/60 p-4 rounded-lg border border-gray-700/50">
    <div className="flex flex-wrap items-center gap-2">
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-blue-600 font-mono text-[11px] font-bold text-white">{n}</span>
      <span className="font-bold text-gray-100 text-sm">{title}</span>
      {where && <span className="rounded border border-gray-700 bg-gray-800 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{where}</span>}
    </div>
    <div className="mt-2 text-gray-400 text-xs leading-relaxed">{children}</div>
  </div>
);

/* Registry entry vs broker connection — the two things people mix up. */
const Compare = ({ title, tone, children }) => (
  <div className={`p-4 rounded-lg border ${tone}`}>
    <div className="text-xs font-bold uppercase tracking-wider mb-2">{title}</div>
    <div className="space-y-1.5">{children}</div>
  </div>
);

const Row = ({ left, right }) => (
  <div className="flex justify-between gap-3 border-b border-gray-700/50 py-1.5">
    <span className="text-gray-500 shrink-0">{left}</span>
    <span className="text-gray-200 font-bold text-right">{right}</span>
  </div>
);

const StrategyFlowTab = () => (
  <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

    {/* ---------------------------------------------------------- pipeline -- */}
    <Card wide title="🛰️ The full flow — from one admin entry to live orders" color="text-blue-400">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Step n={1} title="Register the venue" where="Broker & Data Sources → Exchange Registry">
          An admin adds the exchange once — a <b>code</b>, a display name, the <b>adapter kind</b>
          and its URLs. This is infrastructure configuration: it names a source and decides which
          adapter and endpoints the app would talk to. It stores <b>no credentials</b> and gives
          nobody the ability to trade. Binance Futures and Delta Exchange ship as built-ins.
        </Step>
        <Step n={2} title="Connect your own login" where="Broker & Data Sources → Add broker connection">
          Every user (or client account) adds <b>their own API key + secret</b> for a registered
          venue. Connections are per login — keys added while signed in as someone else are never
          shared. Secrets are stored server-side and always masked in the UI. Multiple connections
          (e.g. <i>Binance primary</i> + <i>Delta live</i>) can exist at the same time.
        </Step>
        <Step n={3} title="Pick a data source per instance" where="Paper Trade / Live Trade">
          Each paper or live instance is created against one registered source (dropdown on the
          instance form). The engine trades the BTC perpetual on that venue and maps the symbol
          automatically: <K>BTCUSDT</K> on Binance-compatible venues, <K>BTCUSD</K> on
          Delta-compatible ones.
        </Step>
        <Step n={4} title="Market data arrives on its own" where="Daily sync + live tick feed">
          Binance- and Delta-compatible integrations get an <b>automatic daily OHLC refresh</b>
          (1h/4h candles for the strategy, plus higher frames) and a live tick feed while an
          instance runs. Generic / custom sources stay <b>disabled for market-data sync</b> until a
          runtime adapter is added — they appear in the sync report as a clear skip, never as a
          broken request.
        </Step>
        <Step n={5} title="Kudos signals & orders" where="Engine">
          The same signal engine documented in <i>Strategy Rules</i> runs on those candles. On a
          signal the order manager sizes the position (margin × leverage → lots), attaches the
          ATR stop / take-profit / trailing bracket, and sends it through the venue's adapter —
          natively bracketed on Delta, entry + reduce-only legs on Binance. Stops trigger on the
          <b> mark price</b>, not the last trade.
        </Step>
        <Step n={6} title="Watch, manage, export" where="Live Terminal & instance pages">
          The terminal streams positions, open & stop orders, fills and order history with
          auto-refresh; every request is throttled against the venue's rate limits. Fills export
          as a Kudos/backtest-style CSV so live results can be compared with backtests on the
          same schema.
        </Step>
      </div>
      <Note tone="blue">
        <b>Who can do what:</b> only admins see the Exchange Registry; every login manages only its
        own connections; an instance trades only with keys that belong to the login that created it.
      </Note>
    </Card>

    {/* ------------------------------------------- registry vs connection -- */}
    <Card title="🔐 Integration vs connection — the difference" color="text-green-400">
      <p className="text-xs text-gray-500">
        <b className="text-gray-300">The integration itself — not your API keys.</b> These are two
        different objects that live on the same page:
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Compare title="Exchange Registry entry" tone="border-gray-700 bg-gray-900/60 text-gray-300">
          <div className="text-xs text-gray-400 space-y-1.5 leading-relaxed">
            <div>• <b>Admin-only.</b> One row per venue, shared by every client.</div>
            <div>• Holds the <b>name, adapter kind and URLs</b> — no keys.</div>
            <div>• Decides which market-data adapter and order adapter are used.</div>
            <div>• Carries per-venue rate limits &amp; trading defaults.</div>
            <div>• Can be enabled / disabled for everyone.</div>
          </div>
        </Compare>
        <Compare title="Broker connection" tone="border-green-800/60 bg-green-900/10 text-green-200">
          <div className="text-xs text-green-100/80 space-y-1.5 leading-relaxed">
            <div>• <b>Yours.</b> Belongs to one login, invisible to others.</div>
            <div>• Holds the <b>API key + secret</b> (masked after saving).</div>
            <div>• What Live Trade and the Terminal actually authenticate with.</div>
            <div>• Optional passphrase and testnet / sandbox flag.</div>
            <div>• Several can be live at once — one per venue.</div>
          </div>
        </Compare>
      </div>
      <Note>
        Adding an entry to the registry does <b>not</b> let anyone trade. Each login still needs
        its own key and secret under <b>Add broker connection</b> — and read/write trading
        permissions should only be enabled on keys used for live trading.
      </Note>
    </Card>

    {/* -------------------------------------------- Add Integration guide -- */}
    <Card title="📝 “Add Integration” — what to put in each field" color="text-teal-400">
      <p className="text-xs text-gray-500">
        Example: registering <b>Bybit Futures</b> as a Binance-compatible source.
      </p>
      <div className="space-y-2">
        <Row left="Code *" right="bybit — short internal key used in APIs & instance settings" />
        <Row left="Display name *" right="Bybit Futures — what dropdowns show every client" />
        <Row left="Adapter kind" right="Generic · Binance-compatible · Delta-compatible" />
        <Row left="Market data URL" right="https://api.bybit.com — candles / tick endpoints" />
        <Row left="Trading API URL" right="https://api.bybit.com — signed order endpoints" />
        <Row left="Notes" right="free text for the team (e.g. “futures only, no spot”)" />
      </div>
      <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50 text-xs text-gray-400 leading-relaxed">
        <K>Code</K> is normalized (case/spacing forgiving) and must be unique — the form rejects a
        duplicate. <K>Display name</K> is the human label. The two URLs are optional for
        Binance/Delta kinds (their built-in endpoints are used when blank) but recommended so the
        adapter talks to <i>your</i> region or proxy. After adding, the row appears with an
        ENABLED / DISABLED switch and a <b>Limits</b> editor.
      </div>
    </Card>

    {/* ---------------------------------------------------- adapter kinds -- */}
    <Card title="🔌 Adapter kinds — what each choice unlocks" color="text-purple-400">
      <div className="space-y-3">
        <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
          <div className="flex items-center gap-2"><K>binance</K><span className="text-[10px] font-bold uppercase text-yellow-300/80">Binance-compatible</span></div>
          <div className="text-gray-400 text-xs mt-1 leading-relaxed">
            Treats the venue like Binance USDⓈ-M futures: <K>BTCUSDT</K> symbol, HMAC request
            signing, weight-based budgets (2 400 weight / 5 min + 1 200 orders / min). Full order
            adapter incl. reduce-only stop legs — brackets are emulated (entry + two reduce-only
            stops). Automatic daily OHLC refresh: <b>yes</b>.
          </div>
        </div>
        <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
          <div className="flex items-center gap-2"><K>delta</K><span className="text-[10px] font-bold uppercase text-green-300/80">Delta-compatible</span></div>
          <div className="text-gray-400 text-xs mt-1 leading-relaxed">
            Treats the venue like Delta Exchange: <K>BTCUSD</K> symbol, contract sizes
            (0.001 BTC per contract), a 10 000-weight / 5-minute fixed window. Supports the
            <b> native bracket endpoint</b> — the unused protection leg is cancelled by the venue.
            Automatic daily OHLC refresh: <b>yes</b>.
          </div>
        </div>
        <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
          <div className="flex items-center gap-2"><K>generic</K><span className="text-[10px] font-bold uppercase text-gray-400">Generic / custom</span></div>
          <div className="text-gray-400 text-xs mt-1 leading-relaxed">
            Registers the name so it appears as a data source everywhere, but <b>no adapter is
            attached</b>: market-data sync is skipped (visible as a skip in the sync report) and
            live trading / terminal calls return a clear “no order adapter installed” error rather
            than guessing an endpoint. Choose this while evaluating a venue, then upgrade the row
            to a compatible kind when its API is Binance- or Delta-shaped.
          </div>
        </div>
      </div>
      <Note>
        Built-ins cannot be deleted — disable them instead. Every newly registered venue also
        gets default fee rows (taker / maker per backtest / paper / live mode) that admins can
        tune in the Admin Panel.
      </Note>
    </Card>

    {/* ---------------------------------------------- limits and defaults -- */}
    <Card title="⏱️ Per-venue limits & trading defaults" color="text-amber-400">
      <p className="text-xs text-gray-500">
        The <b>Limits</b> editor on each registry card. Blank = venue default; every request the
        app sends is throttled against these numbers, shared by the trader, the seeder and the
        terminal poller.
      </p>
      <div className="space-y-2">
        <Row left="Req / second" right="local sliding-window cap" />
        <Row left="Req / minute" right="local sliding-window cap" />
        <Row left="Quota / 5 min" right="Delta-style fixed weight window" />
        <Row left="Orders / minute" right="Binance-style order budget" />
        <Row left="Default leverage" right="pre-set on the terminal's leverage control" />
        <Row left="Margin mode" right="isolated / cross default" />
        <Row left="Contract value" right="BTC per contract for contract-sized venues" />
        <Row left="Tick size" right="price quantum for the venue" />
      </div>
      <div className="text-xs text-gray-500 leading-relaxed">
        When a limit is hit the client backs off and retries — the terminal's <i>Rate limits</i>
        panel shows usage against each budget live, including retried vs abandoned calls.
      </div>
    </Card>

    {/* ------------------------------------------------------ live terminal -- */}
    <Card title="🖥️ Live Terminal — what it gives you" color="text-red-400">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs text-gray-400">
        <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50 space-y-1.5">
          <div className="text-gray-200 font-bold">Order ticket</div>
          <div>Market, limit, stop-market, stop-limit, take-profit and trailing orders; size in BTC or the venue's own unit; reduce-only and post-only flags; attached SL/TP bracket.</div>
        </div>
        <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50 space-y-1.5">
          <div className="text-gray-200 font-bold">Account panels</div>
          <div>Wallet &amp; margin, risk (exposure, effective leverage, margin utilisation) and rate-limit usage — refreshed automatically.</div>
        </div>
        <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50 space-y-1.5">
          <div className="text-gray-200 font-bold">Alerts &amp; guards</div>
          <div>Unfilled-order alerts after 30s–15m (stop legs excluded), entry-gap protection, and “connect this broker” guidance when a venue has no usable key.</div>
        </div>
        <div className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50 space-y-1.5">
          <div className="text-gray-200 font-bold">Risk actions</div>
          <div>Cancel-all, close-position-at-market (reduce-only, leftover protection legs cancelled), leverage &amp; margin-mode controls.</div>
        </div>
      </div>
      <Note tone="blue">
        Fills export as a <b>Kudos CSV</b> (same columns as a backtest run) from the Live Trade
        page — drop it straight into the comparison workflow.
      </Note>
    </Card>

    {/* ------------------------------------------------------------ page map -- */}
    <Card wide title="🗺️ Where everything lives" color="text-gray-300">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 text-xs">
        {[
          ['Dashboard', 'Account overview: equity curve of runs, quick links.'],
          ['Backtest', 'Tune Kudos parameters (incl. per-side ATR floors), run over synced candles, export trades.'],
          ['Paper Trade', 'Multi-instance paper engine — each instance picks its registered data source.'],
          ['Live Trade', 'Same engine on a real connection: start/stop, heartbeat, fills feed, Kudos CSV export.'],
          ['Terminal', 'The live terminal for the selected broker & connection — orders, positions, fills.'],
          ['Chart', 'TradingView-style candles with Kudos signal overlay and backtest-run markers.'],
          ['Strategies', 'Save / lock tuned parameter sets and custom rule strategies for reuse.'],
          ['Kudos Strategy', 'This page — rules, formulas, and the platform flow.'],
          ['Broker & Data Sources', 'Trading defaults, your connections, and (admin) the Exchange Registry.'],
          ['Admin Panel', 'Clients, fees per venue & mode, market-data sync status, session control.'],
        ].map(([name, desc]) => (
          <div key={name} className="bg-gray-900/60 p-3 rounded-lg border border-gray-700/50">
            <div className="font-bold text-gray-200">{name}</div>
            <div className="text-gray-500 mt-0.5 leading-relaxed">{desc}</div>
          </div>
        ))}
      </div>
    </Card>
  </div>
);

export default StrategyFlowTab;
