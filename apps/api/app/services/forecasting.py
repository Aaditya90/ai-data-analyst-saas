"""
Time-series forecasting: pure numpy/pandas/sklearn code, no AI calls
anywhere in this file. Given a date column and a numeric value column,
this:

  1. Parses and resamples the series to a regular frequency (daily/weekly/
     monthly), inferred from the median gap between timestamps unless the
     caller pins one.
  2. Fits a linear trend + (when there's enough history) seasonal dummy
     variables via ordinary least squares — deliberately not a heavier
     model like Prophet/ARIMA: a transparent OLS fit is easy to
     sanity-check, has no extra system dependencies, and this environment
     doesn't ship statsmodels.
  3. Backtests by holding out the tail of the series and scoring against
     it, then refits on the FULL series and projects `horizon` periods
     forward, with prediction intervals derived from the fitted residual
     standard deviation, widening with the square root of the step count
     (the standard random-walk-style growth-of-uncertainty assumption).

ai_narration.py's narrate_forecast() is the only place this output touches
Claude, and it's handed these exact numbers to phrase in plain language —
never to compute or adjust.
"""

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

MIN_PERIODS = 8
# 80% / 95% two-sided normal z-scores, used to build prediction intervals
# from the residual standard deviation.
Z_80 = 1.2816
Z_95 = 1.9600

_FREQ_RULE = {"daily": "D", "weekly": "W", "monthly": "MS"}
# Approximate season length per frequency: day-of-week, week-of-year,
# month-of-year. Seasonality is only modeled once there's at least two full
# cycles of history (see `use_seasonality` below).
_SEASON_LENGTH = {"daily": 7, "weekly": 52, "monthly": 12}


class ForecastingError(ValueError):
    """Raised for input that can't be forecast — the API layer surfaces
    this as a 400, it never means "the code broke"."""


def infer_frequency(dates: pd.Series) -> str:
    sorted_dates = dates.sort_values().drop_duplicates()
    if len(sorted_dates) < 2:
        raise ForecastingError("Not enough distinct dates to infer a frequency")
    median_gap_days = sorted_dates.diff().dropna().dt.days.median()
    if median_gap_days <= 3:
        return "daily"
    if median_gap_days <= 10:
        return "weekly"
    return "monthly"


def _resample(df: pd.DataFrame, date_column: str, value_column: str, frequency: str) -> pd.Series:
    rule = _FREQ_RULE[frequency]
    ordered = df[[date_column, value_column]].sort_values(date_column)
    return ordered.set_index(date_column)[value_column].resample(rule).sum()


def _season_codes(period_index: pd.DatetimeIndex, frequency: str) -> np.ndarray:
    if frequency == "daily":
        return period_index.dayofweek.to_numpy()
    if frequency == "weekly":
        return (period_index.isocalendar().week.to_numpy().astype(int)) % 52
    return period_index.month.to_numpy() - 1  # monthly -> 0-11


def _design_matrix(t_start: int, n: int, season_codes: np.ndarray | None, season_length: int | None) -> np.ndarray:
    t = np.arange(t_start, t_start + n, dtype=float).reshape(-1, 1)
    if season_codes is None or season_length is None or season_length < 2:
        return t
    dummies = np.zeros((n, season_length - 1))
    for i, code in enumerate(season_codes):
        if code < season_length - 1:
            dummies[i, code] = 1.0
    return np.hstack([t, dummies])


