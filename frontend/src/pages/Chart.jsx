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
import DateInput from '../components/DateInput';
import { computeAll } from '../utils/indicators';
import { buildOverlayMarkers, defaultSignalRange, fmtUnixUtc, signalLabel } from '../utils/chartOverlay';

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
  const fullscreenRef = useRef(false);
  // time → { label, side, setup, trend, candle, rsi, price, kind } for the
  // hover tooltip over a marked candle (markers render icon-only).
  const markersByTimeRef = useRef(new Map());
  const lastHoverTimeRef = useRef(null);

  const [interval, setInterval] = useState('1h');
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [symbols, setSymbols] = useState(['BTCUSDT']);
  const [dataSource, setDataSource] = useState('Binance');
  const [sources, setSources] = useState([{ code: 'Binance', name: 'Binance Futures' }, { code: 'Delta', name: 'Delta Exchange' }]);
  const [showSignals, setShowSignals] = useState(true);
  const [signalRange, setSignalRange] = useState(() => defaultSignalRange());
  const [signalCount, setSignalCount] = useState(0);
  const [overlayEvents, setOverlayEvents] = useState([]);
  const [runOverlay, setRunOverlay] = useState(null);
  const [overlayStrategy, setOverlayStrategy] = useState('PhantomV2');
  const [strategies, setStrategies] = useState([]);
  const [indicators, setIndicators] = useState({ ...DEFAULT_INDICATORS });
  const [legend, setLegend] = useState(null);   // hovered bar values
  const [hoverMarker, setHoverMarker] = useState(null);   // { info, x, y } hovered marker
  const [loading, setLoading] = useState(false);
  const [lastPrice, setLastPrice] = useState('—');
  const [dataLen, setDataLen] = useState(0);
  const [noData, setNoData] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [showIndPanel, setShowIndPanel] = useState(false);

  const authHeaders = useCallback(() => ({ Authorization: `Bearer ${localStorage.getItem('token')}` }), []);
  const getChartHeight = useCallback(() => (fullscreenRef.current ? window.innerHeight - 20 : 560), []);

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

  // Deep-link a finished backtest onto the market candles: /chart?run=<id>
  useEffect(() => {
    const runId = new URLSearchParams(window.location.search).get('run');
    if (!runId) return;
    fetch(`${API_URL}/backtest/results/${runId}`, { headers: authHeaders() })
      .then(r => (r.ok ? r.json() : null))
      .then(data => {
        if (!data?.run_details) return;
        const d = data.run_details;
        const start = d.start_date ? String(d.start_date).slice(0, 10) : null;
        const end = d.end_date ? String(d.end_date).slice(0, 10) : null;
        if (start && end) setSignalRange({ start, end });
        if (d.data_source) setDataSource(d.data_source);
        if (d.strategy_id) setOverlayStrategy(String(d.strategy_id));
        setInterval('1h');
        setShowSignals(true);
        setRunOverlay({
          id: d.id,
          name: d.name || `Run #${d.id}`,
          trades: Array.isArray(data.trades) ? data.trades : [],
          strategy_id: d.strategy_id,
        });
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
        height: getChartHeight(),
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
        if (!param || !param.time) { setLegend(null); setHoverMarker(null); lastHoverTimeRef.current = null; return; }
        const s = param.seriesData.get(candleSeriesRef.current);
        if (!s) { setLegend(null); setHoverMarker(null); lastHoverTimeRef.current = null; return; }
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
        // Marker detail: markers are icon-only, so surface the full strategy
        // data (side / setup / trend / candle / RSI) in a tooltip next to the
        // cursor, TradingView-style, only when the hovered bar has a marker.
        const markerInfo = markersByTimeRef.current.get(param.time);
        const markerTime = markerInfo ? param.time : null;
        if (lastHoverTimeRef.current !== markerTime) {
          lastHoverTimeRef.current = markerTime;
          if (markerInfo) {
            const pt = param.point || {};
            setHoverMarker({ info: markerInfo, x: pt.x, y: pt.y });
          } else {
            setHoverMarker(null);
          }
        }
      };
      crosshairRef.current = onCrosshair;
      chart.subscribeCrosshairMove(onCrosshair);

      const handleResize = () => {
        if (chartRef.current && chartContainerRef.current) {
          chartRef.current.applyOptions({
            width: chartContainerRef.current.clientWidth,
            height: getChartHeight(),
          });
          // Refit the visible range so a reflow never leaves the price axis or
          // the last candle stretched across a stale width.
          chartRef.current?.timeScale()?.fitContent();
        }
      };
      window.addEventListener('resize', handleResize);
      // Watch the container directly too: window resize misses layout changes
      // (sidebar reflow, header wrap, fullscreen) that move the chart's width.
      const resizeObserver = new ResizeObserver(handleResize);
      if (chartContainerRef.current) resizeObserver.observe(chartContainerRef.current);

      return () => {
        window.removeEventListener('resize', handleResize);
        resizeObserver.disconnect();
        if (crosshairRef.current) chart.unsubscribeCrosshairMove(crosshairRef.current);
        crosshairRef.current = null;
      };
    } catch (error) {
      console.error('Critical error initializing chart:', error);
    }
  }, [getChartHeight]);

  useEffect(() => {
    const cleanup = initChart();
    return () => {
      if (cleanup) cleanup();
      if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }
    };
  }, [initChart]);

  // --- Data fetch ----------------------------------------------------
  const fetchData = useCallback(async () => {
    if (!candleSeriesRef.current) return;
    setLoading(true);
    try {
      const params = new URLSearchParams({
        symbol, interval, limit: '50000', source: dataSource,
      });
      if (signalRange.start) params.set('start_date', signalRange.start);
      if (signalRange.end) params.set('end_date', signalRange.end);
      const res = await fetch(`${API_URL}/klines?${params.toString()}`);
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
  }, [symbol, interval, showSignals, overlayStrategy, dataSource, signalRange.start, signalRange.end]);

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
        const vals = seriesObj
          .map((v, i) => ({ time: times[i], value: v }))
          .filter(d => d.time !== undefined && d.value !== null && d.value !== undefined);
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
    const trades = runOverlay?.trades || [];
    const canPlotSignals = show && interval === '1h';
    if (!canPlotSignals && !trades.length) {
      if (markersRef.current) markersRef.current.setMarkers([]);
      markersByTimeRef.current = new Map();
      setHoverMarker(null);
      setSignalCount(0);
      setOverlayEvents([]);
      return;
    }
    try {
      let sigs = [];
      if (canPlotSignals) {
        const url = `${API_URL}/phantom/signals?symbol=${symbol}&start_date=${signalRange.start}&end_date=${signalRange.end}&strategy_id=${encodeURIComponent(overlayStrategy)}&source=${encodeURIComponent(dataSource)}`;
        const res = await fetch(url, { headers: authHeaders() });
        if (res.ok) sigs = await res.json();
      }
      const times = new Set(timesRef.current);
      const markers = buildOverlayMarkers({
        signals: Array.isArray(sigs) ? sigs : [],
        trades,
      }).filter(m => times.has(m.time));
      // Feed the hover tooltip: keep the readable detail (label, side, setup,
      // trend, candle type, RSI, price, in/out) per marked time so a hover on
      // the icon can display it without cluttering the candle.
      const byTime = new Map();
      for (const m of markers) {
        byTime.set(m.time, {
          label: m.tooltip || '',
          kind: m.kind,
          ...(m.data || {}),
        });
      }
      markersByTimeRef.current = byTime;
      if (markersRef.current) markersRef.current.setMarkers([]);
      markersRef.current = createSeriesMarkers(candleSeriesRef.current, markers);
      setSignalCount(markers.length);
      const events = (Array.isArray(sigs) ? sigs : [])
        .filter(s => times.has(s.time))
        .slice(-80)
        .reverse()
        .map(s => ({
          time: s.time,
          label: signalLabel(s),
          setup: s.setup,
          trend: s.trend_label || (s.trend === 1 ? 'UP' : s.trend === -1 ? 'DOWN' : ''),
          candle: s.candle_type,
          rsi: s.rsi14,
          side: s.side || (s.direction === 1 ? 'LONG' : 'SHORT'),
        }));
      setOverlayEvents(events);
    } catch (e) {
      if (markersRef.current) markersRef.current.setMarkers([]);
      markersByTimeRef.current = new Map();
      setHoverMarker(null);
      setSignalCount(0);
      setOverlayEvents([]);
    }
  }, [symbol, interval, signalRange, overlayStrategy, dataSource, authHeaders, runOverlay]);

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
    fullscreenRef.current = fullscreen;
    if (chartRef.current && chartContainerRef.current) {
      chartRef.current.applyOptions({
        width: chartContainerRef.current.clientWidth,
        height: getChartHeight(),
      });
      chartRef.current.timeScale().fitContent();
    }
  }, [fullscreen, getChartHeight]);

  useEffect(() => {
    const onFs = () => setFullscreen(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', onFs);
    return () => document.removeEventListener('fullscreenchange', onFs);
  }, []);

  const o = legend || {};

  return (
    <div className={`${fullscreen ? 'fixed inset-0 z-50 bg-gray-950 p-2' : 'page-shell'} text-white min-h-screen font-sans`}>
      {/* Header */}
      <div className="flex flex-wrap justify-between items-center gap-4 mb-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold flex items-center gap-3 text-blue-400">
            <TrendingUp size={28} /> Market Chart
          </h1>
          <p className="text-gray-400 text-sm mt-1">Candlesticks with LONG / SHORT / trend markers — including a finished backtest</p>
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
            <option value="PhantomV2">Kudos V2.5 (Champion)</option>
            <option value="FastTest">FastTest (debug)</option>
            {strategies.map(s => <option key={s.id} value={s.id}>Custom: {s.name}</option>)}
          </select>
        </div>
        <div className="flex items-center gap-2 text-xs text-gray-400 w-full sm:w-auto">
          <div className="w-[9.5rem]"><DateInput value={signalRange.start} onChange={e => setSignalRange({ ...signalRange, start: e.target.value })} /></div>
          <span>→</span>
          <div className="w-[9.5rem]"><DateInput value={signalRange.end} onChange={e => setSignalRange({ ...signalRange, end: e.target.value })} /></div>
        </div>
        <span className="text-xs font-bold text-green-400">{signalCount} markers</span>
        {runOverlay && (
          <span className="text-xs font-semibold text-sky-300 bg-sky-900/30 border border-sky-800/50 rounded px-2 py-1">
            Backtest: {runOverlay.name} · {runOverlay.trades.length} trades
          </span>
        )}
        {interval !== '1h' && (
          <span className="text-[11px] text-amber-300">Switch to 1h to plot LONG/SHORT on the signal candle</span>
        )}
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
        <div className="relative">
          <div ref={chartContainerRef} className="w-full" />
          {noData && (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-gray-500">
              <TrendingUp size={40} className="mb-2 opacity-30" />
              <p className="text-sm">No market data available for {symbol}/{interval}.</p>
              <p className="text-[11px] mt-1">Please run the seeder or ensure Binance is reachable.</p>
            </div>
          )}

          {/* Marker detail on hover — markers are icon-only, so the strategy
              data (side / setup / trend / candle / RSI) appears beside the
              cursor instead of on every candle. */}
          {hoverMarker && (
            <div className="pointer-events-none absolute z-30"
                 style={{ left: hoverMarker.x ?? 0, top: hoverMarker.y ?? 0 }}>
              <div className="-translate-x-1/2 -translate-y-[calc(100%+12px)] whitespace-nowrap rounded-lg border border-gray-600 bg-gray-900/95 px-3 py-2 font-mono text-[11px] shadow-xl">
                <div className="flex items-center gap-2">
                  <span className={`font-bold ${hoverMarker.info.side === 'SHORT' ? 'text-red-400' : 'text-green-400'}`}>
                    {hoverMarker.info.side || 'SIG'}
                  </span>
                  <span className="text-gray-300">{hoverMarker.info.label || ''}</span>
                </div>
                <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-gray-400">
                  {hoverMarker.info.setup && <span>Setup: {hoverMarker.info.setup}</span>}
                  {hoverMarker.info.trend && <span>Trend: {hoverMarker.info.trend}</span>}
                  {hoverMarker.info.candle && <span>Candle: {hoverMarker.info.candle}</span>}
                  {hoverMarker.info.rsi != null && <span>RSI: {Number(hoverMarker.info.rsi).toFixed(1)}</span>}
                  {hoverMarker.info.price != null && <span>Price: {fmt(hoverMarker.info.price)}</span>}
                  {hoverMarker.info.kind === 'exit' && hoverMarker.info.reason && <span>Reason: {hoverMarker.info.reason}</span>}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* TradingView-style value legend */}
        {hoverMarker && (
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 px-2 font-mono text-[11px]">
            <span className={`rounded border px-1.5 py-0.5 font-bold ${
              hoverMarker.info.side === 'SHORT'
                ? 'border-red-800 bg-red-900/20 text-red-300'
                : 'border-green-800 bg-green-900/20 text-green-300'}`}>
              {hoverMarker.info.side || 'SIG'}
            </span>
            <span className="text-gray-300">{hoverMarker.info.label || ''}</span>
            {hoverMarker.info.setup && <span className="text-gray-500">Setup {hoverMarker.info.setup}</span>}
            {hoverMarker.info.trend && <span className="text-gray-500">4h {hoverMarker.info.trend}</span>}
            {hoverMarker.info.candle && <span className="text-gray-500">Candle {hoverMarker.info.candle}</span>}
            {hoverMarker.info.rsi != null && <span className="text-gray-500">RSI {Number(hoverMarker.info.rsi).toFixed(1)}</span>}
            {hoverMarker.info.kind === 'exit' && hoverMarker.info.reason && <span className="text-amber-400">{hoverMarker.info.reason}</span>}
          </div>
        )}
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
        <div className="flex flex-wrap gap-x-5 gap-y-1 mt-1 px-2 text-[11px] text-gray-400">
          <span className="flex items-center gap-1"><span className="text-green-500">▲</span> LONG</span>
          <span className="flex items-center gap-1"><span className="text-red-500">▼</span> SHORT</span>
          <span>REV = reversal · MOM = momentum</span>
          <span>↑ 4h uptrend · ↓ 4h downtrend</span>
          <span className="flex items-center gap-1"><span className="text-sky-400">●</span> IN fill</span>
          <span className="flex items-center gap-1"><span className="text-amber-400">■</span> OUT exit</span>
          <span className="flex items-center gap-1"><Volume2 size={12} className="text-gray-500" /> Volume</span>
        </div>
      </div>

      {overlayEvents.length > 0 && (
        <div className="mt-4 bg-gray-800 rounded-2xl border border-gray-700 overflow-hidden">
          <div className="px-4 py-3 text-xs font-bold uppercase tracking-wider text-gray-400 border-b border-gray-700">
            Signal candles in view ({overlayEvents.length}{overlayEvents.length === 80 ? '+' : ''})
          </div>
          <div className="overflow-x-auto max-h-64 overflow-y-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-gray-900 uppercase text-gray-500 sticky top-0">
                <tr>
                  <th className="p-2 font-semibold">Candle (UTC)</th>
                  <th className="p-2 font-semibold">Side</th>
                  <th className="p-2 font-semibold">Setup</th>
                  <th className="p-2 font-semibold">4h trend</th>
                  <th className="p-2 font-semibold">Candle</th>
                  <th className="p-2 font-semibold">RSI</th>
                </tr>
              </thead>
              <tbody>
                {overlayEvents.map((e) => (
                  <tr key={e.time} className="border-b border-gray-700/70">
                    <td className="p-2 font-mono text-gray-300">{fmtUnixUtc(e.time)}</td>
                    <td className={`p-2 font-bold ${e.side === 'LONG' ? 'text-green-400' : 'text-red-400'}`}>{e.side}</td>
                    <td className="p-2 text-gray-300">{e.setup || '—'}</td>
                    <td className={`p-2 ${e.trend === 'UP' ? 'text-green-400' : e.trend === 'DOWN' ? 'text-red-400' : 'text-gray-500'}`}>{e.trend || '—'}</td>
                    <td className={`p-2 ${e.candle === 'GREEN' ? 'text-green-400' : e.candle === 'RED' ? 'text-red-400' : 'text-gray-500'}`}>{e.candle || '—'}</td>
                    <td className="p-2 font-mono text-gray-400">{e.rsi != null ? Number(e.rsi).toFixed(1) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Info cards */}
      <div className="mt-6 grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-blue-500/20 rounded-lg text-blue-400"><Timer size={20} /></div>
            <h3 className="font-bold">Timeframe</h3>
          </div>
          <p className="text-sm text-gray-400">1m → 1d candle resolution. LONG/SHORT markers plot on the 1h signal candle.</p>
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
          <p className="text-sm text-gray-400">Overlay Kudos signals or a backtest run (`/chart?run=id`) on {symbol}. Each marker shows LONG/SHORT, REV/MOM and 4h trend.</p>
        </div>
      </div>
    </div>
  );
};

export default ChartPage;
