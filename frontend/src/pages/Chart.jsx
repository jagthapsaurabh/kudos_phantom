import React, { useEffect, useRef, useState } from 'react';
import { createChart } from 'lightweight-charts';
import { TrendingUp, Timer, Layers } from 'lucide-react';
import { API_URL } from '../api';

const ChartPage = () => {
  const chartContainerRef = useRef();
  const chartRef = useRef();
  const seriesRef = useRef();
  const [interval, setInterval] = useState('1h');
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [showSignals, setShowSignals] = useState(true);
  const [signalRange, setSignalRange] = useState({ start: '2026-01-01', end: '2026-06-25' });
  const [signalCount, setSignalCount] = useState(0);
  const signalsRef = useRef([]);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    try {
      if (chartRef.current) {
        chartRef.current.remove();
      }

      const chart = createChart(chartContainerRef.current, {
        layout: {
          background: { color: '#111827' },
          textColor: '#9ca3af',
        },
        grid: {
          vertLines: { color: '#1f2937' },
          horzLines: { color: '#1f2937' },
        },
        width: chartContainerRef.current.clientWidth,
        height: 600,
      });
      
      chartRef.current = chart;

      // Use a small timeout to ensure the chart instance is fully mounted in the DOM
      setTimeout(() => {
        if (chart && typeof chart.addCandlestickSeries === 'function') {
          const candlestickSeries = chart.addCandlestickSeries({
            upColor: '#4ade80',
            downColor: '#f87171',
            borderVisible: false,
            wickUpColor: '#4ade80',
            wickDownColor: '#f87171',
          });
          seriesRef.current = candlestickSeries;
          fetchData();
        } else {
          console.error("Lightweight Charts Error: addCandlestickSeries not found.");
        }
      }, 100);

      const handleResize = () => {
        if (chartRef.current) {
          chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
        }
      };

      window.addEventListener('resize', handleResize);
      return () => {
        window.removeEventListener('resize', handleResize);
        if (chartRef.current) {
          chartRef.current.remove();
          chartRef.current = null;
        }
      };
    } catch (error) {
      console.error("Critical error initializing chart:", error);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [interval, symbol, showSignals, signalRange]);

  const fetchSignals = async () => {
    try {
      const res = await fetch(
        `${API_URL}/phantom/signals?symbol=${symbol}&start_date=${signalRange.start}&end_date=${signalRange.end}`,
        { headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` } }
      );
      if (!res.ok) return [];
      return await res.json();
    } catch (e) { return []; }
  };

  const fetchData = async () => {
    if (!seriesRef.current) return;
    try {
      const res = await fetch(`${API_URL}/klines?symbol=${symbol}&interval=${interval}`);
      const data = await res.json();
      seriesRef.current.setData(data);

      // Phantom v3 signal-candle overlay (markers on the exact signal bars)
      if (showSignals && interval === '1h') {
        const sigs = await fetchSignals();
        signalsRef.current = sigs;
        const times = new Set(data.map(d => d.time));
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
        seriesRef.current.setMarkers(markers);
        setSignalCount(markers.length);
      } else {
        seriesRef.current.setMarkers([]);
        setSignalCount(0);
      }
    } catch (e) {
      console.error("Error fetching chart data", e);
    }
  };

  return (
    <div className="ml-64 p-8 bg-gray-900 text-white min-h-screen">
      <div className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-3 text-blue-400">
            <TrendingUp size={32} /> Market Analysis
          </h1>
          <p className="text-gray-400 text-sm mt-1">Real-time BTC/USDT technical chart</p>
        </div>
        <div className="flex gap-4 items-center flex-wrap">
          <div className="flex items-center gap-2 bg-gray-800 p-1 rounded-lg border border-gray-700">
            {['1m', '5m', '15m', '1h', '4h', '1d'].map(int => (
              <button 
                key={int} 
                onClick={() => setInterval(int)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition ${interval === int ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'}`}
              >
                {int}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 bg-gray-800 px-4 py-2 rounded-lg border border-gray-700">
            <Layers size={16} className="text-gray-400" />
            <span className="text-sm font-bold">{symbol}</span>
          </div>
          <div className="flex items-center gap-3 bg-gray-800 px-4 py-2 rounded-lg border border-gray-700">
            <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
              <input type="checkbox" checked={showSignals} onChange={e => setShowSignals(e.target.checked)} className="accent-green-500" />
              Phantom Signals
            </label>
            <input type="date" value={signalRange.start} onChange={e => setSignalRange({ ...signalRange, start: e.target.value })}
              className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-white" />
            <span className="text-gray-500 text-xs">→</span>
            <input type="date" value={signalRange.end} onChange={e => setSignalRange({ ...signalRange, end: e.target.value })}
              className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-white" />
            {showSignals && interval === '1h' && (
              <span className="text-xs font-bold text-green-400">{signalCount} markers</span>
            )}
          </div>
        </div>
      </div>

      <div className="bg-gray-800 p-4 rounded-2xl border border-gray-700 shadow-2xl">
        <div ref={chartContainerRef} className="w-full" />
        <div className="flex gap-6 mt-3 px-2 text-[11px] text-gray-400">
          <span className="flex items-center gap-1"><span className="text-green-500">▲</span> Long signal candle</span>
          <span className="flex items-center gap-1"><span className="text-red-500">▼</span> Short signal candle</span>
          <span className="text-gray-600">Label: M = Momentum setup, R = Reversal setup, with RSI/ADX at that candle (1h overlay, tuned v3 config)</span>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-blue-500/20 rounded-lg text-blue-400"><Timer size={20} /></div>
            <h3 className="font-bold">Timeframe: {interval}</h3>
          </div>
          <p className="text-sm text-gray-400">Adjusting the timeframe updates the data resolution for the candles.</p>
        </div>
        <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-green-500/20 rounded-lg text-green-400"><TrendingUp size={20} /></div>
            <h3 className="font-bold">Real-time Sync</h3>
          </div>
          <p className="text-sm text-gray-400">Data is fetched directly from Binance Futures API for institutional precision.</p>
        </div>
        <div className="bg-gray-800 p-6 rounded-2xl border border-gray-700">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-purple-500/20 rounded-lg text-purple-400"><Layers size={20} /></div>
            <h3 className="font-bold">Symbol: {symbol}</h3>
          </div>
          <p className="text-sm text-gray-400">Currently analyzing BTCUSDT. More symbols coming soon in the Pro version.</p>
        </div>
      </div>
    </div>
  );
};

export default ChartPage;
