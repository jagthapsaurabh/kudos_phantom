import React, { useEffect, useRef, useState, useCallback } from 'react';
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  createSeriesMarkers,
  CrosshairMode,
} from 'lightweight-charts';
import { TrendingUp, Timer, Layers, Volume2, Radio, Filter } from 'lucide-react';
import { API_URL } from '../api';

const INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d'];

const ChartPage = () => {
  const chartContainerRef = useRef();
  const chartRef = useRef();
  const candleSeriesRef = useRef();
  const volumeSeriesRef = useRef();
  const markersRef = useRef();
  const [interval, setInterval] = useState('1h');
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [symbols, setSymbols] = useState(['BTCUSDT']);
  const [showSignals, setShowSignals] = useState(true);
  const [signalRange, setSignalRange] = useState({ start: '2026-01-01', end: '2026-06-25' });
  const [signalCount, setSignalCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [lastPrice, setLastPrice] = useState('—');
  const [dataLen, setDataLen] = useState(0);
  const [noData, setNoData] = useState(false);

  useEffect(() => {
    fetch(`${API_URL}/symbols`, { headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } })
      .then(r => r.ok ? r.json() : ['BTCUSDT'])
      .then(list => {
        if (Array.isArray(list) && list.length) {
          setSymbols(list);
          if (!list.includes(symbol)) setSymbol(list[0]);
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const initChart = useCallback(() => {
    if (!chartContainerRef.current) return;
    try {
      if (chartRef.current) chartRef.current.remove();

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
        height: 560,
      });
      chartRef.current = chart;
      markersRef.current = null;

      // Candlestick series (main pane, paneIndex 0)
      const candleSeries = chart.addSeries(CandlestickSeries, {
        upColor: '#10b981',
        downColor: '#ef4444',
        wickUpColor: '#10b981',
        wickDownColor: '#ef4444',
        borderVisible: false,
        priceLineVisible: true,
        lastValueVisible: true,
      });
      candleSeriesRef.current = candleSeries;

      // Volume histogram (secondary pane, paneIndex 1)
      const volumeSeries = chart.addSeries(HistogramSeries, {
        priceFormat: { type: 'volume' },
        lastValueVisible: false,
        priceLineVisible: false,
      }, 1);
      volumeSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.85, bottom: 0 },
      });
      volumeSeriesRef.current = volumeSeries;

      // Fit the time scale once data is set
      chart.timeScale().fitContent();

      const handleResize = () => {
        if (chartRef.current && chartContainerRef.current) {
          chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
        }
      };
      window.addEventListener('resize', handleResize);

      return () => {
        window.removeEventListener('resize', handleResize);
      };
    } catch (error) {
      console.error('Critical error initializing chart:', error);
    }
  }, []);

  useEffect(() => {
    const cleanup = initChart();
    // Rebuild the chart whenever symbol/interval changes (chart object is per symbol/interval)
    return () => {
      if (cleanup) cleanup();
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
    };
  }, [initChart, interval, symbol]);

  const fetchSignals = useCallback(async () => {
    try {
      const res = await fetch(
        `${API_URL}/phantom/signals?symbol=${symbol}&start_date=${signalRange.start}&end_date=${signalRange.end}`,
        { headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } }
      );
      if (!res.ok) return [];
      return await res.json();
    } catch (e) {
      return [];
    }
  }, [symbol, signalRange.start, signalRange.end]);

  const fetchData = useCallback(async () => {
    if (!candleSeriesRef.current) return;
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/klines?symbol=${symbol}&interval=${interval}&limit=500`);
      const data = await res.json();
      const candles = data.map(d => ({ time: d.time, open: d.open, high: d.high, low: d.low, close: d.close }));
      candleSeriesRef.current.setData(candles);
      chartRef.current?.timeScale().fitContent();
      setDataLen(candles.length);
      setNoData(candles.length === 0);
      setLastPrice(Number(candles[candles.length - 1]?.close ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 }));

      // Volume histogram
      if (volumeSeriesRef.current) {
        volumeSeriesRef.current.setData(
          data.map(d => ({
            time: d.time,
            value: d.volume ?? 0,
            color: d.close >= d.open ? 'rgba(16,185,129,0.45)' : 'rgba(239,68,68,0.45)',
          }))
        );
      }

      // Phantom v3 signal-candle overlay (markers on the exact signal bars)
      if (showSignals && interval === '1h' && candles.length) {
        const sigs = await fetchSignals();
        const times = new Set(candles.map(d => d.time));
        const markers = sigs
          .filter(s => times.has(s.time))
          .map(s => ({
            time: s.time,
            position: s.direction === 1 ? 'belowBar' : 'aboveBar',
            color: s.direction === 1 ? '#22c55e' : '#ef4444',
            shape: s.direction === 1 ? 'arrowUp' : 'arrowDown',
            text: `${s.setup === 'MOMENTUM' ? 'M' : 'R'} RSI:${s.rsi14.toFixed(0)} ADX:${s.adx.toFixed(0)}`,
          }))
          .sort((a, b) => a.time - b.time);
        markersRef.current = createSeriesMarkers(candleSeriesRef.current, markers);
        setSignalCount(markers.length);
      } else if (markersRef.current) {
        markersRef.current.setMarkers([]);
        setSignalCount(0);
      }
    } catch (e) {
      console.error('Error fetching chart data', e);
    } finally {
      setLoading(false);
    }
  }, [symbol, interval, showSignals, fetchSignals]);

  useEffect(() => {
    if (chartRef.current && candleSeriesRef.current) fetchData();
  }, [fetchData]);

  return (
    <div className="ml-64 p-8 bg-gray-900 text-white min-h-screen font-sans">
      {/* Header */}
      <div className="flex flex-wrap justify-between items-center gap-4 mb-6">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-3 text-blue-400">
            <TrendingUp size={32} /> Market Chart
          </h1>
          <p className="text-gray-400 text-sm mt-1">Candlestick, volume & Phantom signal overlay — TradingView-style</p>
        </div>
        <div className="flex gap-3 items-center flex-wrap">
          <div className="flex items-center gap-2 bg-gray-800 p-1 rounded-lg border border-gray-700">
            {INTERVALS.map(int => (
              <button
                key={int}
                onClick={() => setInterval(int)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition ${interval === int ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'}`}
              >
                {int}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 bg-gray-800 px-3 py-2 rounded-lg border border-gray-700">
            <Layers size={16} className="text-gray-400" />
            <select
              value={symbol}
              onChange={e => setSymbol(e.target.value)}
              className="bg-transparent text-sm font-bold text-white outline-none cursor-pointer"
            >
              {symbols.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
        </div>
      </div>

      {/* Signal controls */}
      <div className="flex flex-wrap items-center gap-4 mb-6 bg-gray-800 px-4 py-3 rounded-2xl border border-gray-700">
        <div className="flex items-center gap-2">
          <Filter size={16} className="text-gray-400" />
          <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
            <input type="checkbox" checked={showSignals} onChange={e => setShowSignals(e.target.checked)} className="accent-green-500" />
            Phantom Signals
          </label>
        </div>
        <div className="flex items-center gap-2 text-xs text-gray-400">
          <input type="date" value={signalRange.start} onChange={e => setSignalRange({ ...signalRange, start: e.target.value })}
            className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-white" />
          <span>→</span>
          <input type="date" value={signalRange.end} onChange={e => setSignalRange({ ...signalRange, end: e.target.value })}
            className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-white" />
        </div>
        {showSignals && interval === '1h' && <span className="text-xs font-bold text-green-400">{signalCount} markers</span>}
        {loading && <span className="text-xs text-gray-500 animate-pulse">Loading…</span>}
      </div>

      {/* Chart */}
      <div className="bg-gray-900 p-3 rounded-2xl border border-gray-700 shadow-2xl relative">
        <div className="absolute top-4 left-6 z-10 flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-800/90 border border-gray-700 text-xs">
          <Radio size={14} className="text-green-400 animate-pulse" />
          <span className="font-mono font-bold text-green-400">{symbol}</span>
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
        <div className="flex gap-6 mt-3 px-2 text-[11px] text-gray-400">
          <span className="flex items-center gap-1"><span className="text-green-500">▲</span> Long signal</span>
          <span className="flex items-center gap-1"><span className="text-red-500">▼</span> Short signal</span>
          <span className="flex items-center gap-1"><Volume2 size={12} className="text-gray-500" /> Volume</span>
          <span className="text-gray-600">Label: M = Momentum setup, R = Reversal setup, with RSI/ADX (1h overlay)</span>
        </div>
      </div>

      {/* Info cards */}
      <div className="mt-6 grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-blue-500/20 rounded-lg text-blue-400"><Timer size={20} /></div>
            <h3 className="font-bold">Timeframe</h3>
          </div>
          <p className="text-sm text-gray-400">Adjust between 1m and 1d to change candle resolution.</p>
        </div>
        <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-green-500/20 rounded-lg text-green-400"><Radio size={20} /></div>
            <h3 className="font-bold">Live Data</h3>
          </div>
          <p className="text-sm text-gray-400">Loaded from the local market-data store with live Binance fallback. {dataLen} candles.</p>
        </div>
        <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-purple-500/20 rounded-lg text-purple-400"><Volume2 size={20} /></div>
            <h3 className="font-bold">Symbol</h3>
          </div>
          <p className="text-sm text-gray-400">{symbol} with volume & signal overlays.</p>
        </div>
      </div>
    </div>
  );
};

export default ChartPage;
