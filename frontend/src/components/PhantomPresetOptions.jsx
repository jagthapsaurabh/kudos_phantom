import React from 'react';
import { PHANTOM_PRESETS } from '../utils/phantomPresets';

// The built-in separated Kudos strategies for a <select>. Drop it right after
// the "Kudos V2.5 (Default)" option in any strategy dropdown; the ids are
// understood by every backend endpoint (backtest, signals, paper, live).
const PhantomPresetOptions = ({ label = 'Kudos presets — same strategy, one setup / side' }) => (
  <optgroup label={label}>
    {PHANTOM_PRESETS.map(p => (
      <option key={p.id} value={p.id}>{p.name}</option>
    ))}
  </optgroup>
);

export default PhantomPresetOptions;
