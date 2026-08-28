// Reusable "skip new trades" schedule editor.
//
// Used by Backtest (per run), Paper Trade and Live Trade (per instance). The
// switch only ever blocks NEW entries — a position that is already open keeps
// running with its stop, target and trail, which is exactly what the client
// asked for.
//
// A window is a day/time range in a chosen timezone:
//   * "Sat 18:30 → Mon 01:00" wraps through the end of the week,
//   * "All day Sunday" blocks the whole day,
//   * any number of windows can be combined (Sunday + Tuesday + …).
import React, { useMemo } from 'react';
import { CalendarClock, Plus, Trash2, Ban } from 'lucide-react';
import {
  WEEKDAYS, COMMON_TIMEZONES, DEFAULT_TIMEZONE, SCHEDULE_PRESETS,
  normalizeSchedule, normalizeWindow, describeSchedule, validateSchedule,
  scheduleSummary, isScheduleActive, blockingWindow, timezoneLabel,
} from '../utils/tradingWindows';

const inputCls = 'rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-white outline-none focus:border-blue-500';
const selectCls = 'rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-white outline-none focus:border-blue-500';

const DaySelect = ({ value, onChange, disabled }) => (
  <select value={Number(value)} onChange={(e) => onChange(Number(e.target.value))}
          disabled={disabled} className={selectCls} title="Weekday">
    {WEEKDAYS.map((d) => <option key={d.value} value={d.value}>{d.short}</option>)}
  </select>
);

