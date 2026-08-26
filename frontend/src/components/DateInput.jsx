import React, { useRef } from 'react';
import { Calendar } from 'lucide-react';

/**
 * Dark-theme date field. Native calendar icons are often invisible on dark
 * backgrounds; this wraps the input so the whole field (and our icon) opens
 * the picker via showPicker() when supported.
 */
const DateInput = ({ value, onChange, className = '', disabled = false, min, max, name, id, title }) => {
  const ref = useRef(null);

  const openPicker = (e) => {
    if (disabled) return;
    const el = ref.current;
    if (!el) return;
    try {
      if (typeof el.showPicker === 'function') {
        e?.preventDefault?.();
        el.showPicker();
        return;
      }
    } catch (_) {
      /* showPicker throws if the input is not in a user gesture / insecure context */
    }
    el.focus();
    el.click?.();
  };

  return (
    <div className={`date-input-wrap ${disabled ? 'opacity-50 pointer-events-none' : ''}`}>
      <input
        ref={ref}
        type="date"
        name={name}
        id={id}
        title={title}
        min={min}
        max={max}
        disabled={disabled}
        value={value || ''}
        onChange={onChange}
        onClick={openPicker}
        onFocus={(e) => {
          try { e.target.showPicker?.(); } catch (_) {}
        }}
        className={`date-input-field ${className}`}
      />
      <button
        type="button"
        tabIndex={-1}
        aria-label="Open calendar"
        disabled={disabled}
        onClick={openPicker}
        className="date-input-icon"
      >
        <Calendar size={15} />
      </button>
    </div>
  );
};

export default DateInput;
