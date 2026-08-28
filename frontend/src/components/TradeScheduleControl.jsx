import React from 'react';
import { Clock, Plus, Trash2 } from 'lucide-react';

const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const DEFAULT_BLOCK = { start_day: 'Saturday', start_time: '17:30', end_day: 'Sunday', end_time: '17:30' };

const normalizeValue = (v) => ({
  skip_new_trades: !!v?.skip_new_trades,
  skip_days: Array.isArray(v?.skip_days) ? v.skip_days.filter(Boolean) : [],
  skip_blocks: Array.isArray(v?.skip_blocks) ? v.skip_blocks.map(b => ({ ...DEFAULT_BLOCK, ...(b || {}) })) : [],
});

const TradeScheduleControl = ({ value, onChange }) => {
  const v = normalizeValue(value);
  const set = (patch) => onChange({ ...v, ...patch });
  const toggleSkip = (e) => set({ skip_new_trades: e.target.checked });
  const toggleDay = (day) => {
    const next = v.skip_days.includes(day)
      ? v.skip_days.filter(d => d !== day)
      : [...v.skip_days, day];
    set({ skip_days: next });
  };
  const updateBlock = (idx, patch) => {
    const blocks = v.skip_blocks.map((b, i) => i === idx ? { ...b, ...patch } : b);
    set({ skip_blocks: blocks });
  };
  const addBlock = () => set({ skip_blocks: [...v.skip_blocks, { ...DEFAULT_BLOCK }] });
  const removeBlock = (idx) => set({ skip_blocks: v.skip_blocks.filter((_, i) => i !== idx) });

  return (
    <div className="rounded-xl border border-gray-700 bg-gray-900/60 p-3">
      <label className="flex cursor-pointer items-start gap-2">
        <input type="checkbox" checked={v.skip_new_trades}
          onChange={toggleSkip}
          className="mt-0.5 h-3.5 w-3.5 accent-blue-500" />
        <span>
          <span className="block text-xs font-bold text-white">Skip new trades in weekly window</span>
          <span className="mt-0.5 block text-[10px] leading-snug text-gray-500">
            Already-open trades keep running. Only new entries are suppressed. Times are India Standard Time (UTC+5:30).
          </span>
        </span>
      </label>

      {v.skip_new_trades && (
        <div className="mt-3 space-y-3 border-t border-gray-700 pt-3">
          <div>
            <div className="mb-1.5 flex items-center gap-1 text-[10px] font-bold uppercase text-gray-500">
              <Clock size={11} /> Skip full days
            </div>
            <div className="flex flex-wrap gap-1.5">
              {DAYS.map(day => (
                <label key={day}
                  className={`flex cursor-pointer items-center gap-1 rounded-lg border px-2 py-1 text-[10px] transition ${
                    v.skip_days.includes(day)
                      ? 'border-red-800/50 bg-red-900/20 text-red-300'
                      : 'border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600'}`}>
                  <input type="checkbox" className="hidden" checked={v.skip_days.includes(day)}
                    onChange={() => toggleDay(day)} />
                  {day.slice(0, 3)}
                </label>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-[10px] font-bold uppercase text-gray-500">Custom windows (start → end)</span>
              <button type="button" onClick={addBlock}
                className="flex items-center gap-1 rounded border border-gray-700 px-2 py-1 text-[10px] text-blue-300 transition hover:border-blue-500 hover:text-white">
                <Plus size={11} /> Add window
              </button>
            </div>
            <div className="space-y-2">
              {v.skip_blocks.length === 0 && (
                <p className="text-[10px] text-gray-600">No custom windows — add one (e.g. Saturday 17:30 → Sunday 17:30).</p>
              )}
              {v.skip_blocks.map((b, idx) => (
                <div key={idx} className="grid grid-cols-[1fr_auto_1fr_auto] items-center gap-2 rounded-lg border border-gray-700 bg-gray-800 p-2 sm:grid-cols-[1fr_68px_auto_1fr_68px_auto]">
                  <div className="flex flex-col">
                    <select className="rounded border border-gray-700 bg-gray-900 px-1.5 py-1 text-[10px] text-white"
                      value={b.start_day} onChange={e => updateBlock(idx, { start_day: e.target.value })}>
                      {DAYS.map(d => <option key={d} value={d}>{d}</option>)}
                    </select>
                    <input type="time" value={b.start_time}
                      onChange={e => updateBlock(idx, { start_time: e.target.value })}
                      className="mt-1 rounded border border-gray-700 bg-gray-900 px-1.5 py-1 text-[10px] text-white" />
                  </div>
                  <span className="text-center text-[10px] text-gray-500">→</span>
                  <div className="flex flex-col">
                    <select className="rounded border border-gray-700 bg-gray-900 px-1.5 py-1 text-[10px] text-white"
                      value={b.end_day} onChange={e => updateBlock(idx, { end_day: e.target.value })}>
                      {DAYS.map(d => <option key={d} value={d}>{d}</option>)}
                    </select>
                    <input type="time" value={b.end_time}
                      onChange={e => updateBlock(idx, { end_time: e.target.value })}
                      className="mt-1 rounded border border-gray-700 bg-gray-900 px-1.5 py-1 text-[10px] text-white" />
                  </div>
                  <button type="button" onClick={() => removeBlock(idx)}
                    className="rounded p-1.5 text-gray-500 transition hover:bg-red-900/20 hover:text-red-400"
                    title="Remove window">
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
            </div>
            <p className="mt-1.5 text-[9px] leading-snug text-gray-600">
              Windows may cross midnight and the week boundary. The default is the weekend pause: Saturday 17:30 → Sunday 17:30 IST.
            </p>
          </div>
        </div>
      )}
    </div>
  );
};

export default TradeScheduleControl;
