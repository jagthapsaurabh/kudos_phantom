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
  }, [interval, symbol]);

  const fetchData = async () => {
    if (!seriesRef.current) return;
    try {
      const res = await fetch(`${API_URL}/klines?symbol=${symbol}&interval=${interval}`);
      const data = await res.json();
      seriesRef.current.setData(data);
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
        <div className="flex gap-4 items-center">
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
        </div>
      </div>

      <div className="bg-gray-800 p-4 rounded-2xl border border-gray-700 shadow-2xl">
        <div ref={chartContainerRef} className="w-full" />
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
