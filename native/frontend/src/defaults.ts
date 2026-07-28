/**
 * Fallbacks used only while `/api/meta` has not answered. Controls built from
 * these still write through to the sidecar, which validates and reports back —
 * they are a degraded catalog, not a fake control.
 */
export const FALLBACK_INTERVALS = ['1m', '5m', '15m', '1h', '1d']
export const FALLBACK_TIMEFRAMES = ['1m', '5m', '15m', '1h', '1d']

/** `chart_interval === ""` means "follow the scanner timeframe". */
export const FOLLOW = ''
