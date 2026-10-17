/**
 * RoninTrBot — analysis library, a faithful JS port of the Python core
 * (app/strategy/indicators.py + app/strategy/registry.py).
 *
 * The port targets identical DISCRETE outputs (direction, score, eligible,
 * regime) for identical candle inputs; floating-point internals differ
 * slightly from Python's Decimal arithmetic, which the CI parity test
 * covers with exact discrete assertions and tight numeric tolerance.
 * No network here; the worker supplies candles.
 */

export const SUPPORTED_TIMEFRAMES = ["15m", "1h", "4h"];
export const SIGNAL_THRESHOLD = 70;
export const HIGH_VOL_ATR_FRACTION = 0.05;
export const DEFAULT_TREND_SPREAD = 0.005;
export const MIN_REGIME_CANDLES = 22;
export const MIN_TIMEFRAME_CANDLES = 51;
const REGIMES = ["trend", "range", "high_volatility"];

function num(v, field) {
  // Match Python Decimal(str(value)): reject missing/boolean/blank values
  // instead of silently coercing them to 0 or 1 via JavaScript Number().
  if (v === null || v === undefined || typeof v === "boolean" ||
      (typeof v === "string" && v.trim() === "")) {
    throw new TypeError(`${field} must be a finite number`);
  }
  const n = Number(v);
  if (!Number.isFinite(n)) throw new TypeError(`${field} must be a finite number`);
  return n;
}

/** Validate + normalize candles; mirrors validate_candles (fail-closed). */
export function validateCandles(candles, minCount = 1) {
  const data = candles.map((c) => ({
    timestamp: num(c.timestamp ?? c.ts, "timestamp"),
    open: num(c.open, "open"),
    high: num(c.high, "high"),
    low: num(c.low, "low"),
    close: num(c.close, "close"),
    volume: num(c.volume, "volume"),
  }));
  if (data.length < minCount) throw new RangeError(`not enough candles: at least ${minCount} are required`);
  let previous = -1;
  for (const c of data) {
    if (!Number.isInteger(c.timestamp) || c.timestamp < 0) throw new TypeError("timestamp must be a non-negative Unix-second integer");
    for (const f of ["open", "high", "low", "close", "volume"]) {
      if (c[f] < 0) throw new RangeError(`${f} must be non-negative`);
    }
    if (c.low > Math.min(c.open, c.close) || c.high < Math.max(c.open, c.close)) throw new RangeError("OHLC values are inconsistent");
    if (c.low > c.high) throw new RangeError("low must not exceed high");
    if (c.timestamp <= previous) throw new RangeError("candle timestamps must be strictly increasing");
    previous = c.timestamp;
  }
  return data;
}

export function sma(values, period) {
  if (period < 1 || values.length < period) throw new RangeError("not enough values for SMA period");
  let sum = 0;
  for (let i = values.length - period; i < values.length; i++) sum += values[i];
  return sum / period;
}

/** Simple-window RSI; mirrors app.strategy.indicators.rsi. */
export function rsi(closes, period = 14) {
  if (period < 1 || closes.length < period + 1) throw new RangeError("not enough closes for RSI period");
  let gains = 0, losses = 0;
  for (let i = closes.length - period; i < closes.length; i++) {
    const change = closes[i] - closes[i - 1];
    if (change > 0) gains += change; else losses += -change;
  }
  if (losses === 0) return gains === 0 ? 50 : 100;
  const averageGain = gains / period, averageLoss = losses / period;
  return 100 - 100 / (1 + averageGain / averageLoss);
}

/** Simple average true range over the latest `period` true ranges. */
export function atr(candles, period = 14) {
  if (period < 1 || candles.length < period + 1) throw new RangeError("not enough candles for ATR period");
  let sum = 0;
  for (let i = candles.length - period; i < candles.length; i++) {
    const cur = candles[i], prev = candles[i - 1];
    sum += Math.max(cur.high - cur.low, Math.abs(cur.high - prev.close), Math.abs(cur.low - prev.close));
  }
  return sum / period;
}