const TradingWindowsEditor = ({
  value,
  onChange,
  compact = false,
  showPresets = true,
  title = 'Skip new trades',
  subtitle = 'Block new entries on chosen days and times. Positions already open keep running normally.',
}) => {
  const schedule = useMemo(() => normalizeSchedule(value), [value]);
  const problems = useMemo(() => validateSchedule(schedule), [schedule]);
  const active = isScheduleActive(schedule);
  const blockedNow = blockingWindow(schedule);

  const emit = (next) => onChange(normalizeSchedule(next));

  const setWindow = (index, patch) => emit({
    ...schedule,
    windows: schedule.windows.map((w, i) => (i === index ? normalizeWindow({ ...w, ...patch }) : w)),
  });

  const addWindow = () => emit({
    ...schedule,
    enabled: true,
    windows: [...schedule.windows, {
      label: '', start_day: 5, start_time: '18:30',
      end_day: 0, end_time: '01:00', all_day: false, enabled: true,
    }],
  });

  const removeWindow = (index) => emit({
    ...schedule,
    windows: schedule.windows.filter((_, i) => i !== index),
  });

  return (
    <div className={`rounded-xl border ${active ? 'border-amber-700/60 bg-amber-900/5' : 'border-gray-700 bg-gray-900/40'} p-3`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-gray-200">
            <CalendarClock size={14} className={active ? 'text-amber-400' : 'text-gray-500'} />
            {title}
          </div>
          <div className="mt-1 text-[11px] leading-snug text-gray-500">{subtitle}</div>
        </div>
        <label className="flex shrink-0 cursor-pointer items-center gap-2 rounded-lg border border-gray-700 bg-gray-900 px-3 py-1.5 text-xs text-gray-300">
          <input type="checkbox" checked={schedule.enabled}
                 onChange={(e) => emit({ ...schedule, enabled: e.target.checked })}
                 className="h-3.5 w-3.5 accent-amber-500" />
          <span className="font-semibold text-white">{schedule.enabled ? 'ON' : 'OFF'}</span>
        </label>
      </div>

      {/* Status line: what is blocked, whether it is blocking right now. */}
      <div className={`mt-3 rounded-lg border px-2.5 py-1.5 text-[11px] ${
        active ? 'border-amber-800/50 bg-amber-900/10 text-amber-300'
               : 'border-gray-700 bg-gray-900 text-gray-500'}`}>
        {blockedNow
          ? `⏸ New entries paused now — ${blockedNow.label || 'blocked window'}`
          : scheduleSummary(schedule)}
        {active && (
          <span className="ml-1 text-gray-500">
            ({timezoneLabel(schedule)})
          </span>
        )}
      </div>

      {schedule.enabled && (
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col">
              <label className="mb-1 text-[10px] font-bold uppercase text-gray-500">Timezone</label>
              <input
                list="phantom-timezones"
                value={schedule.timezone}
                onChange={(e) => emit({ ...schedule, timezone: e.target.value || DEFAULT_TIMEZONE })}
                className={inputCls + ' w-48'}
                placeholder="Asia/Kolkata" />
              <datalist id="phantom-timezones">
                {COMMON_TIMEZONES.map((tz) => <option key={tz} value={tz} />)}
              </datalist>
            </div>
            <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-gray-700 bg-gray-900 px-2.5 py-1.5 text-[11px] text-gray-300"
                   title="Default (off): open positions are still managed — stops, targets and trailing keep working inside a window.">
              <input type="checkbox" checked={schedule.block_exits}
                     onChange={(e) => emit({ ...schedule, block_exits: e.target.checked })}
                     className="h-3.5 w-3.5 accent-amber-500" />
              Also freeze exits
            </label>
            <button onClick={addWindow}
                    className="flex items-center gap-1 rounded-lg border border-gray-700 bg-gray-800 px-2.5 py-1.5 text-[11px] font-semibold text-gray-200 transition hover:border-blue-500 hover:text-white">
              <Plus size={12} /> Add window
            </button>
          </div>

          {showPresets && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[10px] font-bold uppercase text-gray-500">Presets:</span>
              {SCHEDULE_PRESETS.map((preset) => (
                <button key={preset.id} onClick={() => emit({ ...schedule, ...preset.build() })}
                        className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-[10px] text-gray-300 transition hover:border-amber-500 hover:text-white">
                  {preset.label}
                </button>
              ))}
            </div>
          )}

          {/* One row per window. */}
          <div className="space-y-2">
            {schedule.windows.map((w, index) => (
              <div key={index}
                   className={`rounded-lg border p-2 ${w.enabled ? 'border-gray-700 bg-gray-900' : 'border-gray-800 bg-gray-900/40 opacity-60'}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <input type="checkbox" checked={w.enabled}
                         onChange={(e) => setWindow(index, { enabled: e.target.checked })}
                         title="Enable / disable this window"
                         className="h-3.5 w-3.5 accent-amber-500" />
                  <input type="text" value={w.label} placeholder="Label (e.g. Weekend gap)"
                         onChange={(e) => setWindow(index, { label: e.target.value })}
                         className={inputCls + ' w-40'} />
                  <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-gray-300">
                    <input type="checkbox" checked={w.all_day}
                           onChange={(e) => setWindow(index, { all_day: e.target.checked })}
                           className="h-3.5 w-3.5 accent-blue-500" />
                    All day
                  </label>
                  <button onClick={() => removeWindow(index)} title="Remove this window"
                          className="ml-auto rounded border border-gray-700 p-1 text-gray-500 transition hover:border-red-700 hover:text-red-300">
                    <Trash2 size={12} />
                  </button>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-gray-400">
                  <span className="font-bold uppercase text-gray-500">From</span>
                  <DaySelect value={w.start_day} disabled={w.all_day && false}
                             onChange={(v) => setWindow(index, { start_day: v, end_day: w.all_day ? v : w.end_day })} />
                  {!w.all_day && (
                    <input type="time" value={w.start_time}
                           onChange={(e) => setWindow(index, { start_time: e.target.value })}
                           className={inputCls + ' w-24'} />
                  )}
                  <span className="font-bold uppercase text-gray-500">to</span>
                  <DaySelect value={w.end_day}
                             onChange={(v) => setWindow(index, { end_day: v, start_day: w.all_day ? v : w.start_day })} />
                  {!w.all_day && (
                    <input type="time" value={w.end_time}
                           onChange={(e) => setWindow(index, { end_time: e.target.value })}
                           className={inputCls + ' w-24'} />
                  )}
                  {!w.all_day && (
                    <span className="text-[10px] text-gray-500">
                      (end before start = wraps past Sunday)
                    </span>
                  )}
                </div>
                <div className="mt-1.5 font-mono text-[10px] text-amber-300/90">
                  {describeSchedule({ ...schedule, windows: [w] })[0] || '—'}
                </div>
              </div>
            ))}
            {schedule.windows.length === 0 && (
              <div className="rounded-lg border border-dashed border-gray-700 p-3 text-center text-[11px] text-gray-500">
                No windows configured — add one, or switch the schedule off.
              </div>
            )}
          </div>

          {!compact && (
            <div className="rounded-lg border border-gray-700 bg-gray-900 p-2 text-[10px] leading-relaxed text-gray-500">
              Entries are refused when the exchange time of the new candle falls inside a window.
              A trade opened before the window keeps its stop-loss, take-profit, breakeven and
              trailing rules until it closes on its own.
            </div>
          )}

          {problems.length > 0 && (
            <div className="space-y-1 rounded-lg border border-red-900/60 bg-red-900/10 p-2 text-[11px] text-red-300">
              {problems.map((p, i) => (
                <div key={i} className="flex items-start gap-1.5"><Ban size={11} className="mt-0.5 shrink-0" />{p}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export { describeSchedule, validateSchedule, scheduleSummary, isScheduleActive, blockingWindow };
export default TradingWindowsEditor;
