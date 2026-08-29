import React from 'react';

/**
 * Badges for the guards that decide whether a signal becomes an order.
 *
 * - `blocked`  — refused by the "skip new trades" schedule
 * - `held`     — refused by the entry gates: one order per signal candle, one
 *                position at a time, and the post-exit cooldown. Without these
 *                a condition that stays TRUE for several candles fired a fresh
 *                order on every 60-second tick, so the counter is how an
 *                operator sees the worker deliberately doing nothing.
 * - `position` — a position the venue already holds (an earlier run, a restart
 *                or a manual terminal order). New entries are held so it is
 *                never doubled up, and this badge says why.
 * - `shared`   — how many live runs point at the same broker account. One
 *                futures account carries ONE netted position per contract, so
 *                only one of those runs can hold a trade and the rest queue.
 *                Without this badge an idle strategy looks broken.
 */
const EntryGuardBadges = ({ blocked = 0, held = 0, reason = '', position = null,
                            broker = 'the broker', shared = null }) => (
  <>
    {blocked > 0 && (
      <span className="rounded border border-red-900/60 bg-red-900/20 px-1.5 py-0.5 text-[9px] font-bold text-red-300"
            title="New entries skipped by your trading windows">
        {blocked} skipped
      </span>
    )}
    {held > 0 && (
      <span className="rounded border border-sky-900/60 bg-sky-900/20 px-1.5 py-0.5 text-[9px] font-bold text-sky-300"
            title={reason || 'Signal refused by the entry guards'}>
        {held} held
      </span>
    )}
    {position && (
      <span className="rounded border border-purple-800/60 bg-purple-900/20 px-1.5 py-0.5 text-[9px] font-bold text-purple-300"
            title={`Position already on ${broker} — new entries are held so it is never doubled up`}>
        VENUE {position.direction === 1 ? 'LONG' : 'SHORT'} {Number(position.size_btc || 0).toFixed(4)}
      </span>
    )}
    {shared && shared.strategies_on_account > 1 && (
      <span className="rounded border border-cyan-800/60 bg-cyan-900/20 px-1.5 py-0.5 text-[9px] font-bold text-cyan-300"
            title={`${shared.note}\nRunning together: ${shared.other_strategies.join(', ')}${
              shared.position_held_by ? `\nPosition currently held by: ${shared.position_held_by}` : ''}`}>
        {shared.holds_account_position
          ? `HOLDS ACCOUNT · ${shared.strategies_on_account} SHARED`
          : `QUEUED ${shared.queue_position}/${shared.strategies_on_account}`}
      </span>
    )}
  </>
);

export default EntryGuardBadges;
