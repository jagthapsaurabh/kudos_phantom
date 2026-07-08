import React from 'react';
import TradingPage from './TradingPage';

const PaperTrade = () => <TradingPage type="paper" />;
const LiveTrade = () => <TradingPage type="live" />;

export { PaperTrade, LiveTrade };