def _fit_predict(y: np.ndarray, X: np.ndarray, X_future: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    model = LinearRegression()
    model.fit(X, y)
    return model.predict(X), model.predict(X_future)


def _mape(y_true: np.ndarray, y_pred: np.ndarray) -> float | None:
    nonzero = y_true != 0
    if not nonzero.any():
        return None
    return float(np.mean(np.abs((y_true[nonzero] - y_pred[nonzero]) / y_true[nonzero])) * 100)


def run_forecast(
    df: pd.DataFrame,
    date_column: str,
    value_column: str,
    horizon: int,
    frequency: str | None = None,
) -> dict:
    """
    Returns:
        {
          "frequency": "daily"|"weekly"|"monthly",
          "method": "linear_trend_seasonal" | "linear_trend",
          "history": [{"period": iso_date, "actual": float}, ...],
          "forecast": [{"period": iso_date, "forecast": float, "lower_80":..,
                        "upper_80":.., "lower_95":.., "upper_95":..}, ...],
          "metrics": {"mae":.., "rmse":.., "mape": float|None,
                      "holdout_periods": int} | None,
        }
    Raises ForecastingError for input that can't be forecast.
    """
    if date_column not in df.columns:
        raise ForecastingError(f"Date column '{date_column}' not found in dataset")
    if value_column not in df.columns:
        raise ForecastingError(f"Value column '{value_column}' not found in dataset")
    if horizon < 1 or horizon > 365:
        raise ForecastingError("horizon must be between 1 and 365 periods")

    work = df[[date_column, value_column]].copy()
    work[date_column] = pd.to_datetime(work[date_column], errors="coerce")
    work[value_column] = pd.to_numeric(work[value_column], errors="coerce")
    work = work.dropna(subset=[date_column, value_column])
    if len(work) < MIN_PERIODS:
        raise ForecastingError(
            f"Need at least {MIN_PERIODS} valid (date, value) rows to forecast; found {len(work)}"
        )

    resolved_frequency = frequency or infer_frequency(work[date_column])
    if resolved_frequency not in _FREQ_RULE:
        raise ForecastingError(f"Unsupported frequency '{resolved_frequency}'")

    series = _resample(work, date_column, value_column, resolved_frequency).dropna()
    if len(series) < MIN_PERIODS:
        raise ForecastingError(
            f"After resampling to {resolved_frequency}, only {len(series)} periods remain "
            f"(need at least {MIN_PERIODS})"
        )

    season_length = _SEASON_LENGTH[resolved_frequency]
    use_seasonality = len(series) >= season_length * 2
    season_codes = _season_codes(series.index, resolved_frequency) if use_seasonality else None

    y = series.to_numpy(dtype=float)
    n = len(y)
    X = _design_matrix(0, n, season_codes, season_length if use_seasonality else None)

    # --- Backtest: hold out the tail, fit on the rest, score the holdout ---
    holdout_periods = min(max(3, horizon), n // 4)
    metrics = None
    if holdout_periods >= 2 and (n - holdout_periods) >= max(4, MIN_PERIODS // 2):
        train_y, test_y = y[:-holdout_periods], y[-holdout_periods:]
        train_X, test_X = X[:-holdout_periods], X[-holdout_periods:]
        _, backtest_pred = _fit_predict(train_y, train_X, test_X)
        errors = test_y - backtest_pred
        mape = _mape(test_y, backtest_pred)
        metrics = {
            "mae": round(float(np.mean(np.abs(errors))), 4),
            "rmse": round(float(np.sqrt(np.mean(errors**2))), 4),
            "mape": round(mape, 2) if mape is not None else None,
            "holdout_periods": holdout_periods,
        }

    # --- Final fit on the full series, project forward ---
    future_index = pd.date_range(series.index[-1], periods=horizon + 1, freq=_FREQ_RULE[resolved_frequency])[1:]
    future_season_codes = _season_codes(future_index, resolved_frequency) if use_seasonality else None
    X_future = _design_matrix(n, horizon, future_season_codes, season_length if use_seasonality else None)

    fitted, future_pred = _fit_predict(y, X, X_future)
    residual_std = float(np.std(y - fitted, ddof=1)) if n > 1 else 0.0

    forecast_points = []
    for step, (period, point) in enumerate(zip(future_index, future_pred), start=1):
        width = residual_std * np.sqrt(step)
        forecast_points.append(
            {
                "period": period.date().isoformat(),
                "forecast": round(float(point), 4),
                "lower_80": round(float(point - Z_80 * width), 4),
                "upper_80": round(float(point + Z_80 * width), 4),
                "lower_95": round(float(point - Z_95 * width), 4),
                "upper_95": round(float(point + Z_95 * width), 4),
            }
        )

    history_points = [
        {"period": idx.date().isoformat(), "actual": round(float(val), 4)} for idx, val in series.items()
    ]

    return {
        "frequency": resolved_frequency,
        "method": "linear_trend_seasonal" if use_seasonality else "linear_trend",
        "history": history_points,
        "forecast": forecast_points,
        "metrics": metrics,
    }
