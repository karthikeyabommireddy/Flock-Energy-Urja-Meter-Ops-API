import { useMemo, useSyncExternalStore } from 'react';

export type Tone = 'ok' | 'bad' | 'warn' | 'muted';

const STATUS_LABELS: Record<string, string> = {
  INSTALLED: 'Installed',
  FAULTY: 'Faulty',
  DECOMMISSIONED: 'Decommissioned',
};

export function statusLabel(status: string | null | undefined): string {
  if (!status) return 'Unknown';
  return STATUS_LABELS[status.toUpperCase()] ?? status;
}

export function statusTone(status: string | null | undefined): Tone {
  switch (status?.toUpperCase()) {
    case 'INSTALLED':
      return 'ok';
    case 'FAULTY':
      return 'bad';
    default:
      return 'muted';
  }
}

export function formatNumber(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return value.toLocaleString('en-IN', { maximumFractionDigits: digits });
}

/** "12.5 kWh", or a dash when there is no number: never "— kWh". */
export function formatWithUnit(value: number | null | undefined, unit: string, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${formatNumber(value, digits)} ${unit}`;
}

/** "1 reading", "337 readings"; pass the plural form when it is not just an "s" ("stretch", "stretches"). */
export function plural(count: number, noun: string, many = `${noun}s`): string {
  return `${formatNumber(count, 0)} ${count === 1 ? noun : many}`;
}

export function formatPercent(part: number, whole: number): string {
  return whole === 0 ? '—' : `${((part / whole) * 100).toFixed(0)}%`;
}

function formatMoment(moment: string | number, options: Intl.DateTimeFormatOptions, timeZone?: string | null): string {
  const date = new Date(moment);
  if (Number.isNaN(date.getTime())) return String(moment); // a label that is not a time is shown as it came
  try {
    return new Intl.DateTimeFormat('en-GB', { ...options, timeZone: timeZone ?? 'UTC' }).format(date);
  } catch {
    return new Intl.DateTimeFormat('en-GB', { ...options, timeZone: 'UTC' }).format(date); // a zone this browser lacks
  }
}

/** A moment shown in the zone the portal itself shows it in, so what the API says matches the portal's screens. */
export function formatTime(moment: string | number, timeZone?: string | null): string {
  return formatMoment(moment, { dateStyle: 'medium', timeStyle: 'short' }, timeZone);
}

export function formatTick(moment: number, timeZone?: string | null): string {
  return formatMoment(moment, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }, timeZone);
}

/**
 * Colours for the places CSS variables cannot be used directly (SVG chart attributes, Leaflet path options). They
 * are read from the same tokens the stylesheet defines, so the two can never drift apart.
 */
export interface Palette {
  ok: string;
  bad: string;
  warn: string;
  muted: string;
  primary: string;
  grid: string;
  text: string;
  surface: string;
}

function readPalette(): Palette {
  const style = getComputedStyle(document.documentElement);
  const token = (name: string) => style.getPropertyValue(name).trim() || 'currentColor';
  return {
    ok: token('--ok'),
    bad: token('--bad'),
    warn: token('--warn'),
    muted: token('--neutral'),
    primary: token('--primary'),
    grid: token('--border'),
    text: token('--muted'),
    surface: token('--surface'),
  };
}

const DARK_QUERY = '(prefers-color-scheme: dark)';

function subscribeToScheme(onChange: () => void): () => void {
  const media = window.matchMedia(DARK_QUERY);
  media.addEventListener('change', onChange);
  return () => media.removeEventListener('change', onChange);
}

export function usePalette(): Palette {
  const dark = useSyncExternalStore(
    subscribeToScheme,
    () => window.matchMedia(DARK_QUERY).matches,
    () => false,
  );
  // `dark` changes exactly when the stylesheet swaps its tokens, so it is what makes the colours be read again.
  return useMemo(() => readPalette(), [dark]);
}

export function toneColor(palette: Palette, tone: Tone): string {
  return palette[tone];
}
