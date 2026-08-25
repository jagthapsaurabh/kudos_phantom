import React, { useEffect, useRef, useState, useCallback } from 'react';
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createSeriesMarkers,
  CrosshairMode,
} from 'lightweight-charts';
import {
  TrendingUp, Timer, Layers, Volume2, Radio, Filter,
  Maximize2, Minimize2, Activity, BarChart3, ChevronDown,
} from 'lucide-react';
import { API_URL } from '../api';
import { computeAll } from '../utils/indicators';

const INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d'];
const DEFAULT_INDICATORS = { ema20: false, ema50: true, sma50: false, rsi: false, macd: false };

const COLOR = {
  up: '#10b981',
  down: '#ef4444',
  ema20: '#f59e0b',
  ema50: '#3b82f6',
  sma50: '#a855f7',
  rsi: '#22c55e',
  macd: '#3b82f6',
  macdSignal: '#f59e0b',
};

const fmt = (n, d = 2) => Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });

const ChartPage = () => {
  const chartContainerRef = useRef();
  const chartRef = useRef();
  const candleSeriesRef = useRef();
  const volumeSeriesRef = useRef();
  const markersRef = useRef();

  // Indicator series refs (main pane overlays + sub panes)
  const overlayRefs = useRef({});      // { ema20, ema50, sma50 }
  const rsiSeriesRef = useRef();
  const macdLineRef = useRef();
  const macdSignalRef = useRef();
  const macdHistRef = useRef();

  const candlesRef = useRef([]);       // original kline array
  const timesRef = useRef([]);         // aligned times array
  const closesRef = useRef([]);        // aligned closes array
  const crosshairRef = useRef(null);   // subscription handler

  const [interval, setInterval] = useState('1h');
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [symbols, setSymbols] = useState(['BTCUSDT']);
  const [dataSource, setDataSource] = useState('Binance');
  const [sources, setSources] = useState([{ code: 'Binance', name: 'Binance Futures' }, { code: 'Delta', name: 'Delta Exchange' }]);
  const [showSignals, setShowSignals] = useState(true);
  const [signalRange, setSignalRange] = useState({ start: '2026-01-01', end: '2026-06-25' });
  const [signalCount, setSignalCount] = useState(0);
  const [overlayStrategy, setOverlayStrategy] = useState('PhantomV2');
  const [strategies, setStrategies] = useState([]);
  const [indicators, setIndicators] = useState({ ...DEFAULT_INDICATORS });
  const [legend, setLegend] = useState(null);   // hovered bar values
  const [loading, setLoading] = useState(false);
  const [lastPrice, setLastPrice] = useState('—');
  const [dataLen, setDataLen] = useState(0);
  const [noData, setNoData] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [showIndPanel, setShowIndPanel] = useState(false);

  const authHeaders = useCallback(() => ({ Authorization: `Bearer ${localStorage.getItem('token')}` }), []);

  // --- Symbols + strategies -----------------------------------------
  useEffect(() => {
    fetch(`${API_URL}/broker-definitions`, { headers: authHeaders() }).then(r => r.ok ? r.json() : []).then(list => {
      if (Array.isArray(list) && list.length) setSources(list.map(x => ({ code: x.code, name: x.name })));
    }).catch(() => {});
    fetch(`${API_URL}/symbols?source=${encodeURIComponent(dataSource)}`, { headers: authHeaders() })
      .then(r => (r.ok ? r.json() : ['BTCUSDT']))
      .then(list => {
        if (Array.isArray(list) && list.length) {
          setSymbols(list);
          if (!list.includes(symbol)) setSymbol(list[0]);
        }
      })
      .catch(() => {});
    fetch(`${API_URL}/strategies`, { headers: authHeaders() })
      .then(r => (r.ok ? r.json() : []))
      .then(list => {
        const arr = Array.isArray(list) ? list : [];
        setStrategies(arr);
        // Deep-link support: /chart?strategy=<id> preselects that strategy
        const q = new URLSearchParams(window.location.search).get('strategy');
        if (q && (q === 'PhantomV2' || q === 'FastTest' || arr.some(s => String(s.id) === q))) {
          setOverlayStrategy(q);
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetch(`${API_URL}/symbols?source=${encodeURIComponent(dataSource)}`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : ['BTCUSDT']).then(list => {
        if (Array.isArray(list) && list.length) { setSymbols(list); if (!list.includes(symbol)) setSymbol(list[0]); }
      }).catch(() => {});
  }, [dataSource]);

  // --- Chart init ----------------------------------------------------
  const initChart = useCallback(() => {
    if (!chartContainerRef.current) return;
    try {
      if (chartRef.current) chartRef.current.remove();
      overlayRefs.current = {};
      rsiSeriesRef.current = null;
      macdLineRef.current = null;
      macdSignalRef.current = null;
      macdHistRef.current = null;
      markersRef.current = null;

      const chart = createChart(chartContainerRef.current, {
        layout: {
          background: { color: '#0b1120' },
          textColor: '#94a3b8',
          fontSize: 12,
          fontFamily: "'Inter', system-ui, sans-serif",
        },
        grid: {
          vertLines: { color: '#1c2537' },
          horzLines: { color: '#1c2537' },
        },
        crosshair: {
          mode: CrosshairMode.Normal,
          vertLine: { color: '#334155', width: 1, style: 3, labelBackgroundColor: '#334155' },
          horzLine: { color: '#334155', width: 1, style: 3, labelBackgroundColor: '#334155' },
        },
        rightPriceScale: {
          borderColor: '#1c2537',
          scaleMargins: { top: 0.05, bottom: 0.15 },
        },
        timeScale: {
          borderColor: '#1c2537',
          timeVisible: true,
          secondsVisible: false,
          rightOffset: 8,
          barSpacing: 8,
        },
        width: chartContainerRef.current.clientWidth,
        height: fullscreen ? window.innerHeight - 20 : 560,
      });
      chartRef.current = chart;

      // Candlestick series (main pane, paneIndex 0)
      const candleSeries = chart.addSeries(CandlestickSeries, {
        upColor: COLOR.up,
        downColor: COLOR.down,
        wickUpColor: COLOR.up,
        wickDownColor: COLOR.down,
        borderVisible: false,
        priceLineVisible: true,
        lastValueVisible: true,
      });
      candleSeriesRef.current = candleSeries;

      // Volume histogram (pane 1)
      const volumeSeries = chart.addSeries(HistogramSeries, {
        priceFormat: { type: 'volume' },
        lastValueVisible: false,
        priceLineVisible: false,
      }, 1);
      volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
      volumeSeriesRef.current = volumeSeries;

      chart.timeScale().fitContent();

      // Legend readout on crosshair
      const onCrosshair = (param) => {
        if (!param || !param.time) { setLegend(null); return; }
        const s = param.seriesData.get(candleSeriesRef.current);
        if (!s) { setLegend(null); return; }
        const idx = timesRef.current.indexOf(param.time);
        let vals = null;
        if (idx >= 0) {
          vals = {
            time: param.time,
            open: s.open, high: s.high, low: s.low, close: s.close, volume: s.volume,
            ema20: overlayRefs.current.ema20?.data?.[idx] ?? null,
            ema50: overlayRefs.current.ema50?.data?.[idx] ?? null,
            sma50: overlayRefs.current.sma50?.data?.[idx] ?? null,
            rsi: rsiSeriesRef.current?.data?.[idx] ?? null,
            macdLine: macdLineRef.current?.data?.[idx] ?? null,
            macdSignal: macdSignalRef.current?.data?.[idx] ?? null,
            macdHist: macdHistRef.current?.data?.[idx] ?? null,
          };
        }
        setLegend(vals);
      };
      crosshairRef.current = onCrosshair;
      chart.subscribeCrosshairMove(onCrosshair);

      const handleResize = () => {
        if (chartRef.current && chartContainerRef.current) {
          chartRef.current.applyOptions({
            width: chartContainerRef.current.clientWidth,
            height: fullscreen ? window.innerHeight - 20 : 560,
          });
        }
      };
      window.addEventListener('resize', handleResize);

      return () => {
        window.removeEventListener('resize', handleResize);
        if (crosshairRef.current) chart.unsubscribeCrosshairMove(crosshairRef.current);
        crosshairRef.current = null;
      };
    } catch (error) {
      console.error('Critical error initializing chart:', error);
    }
  }, [fullscreen]);

  useEffect(() => {
    const cleanup = initChart();
    return () => {
      if (cleanup) cleanup();
      if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }
    };
  }, [initChart, interval, symbol]);

  // --- Data fetch ----------------------------------------------------
  const fetchData = useCallback(async () => {
    if (!candleSeriesRef.current) return;
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/klines?symbol=${symbol}&interval=${interval}&limit=500&source=${encodeURIComponent(dataSource)}`);
      const data = await res.json();
      candlesRef.current = data;
      timesRef.current = data.map(d => d.time);
      closesRef.current = data.map(d => d.close);

      const candles = data.map(d => ({ time: d.time, open: d.open, high: d.high, low: d.low, close: d.close }));
      candleSeriesRef.current.setData(candles);
      chartRef.current?.timeScale().fitContent();
      setDataLen(candles.length);
      setNoData(candles.length === 0);
      setLastPrice(Number(data[data.length - 1]?.close ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 }));

      if (volumeSeriesRef.current) {
        volumeSeriesRef.current.setData(
          data.map(d => ({
            time: d.time,
            value: d.volume ?? 0,
            color: d.close >= d.open ? 'rgba(16,185,129,0.45)' : 'rgba(239,68,68,0.45)',
          }))
        );
      }

      applyIndicators(indicators);
      applySignals(showSignals);
    } catch (e) {
      console.error('Error fetching chart data', e);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, interval, showSignals, overlayStrategy, dataSource]);

  useEffect(() => {
    if (chartRef.current && candleSeriesRef.current) fetchData();
  }, [fetchData]);

  // --- Indicators ----------------------------------------------------
  const applyIndicators = useCallback((ind) => {
    const chart = chartRef.current;
    if (!chart) return;
    const closes = closesRef.current;
    const times = timesRef.current;
    if (!closes.length) return;

    const comp = computeAll(closes, ind);

    const ensureOverlay = (key, seriesObj, color) => {
      if (ind[key]) {
        if (!overlayRefs.current[key]) {
          overlayRefs.current[key] = chart.addSeries(LineSeries, {
            color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          }, 0);
        }
        const vals = seriesObj.map((v) => ({ time, value: v }))
          .filter((d, i) => d.value !== null && d.value !== undefined);
        overlayRefs.current[key].setData(vals);
        overlayRefs.current[key].data = seriesObj;
      } else if (overlayRefs.current[key]) {
        chart.removeSeries(overlayRefs.current[key]);
        overlayRefs.current[key] = null;
      }
    };

    ensureOverlay('ema20', comp.ema20 || [], COLOR.ema20);
    ensureOverlay('ema50', comp.ema50 || [], COLOR.ema50);
    ensureOverlay('sma50', comp.sma50 || [], COLOR.sma50);

    // RSI sub-pane (pane 2)
    if (ind.rsi) {
      if (!rsiSeriesRef.current) {
        rsiSeriesRef.current = chart.addSeries(LineSeries, {
          color: COLOR.rsi, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false,
        }, 2);
        rsiSeriesRef.current.priceScale().applyOptions({ scaleMargins: { top: 0.1, bottom: 0.1 } });
      }
      rsiSeriesRef.current.setData((comp.rsi14 || []).map((v, i) => ({ time: times[i], value: v })).filter(d => d.value != null));
      rsiSeriesRef.current.data = comp.rsi14 || [];
    } else if (rsiSeriesRef.current) {
      chart.removeSeries(rsiSeriesRef.current);
      rsiSeriesRef.current = null;
    }

    // MACD sub-pane (pane 3)
    if (ind.macd && comp.macd) {
      if (!macdLineRef.current) {
        macdLineRef.current = chart.addSeries(LineSeries, { color: COLOR.macd, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 3);
        macdSignalRef.current = chart.addSeries(LineSeries, { color: COLOR.macdSignal, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 3);
        macdHistRef.current = chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false, priceFormat: { type: 'price' } }, 3);
        macdHistRef.current.priceScale().applyOptions({ scaleMargins: { top: 0.15, bottom: 0.15 } });
      }
      macdLineRef.current.setData(comp.macd.macdLine.map((v, i) => ({ time: times[i], value: v })).filter(d => d.value != null));
      macdSignalRef.current.setData(comp.macd.signalLine.map((v, i) => ({ time: times[i], value: v })).filter(d => d.value != null));
      macdHistRef.current.setData(comp.macd.histogram.map((v, i) => ({
        time: times[i], value: v, color: v >= 0 ? 'rgba(16,185,129,0.6)' : 'rgba(239,68,68,0.6)',
      })).filter(d => d.value != null));
      macdLineRef.current.data = comp.macd.macdLine;
      macdSignalRef.current.data = comp.macd.signalLine;
      macdHistRef.current.data = comp.macd.histogram;
    } else if (macdLineRef.current) {
      chart.removeSeries(macdLineRef.current);
      chart.removeSeries(macdSignalRef.current);
      chart.removeSeries(macdHistRef.current);
      macdLineRef.current = null;
      macdSignalRef.current = null;
      macdHistRef.current = null;
    }
  }, []);

  useEffect(() => { applyIndicators(indicators); }, [indicators, applyIndicators]);

  // --- Signals overlay ------------------------------------------------
  const applySignals = useCallback(async (show) => {
    if (!candleSeriesRef.current) return;
    if (!show || interval !== '1h') {
      if (markersRef.current) markersRef.current.setMarkers([]);
      setSignalCount(0);
      return;
    }
    try {
      const url = `${API_URL}/phantom/signals?symbol=${symbol}&start_date=${signalRange.start}&end_date=${signalRange.end}&strategy_id=${encodeURIComponent(overlayStrategy)}&source=${encodeURIComponent(dataSource)}`;
      const res = await fetch(url, { headers: authHeaders() });
      if (!res.ok) { if (markersRef.current) markersRef.current.setMarkers([]); setSignalCount(0); return; }
      const sigs = await res.json();
      const times = new Set(timesRef.current);
      const markers = sigs
        .filter(s => times.has(s.time))
        .map(s => ({
          time: s.time,
          position: s.direction === 1 ? 'belowBar' : 'aboveBar',
          color: s.direction === 1 ? '#22c55e' : '#ef4444',
          shape: s.direction === 1 ? 'arrowUp' : 'arrowDown',
          text: `${(s.setup || 'S').slice(0, 1)} ${s.rsi14 != null ? `RSI:${Number(s.rsi14).toFixed(0)}` : ''}`.trim(),
        }))
        .sort((a, b) => a.time - b.time);
      if (markersRef.current) markersRef.current.setMarkers([]);
      markersRef.current = createSeriesMarkers(candleSeriesRef.current, markers);
      setSignalCount(markers.length);
    } catch (e) {
      if (markersRef.current) markersRef.current.setMarkers([]);
      setSignalCount(0);
    }
  }, [symbol, interval, signalRange, overlayStrategy, dataSource, authHeaders]);

  useEffect(() => { applySignals(showSignals); }, [applySignals, showSignals]);

  // --- Fullscreen ----------------------------------------------------
  const toggleFullscreen = () => {
    const el = chartContainerRef.current?.parentElement;
    if (!el) return;
    if (!document.fullscreenElement) {
      el.requestFullscreen?.();
      setFullscreen(true);
    } else {
      document.exitFullscreen?.();
      setFullscreen(false);
    }
  };

  useEffect(() => {
    const onFs = () => setFullscreen(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', onFs);
    return () => document.removeEventListener('fullscreenchange', onFs);
  }, []);

  const o = legend || {};

  return (
    <div className={`${fullscreen ? 'fixed inset-0 z-50 bg-gray-950 p-2' : 'ml-64 p-8'} bg-gray-900 text-white min-h-screen font-sans`}>
      {/* Header */}
      <div className="flex flex-wrap justify-between items-center gap-4 mb-4">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-3 text-blue-400">
            <TrendingUp size={32} /> Market Chart
          </h1>
          <p className="text-gray-400 text-sm mt-1">Candlesticks, volume, indicators & strategy signal overlay</p>
        </div>
        <div className="flex gap-3 items-center flex-wrap">
          <div className="flex items-center gap-2 bg-gray-800 p-1 rounded-lg border border-gray-700">
            {INTERVALS.map(int => (
              <button key={int} onClick={() => setInterval(int)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition ${interval === int ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'}`}>
                {int}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 bg-gray-800 px-3 py-2 rounded-lg border border-gray-700">
            <select value={dataSource} onChange={e => setDataSource(e.target.value)} className="bg-transparent text-sm font-bold text-white outline-none cursor-pointer">
              {sources.map(s => <option key={s.code} value={s.code}>{s.name}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-2 bg-gray-800 px-3 py-2 rounded-lg border border-gray-700">
            <Layers size={16} className="text-gray-400" />
            <select value={symbol} onChange={e => setSymbol(e.target.value)}
              className="bg-transparent text-sm font-bold text-white outline-none cursor-pointer">
              {symbols.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <button onClick={() => setShowIndPanel(!showIndPanel)}
            className="flex items-center gap-2 bg-gray-800 px-3 py-2 rounded-lg border border-gray-700 text-xs font-semibold hover:border-blue-500 transition">
            <Activity size={14} /> Indicators {Object.values(indicators).some(Boolean) ? '·' : ''}
          </button>
          <button onClick={toggleFullscreen}
            className="flex items-center gap-2 bg-gray-800 px-3 py-2 rounded-lg border border-gray-700 text-xs font-semibold hover:border-blue-500 transition"
            title="Fullscreen">
            {fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />} {fullscreen ? 'Exit' : 'Full'}
          </button>
        </div>
      </div>

      {/* Indicator panel */}
      {showIndPanel && (
        <div className="mb-4 bg-gray-800 p-4 rounded-2xl border border-gray-700 animate-in fade-in slide-in-from-top-2">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
            <span className="text-xs font-bold text-gray-400 uppercase tracking-wider flex items-center gap-1"><BarChart3 size={14} /> Overlays</span>
            {[['ema20', 'EMA 20'], ['ema50', 'EMA 50'], ['sma50', 'SMA 50']].map(([k, label]) => (
              <label key={k} className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
                <input type="checkbox" checked={indicators[k]} onChange={e => setIndicators({ ...indicators, [k]: e.target.checked })} className="accent-blue-500" /> {label}
              </label>
            ))}
            <span className="text-xs font-bold text-gray-400 uppercase tracking-wider flex items-center gap-1 mx-2"><Activity size={14} /> Sub-panels</span>
            <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
              <input type="checkbox" checked={indicators.rsi} onChange={e => setIndicators({ ...indicators, rsi: e.target.checked })} className="accent-green-500" /> RSI 14
            </label>
            <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
              <input type="checkbox" checked={indicators.macd} onChange={e => setIndicators({ ...indicators, macd: e.target.checked })} className="accent-purple-500" /> MACD
            </label>
          </div>
        </div>
      )}

      {/* Signal + legend controls */}
      <div className="flex flex-wrap items-center gap-4 mb-2 bg-gray-800 px-4 py-3 rounded-2xl border border-gray-700">
        <div className="flex items-center gap-2">
          <Filter size={16} className="text-gray-400" />
          <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
            <input type="checkbox" checked={showSignals} onChange={e => setShowSignals(e.target.checked)} className="accent-green-500" /> Signals
          </label>
        </div>
        <div className="flex items-center gap-2">
          <Radio size={15} className="text-gray-400" />
          <select value={overlayStrategy} onChange={e => setOverlayStrategy(e.target.value)}
            className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-white outline-none">
            <option value="PhantomV2">Phantom V2.5 (Champion)</option>
            <option value="FastTest">FastTest (debug)</option>
            {strategies.map(s => <option key={s.id} value={s.id}>Custom: {s.name}</option>)}
          </select>
        </div>
        <div className="flex items-center gap-2 text-xs text-gray-400">
          <input type="date" value={signalRange.start} onChange={e => setSignalRange({ ...signalRange, start: e.target.value })}
            className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-white" />
          <span>→</span>
          <input type="date" value={signalRange.end} onChange={e => setSignalRange({ ...signalRange, end: e.target.value })}
            className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-white" />
        </div>
        <span className="text-xs font-bold text-green-400">{signalCount} markers</span>
        {loading && <span className="text-xs text-gray-500 animate-pulse">Loading…</span>}
      </div>

      {/* Chart + legend */}
      <div className="relative bg-gray-900 p-3 rounded-2xl border border-gray-700 shadow-2xl">
        <div className="absolute top-4 left-6 z-10 flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-800/90 border border-gray-700 text-xs">
          <Radio size={14} className="text-green-400 animate-pulse" />
          <span className="font-mono font-bold text-green-400">{dataSource} · {symbol}</span>
          <span className="text-gray-500">/</span>
          <span className="text-gray-400">{interval}</span>
          <span className="mx-1 text-gray-600">•</span>
          <span className="font-mono font-bold text-yellow-400">{lastPrice}</span>
        </div>
        <div ref={chartContainerRef} className="w-full" />
        {noData && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-gray-500">
            <TrendingUp size={40} className="mb-2 opacity-30" />
            <p className="text-sm">No market data available for {symbol}/{interval}.</p>
            <p className="text-[11px] mt-1">Please run the seeder or ensure Binance is reachable.</p>
          </div>
        )}

        {/* TradingView-style value legend */}
        <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 px-2 text-[11px] font-mono">
          <span className="text-gray-500">O <span className="text-white">{o.open != null ? fmt(o.open) : '—'}</span></span>
          <span className="text-gray-500">H <span className="text-green-400">{o.high != null ? fmt(o.high) : '—'}</span></span>
          <span className="text-gray-500">L <span className="text-red-400">{o.low != null ? fmt(o.low) : '—'}</span></span>
          <span className="text-gray-500">C <span className="text-white">{o.close != null ? fmt(o.close) : '—'}</span></span>
          <span className="text-gray-500">Vol <span className="text-gray-300">{o.volume != null ? Number(o.volume).toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—'}</span></span>
          {o.close != null && o.open != null && (
            <span className={o.close >= o.open ? 'text-green-400' : 'text-red-400'}>
              {((o.close - o.open) / o.open * 100).toFixed(2)}%
            </span>
          )}
          {o.ema20 != null && <span className="text-gray-500">EMA20 <span style={{ color: COLOR.ema20 }}>{fmt(o.ema20)}</span></span>}
          {o.ema50 != null && <span className="text-gray-500">EMA50 <span style={{ color: COLOR.ema50 }}>{fmt(o.ema50)}</span></span>}
          {o.sma50 != null && <span className="text-gray-500">SMA50 <span style={{ color: COLOR.sma50 }}>{fmt(o.sma50)}</span></span>}
          {o.rsi != null && <span className="text-gray-500">RSI14 <span style={{ color: COLOR.rsi }}>{fmt(o.rsi)}</span></span>}
          {o.macdHist != null && <span className="text-gray-500">MACD <span style={{ color: COLOR.macd }}>{fmt(o.macdLine)}</span></span>}
        </div>
        <div className="flex gap-6 mt-1 px-2 text-[11px] text-gray-400">
          <span className="flex items-center gap-1"><span className="text-green-500">▲</span> Long</span>
          <span className="flex items-center gap-1"><span className="text-red-500">▼</span> Short</span>
          <span className="flex items-center gap-1"><Volume2 size={12} className="text-gray-500" /> Volume</span>
          <span className="text-gray-600">Hover a candle to read values · overlay a custom strategy from the Signals menu</span>
        </div>
      </div>

      {/* Info cards */}
      <div className="mt-6 grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-blue-500/20 rounded-lg text-blue-400"><Timer size={20} /></div>
            <h3 className="font-bold">Timeframe</h3>
          </div>
          <p className="text-sm text-gray-400">1m → 1d candle resolution. Signals overlay on 1h.</p>
        </div>
        <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-green-500/20 rounded-lg text-green-400"><Radio size={20} /></div>
            <h3 className="font-bold">Indicators</h3>
          </div>
          <p className="text-sm text-gray-400">EMA/SMA overlays plus RSI & MACD sub-panels, computed live. {dataLen} candles loaded.</p>
        </div>
        <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-purple-500/20 rounded-lg text-purple-400"><Layers size={20} /></div>
            <h3 className="font-bold">Strategy Overlay</h3>
          </div>
          <p className="text-sm text-gray-400">Overlay Phantom or any custom strategy's signals on {symbol}.</p>
        </div>
      </div>
    </div>
  );
};

export default ChartPage;