/** Port of analyze_timeframe(): identical rules, identical integer scores. */
export function analyzeTimeframe(timeframe, candles) {
  if (!SUPPORTED_TIMEFRAMES.includes(timeframe)) throw new RangeError(`timeframe must be one of ${SUPPORTED_TIMEFRAMES.join(", ")}`);
  const data = validateCandles(candles, MIN_TIMEFRAME_CANDLES);
  const closes = data.map((c) => c.close);
  const fast = sma(closes, 9), slow = sma(closes, 21);
  const momentum = rsi(closes, 14), volatility = atr(data, 14);
  let score = 0, direction = "neutral";
  const reasons = [];

  if (fast > slow && closes[closes.length - 1] > slow) {
    direction = "long"; score += 40;
    reasons.push("close and SMA(9) are above SMA(21)");
  } else if (fast < slow && closes[closes.length - 1] < slow) {
    direction = "short"; score += 40;
    reasons.push("close and SMA(9) are below SMA(21)");
  } else {
    reasons.push("moving-average trend is mixed");
  }

  if (direction === "long" && momentum > 50 && momentum < 70) {
    score += 30; reasons.push("RSI(14) supports upward momentum without being overbought");
  } else if (direction === "short" && momentum > 30 && momentum < 50) {
    score += 30; reasons.push("RSI(14) supports downward momentum without being oversold");
  } else if (direction === "long" && momentum >= 70) {
    score += 10; reasons.push("RSI(14) is overbought; upward momentum is extended");
  } else if (direction === "short" && momentum <= 30) {
    score += 10; reasons.push("RSI(14) is oversold; downward momentum is extended");
  } else {
    reasons.push("RSI(14) does not confirm the trend");
  }

  if (volatility > 0 && closes[closes.length - 1] > 0 && volatility / closes[closes.length - 1] <= 0.05) {
    score += 20; reasons.push("ATR(14) is at or below 5% of close");
  } else {
    reasons.push("ATR(14) is zero or elevated relative to close");
  }

  return {
    timeframe,
    direction,
    score: Math.min(score, 100),
    close: closes[closes.length - 1],
    sma_fast: fast,
    sma_slow: slow,
    rsi: momentum,
    atr: volatility,
    reasons,
    eligible: Math.min(score, 100) >= SIGNAL_THRESHOLD,
  };
}

/** Port of classify_regime(): identical thresholds to app.strategy.registry. */
export function classifyRegime(candles, trendSpreadFraction = DEFAULT_TREND_SPREAD) {
  if (trendSpreadFraction <= 0) throw new RangeError("trend_spread_fraction must be greater than zero");
  const data = validateCandles(candles, MIN_REGIME_CANDLES);
  const closes = data.map((c) => c.close);
  const close = closes[closes.length - 1];
  const volatility = close > 0 ? atr(data, 14) / close : 0;
  if (volatility > HIGH_VOL_ATR_FRACTION) return "high_volatility";
  const fast = sma(closes, 9), slow = sma(closes, 21);
  const spread = close > 0 ? Math.abs(fast - slow) / close : 0;
  if (spread >= trendSpreadFraction && ((fast > slow && close > slow) || (fast < slow && close < slow))) {
    return "trend";
  }
  return "range";
}

export function regimesList() {
  return [...REGIMES];
}

/* ---------- Node CLI harness (used ONLY by the CI parity test) ---------- */
const isMain = typeof process !== "undefined" && process.argv?.[1] &&
  import.meta.url === `file://${process.argv[1]}`;
if (isMain && process.argv.includes("--selftest")) {
  let raw = "";
  process.stdin.on("data", (d) => (raw += d)).on("end", () => {
    const input = JSON.parse(raw);
    const analysis = analyzeTimeframe(input.timeframe || "1h", input.candles);
    const regime = classifyRegime(input.candles);
    process.stdout.write(JSON.stringify({
      score: analysis.score,
      direction: analysis.direction,
      eligible: analysis.eligible,
      regime,
      sma_fast: analysis.sma_fast,
      sma_slow: analysis.sma_slow,
      rsi: analysis.rsi,
      atr: analysis.atr,
    }));
  });
}
