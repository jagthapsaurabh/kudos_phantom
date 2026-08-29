import React, { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, createSeriesMarkers, CrosshairMode } from 'lightweight-charts';
import { buildOverlayMarkers } from '../utils/chartOverlay';

/** Candlestick pane with LONG/SHORT/IN/OUT markers. Used on the Backtest page
 * so a finished run is visible on market candles, not only as an equity curve.
 */
const MarketOverlayChart = ({ candles = [], trades = [], signals = [], height = 420 }) => {
  const containerRef = useRef();

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const chart = createChart(containerRef.current, {
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
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#1c2537', scaleMargins: { top: 0.05, bottom: 0.08 } },
      timeScale: { borderColor: '#1c2537', timeVisible: true, secondsVisible: false, rightOffset: 6, barSpacing: 6 },
      width: containerRef.current.clientWidth,
      height,
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981',
      downColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
      borderVisible: false,
    });
    const bars = (candles || []).map(d => ({
      time: d.time, open: d.open, high: d.high, low: d.low, close: d.close,
    }));
    if (bars.length) series.setData(bars);
    const times = new Set(bars.map(b => b.time));
    const markers = buildOverlayMarkers({ signals, trades }).filter(m => times.has(m.time));
    createSeriesMarkers(series, markers);
    chart.timeScale().fitContent();
    const onResize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chart.remove();
    };
  }, [candles, trades, signals, height]);

  if (!candles.length) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-gray-500">
        No candles in this date range. Seed market data or shrink the window.
      </div>
    );
  }
  return <div ref={containerRef} className="w-full" style={{ height }} />;
};

export default MarketOverlayChart;
