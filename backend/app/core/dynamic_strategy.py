import pandas as pd
import numpy as np
from .indicators import compute_indicators, ema, sma, rsi, macd, adx_di, atr

class DynamicStrategyService:
    """
    Chartink-like Dynamic Strategy Evaluator.
    Evaluates a set of nested rules against market data.
    """
    def __init__(self, rules: list):
        # rules is now a list of rule objects or a single group object
        self.rules = rules 
        self.indicator_cache = {}

    def _get_indicator_series(self, df, field_config):
        """
        Returns a pandas series for the given field configuration.
        field_config: { 'type': 'indicator'|'field', 'name': 'ema', 'params': { 'length': 50 } }
        """
        if not isinstance(field_config, dict):
            return None

        # Normalize config for simple cases
        if 'name' not in field_config:
            return None

        name = field_config['name']
        params = field_config.get('params', {})
        
        # Cache key includes params to avoid collisions (e.g., ema50 vs ema200)
        cache_key = (id(df), name, tuple(sorted(params.items())))
        if cache_key in self.indicator_cache:
            return self.indicator_cache[cache_key]

        res = None
        # 1. Basic Price Data
        if name == 'close': res = df['close']
        elif name == 'open': res = df['open']
        elif name == 'high': res = df['high']
        elif name == 'low': res = df['low']
        elif name == 'volume': res = df['volume']
        
        # 2. Indicators
        elif name == 'ema':
            length = int(params.get('length', 50))
            res = pd.Series(ema(df['close'].values, length), index=df.index)
        elif name == 'sma':
            length = int(params.get('length', 50))
            res = pd.Series(sma(df['close'].values, length), index=df.index)
        elif name == 'rsi':
            length = int(params.get('length', 14))
            res = pd.Series(rsi(df['close'].values, length), index=df.index)
        elif name == 'atr':
            length = int(params.get('length', 14))
            res = pd.Series(atr(df['high'].values, df['low'].values, df['close'].values, length), index=df.index)
        elif name == 'adx':
            length = int(params.get('length', 14))
            adx, _, _ = adx_di(df['high'].values, df['low'].values, df['close'].values, length)
            res = pd.Series(adx, index=df.index)
        elif name == 'pdi':
            length = int(params.get('length', 14))
            _, pdi, _ = adx_di(df['high'].values, df['low'].values, df['close'].values, length)
            res = pd.Series(pdi, index=df.index)
        elif name == 'mdi':
            length = int(params.get('length', 14))
            _, _, mdi = adx_di(df['high'].values, df['low'].values, df['close'].values, length)
            res = pd.Series(mdi, index=df.index)
        elif name == 'macd_line':
            fast = int(params.get('fast', 12))
            slow = int(params.get('slow', 26))
            sig = int(params.get('signal', 9))
            line, _, _ = macd(df['close'].values, fast, slow, sig)
            res = pd.Series(line, index=df.index)
        elif name == 'macd_signal':
            fast = int(params.get('fast', 12))
            slow = int(params.get('slow', 26))
            sig = int(params.get('signal', 9))
            _, signal, _ = macd(df['close'].values, fast, slow, sig)
            res = pd.Series(signal, index=df.index)
        elif name == 'macd_hist':
            fast = int(params.get('fast', 12))
            slow = int(params.get('slow', 26))
            sig = int(params.get('signal', 9))
            _, _, hist = macd(df['close'].values, fast, slow, sig)
            res = pd.Series(hist, index=df.index)

        self.indicator_cache[cache_key] = res
        return res

    def _evaluate_node(self, df_1h, df_4h, node, idx):
        """
        Recursively evaluates a rule node (either a single condition or a group).
        """
        if not node or not node.get('enabled', True):
            return True # Disabled rules are considered passed

        if node.get('type') == 'group':
            op = node.get('operator', 'AND').upper()
            children = node.get('children', [])
            if not children: return True
            
            if op == 'AND':
                return all(self._evaluate_node(df_1h, df_4h, child, idx) for child in children)
            elif op == 'OR':
                return any(self._evaluate_node(df_1h, df_4h, child, idx) for child in children)
            return True

        # Single Condition
        # Expects: { type: 'condition', left: {...}, op: 'gt', right: {...}, timeframe: '4h' }
        timeframe = node.get('timeframe', '1h')
        df = df_4h if timeframe == '4h' else df_1h
        
        left_cfg = node.get('left')
        right_cfg = node.get('right')
        op = node.get('op')

        # Get left series
        l_series = self._get_indicator_series(df, left_cfg)
        if l_series is None: return False
        # Apply offset
        offset_l = int(left_cfg.get('offset', 0))
        if offset_l != 0:
            l_series = l_series.shift(offset_l)

        # Get right value/series
        if isinstance(right_cfg, (int, float)) or (isinstance(right_cfg, str) and right_cfg.replace('.','',1).isdigit()):
            r_val = float(right_cfg)
            r_series = pd.Series(r_val, index=df.index)
        elif isinstance(right_cfg, dict):
            if right_cfg.get('type') == 'number':
                r_val = float(right_cfg.get('value', 0))
                r_series = pd.Series(r_val, index=df.index)
            else:
                r_series = self._get_indicator_series(df, right_cfg)
                if r_series is None: return False
                offset_r = int(right_cfg.get('offset', 0))
                if offset_r != 0:
                    r_series = r_series.shift(offset_r)
        else:
            return False

        current_time = df_1h.index[idx]
        l_val = l_series.asof(current_time)
        r_val = r_series.asof(current_time)

        if pd.isna(l_val) or pd.isna(r_val): return False

        if op == 'gt': return l_val > r_val
        if op == 'lt': return l_val < r_val
        if op == 'eq': return l_val == r_val
        if op == 'neq': return l_val != r_val
        if op == 'gte': return l_val >= r_val
        if op == 'lte': return l_val <= r_val
        
        if op == 'crosses_above' or op == 'crosses_below':
            try:
                target_idx = df.index.get_indexer([current_time], method='ffill')[0]
                if target_idx < 1: return False
                
                l_prev = l_series.iloc[target_idx-1]
                r_prev = r_series.iloc[target_idx-1]
                
                if op == 'crosses_above': return l_prev <= r_prev and l_val > r_val
                if op == 'crosses_below': return l_prev >= r_prev and l_val < r_val
            except:
                return False
        
        return False

    def generate_signals(self, df_1h: pd.DataFrame, df_4h: pd.DataFrame):
        n = len(df_1h)
        signals = np.zeros(n)
        self.indicator_cache = {}
        
        # Normalize rules to a group if it's just a list
        if isinstance(self.rules, list):
            root_node = {
                'type': 'group',
                'operator': 'AND',
                'children': self.rules,
                'enabled': True
            }
        else:
            root_node = self.rules
            
        for i in range(1, n):
            if self._evaluate_node(df_1h, df_4h, root_node, i):
                signals[i] = 1
            
        return signals
