import React from 'react';

const typeStyles = {
  info: 'border-blue-700 bg-blue-900/30 text-blue-200',
  success: 'border-green-700 bg-green-900/30 text-green-200',
  warning: 'border-yellow-700 bg-yellow-900/30 text-yellow-200',
  error: 'border-red-700 bg-red-900/30 text-red-200',
};

const typeIcon = {
  info: 'ℹ',
  success: '✓',
  warning: '⚠',
  error: '✕',
};

export default function ToastContainer({ toasts, onRemove }) {
  if (!toasts || toasts.length === 0) return null;
  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 max-w-sm w-full pointer-events-none">
      {toasts.map(t => (
        <div
          key={t.id}
          className={`pointer-events-auto rounded-xl border px-4 py-3 shadow-2xl backdrop-blur-sm text-sm flex items-start gap-2 transition-all ${typeStyles[t.type] || typeStyles.info}`}
        >
          <span className="font-bold text-base leading-none mt-0.5">{typeIcon[t.type] || 'ℹ'}</span>
          <div className="flex-1 min-w-0">
            <div className="font-semibold break-words">{t.message}</div>
            {t.code && <div className="text-[10px] opacity-60 mt-0.5 font-mono">Code: {t.code}</div>}
            {t.hint && <div className="text-[11px] opacity-80 mt-1 italic">{t.hint}</div>}
            {t.details && (
              <div className="text-[10px] opacity-60 mt-1 break-all">
                {typeof t.details === 'string' ? t.details : JSON.stringify(t.details).slice(0, 200)}
              </div>
            )}
          </div>
          <button
            onClick={() => onRemove(t.id)}
            className="shrink-0 ml-2 text-xs opacity-60 hover:opacity-100 transition"
            aria-label="Dismiss"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}
