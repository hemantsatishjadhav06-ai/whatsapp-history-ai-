export function formatDate(value: unknown, timezone = 'Asia/Kolkata', compact = false): string {
  if (typeof value !== 'string' || Number.isNaN(Date.parse(value))) return 'Time unavailable';
  try { return new Intl.DateTimeFormat('en-IN', {timeZone: timezone, day: 'numeric', month: 'short',
    ...(compact ? {} : {year: 'numeric' as const}), hour: 'numeric', minute: '2-digit'}).format(new Date(value)); }
  catch { return 'Timezone unavailable'; }
}
export function textValue(value: unknown, fallback = ''): string { return typeof value === 'string' ? value : fallback; }
export function numberValue(value: unknown, fallback = 0): number { return typeof value === 'number' && Number.isFinite(value) ? value : fallback; }
export function routeObjectId(value: unknown): string | null {
  return typeof value === 'string' && /^[A-Za-z0-9_-]{1,180}$/.test(value) ? value : null;
}
