
# ============================================================
# 中文名称: OU过程均值回归策略
# 简要说明: 将价格建模为 Ornstein-Uhlenbeck 过程 dX = theta*(mu-X)*dt + sigma*dW。
#           通过滚动 OLS 回归 dX ~ X 估计 theta（回归速度）、mu（长期均值）、
#           sigma（波动率）。theta>0 时产生与 (mu-X)/sigma 成正比的信号；
#           theta<=0 则信号归零。信号值 ∈ [-1, 1]，NaN 安全，无前瞻偏差。
# 典型用途: 统计套利中检测并交易均值回归资产。
# ============================================================
"""Ornstein-Uhlenbeck Mean Reversion (mr_ornstein).

Models price as an OU process: dX = theta*(mu - X)*dt + sigma*dW.
Estimates theta, mu, sigma via rolling OLS of price changes on price
levels. When theta > 0 (mean-reverting), generates signal proportional
to (mu - X) / sigma, capped at [-1, 1]. When theta <= 0, signal = 0.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

__strategy_meta__ = {
    "id": "mr_ornstein",
    "nickname": "OU过程回归",
    "category": "mean_reversion",
    "description": (
        "Ornstein-Uhlenbeck mean reversion. Models price as an OU process, "
        "estimates mean-reversion speed (theta), long-run mean (mu), and "
        "volatility (sigma) via rolling OLS. Signal proportional to "
        "(mu - X) / sigma when theta > 0, zero otherwise. Capped at [-1, 1]."
    ),
    "universe": ["equity_cn", "equity_us"],
    "frequency": ["1D"],
    "columns_required": ["close"],
    "default_params": {"lookback": 60, "min_halflife": 5, "max_halflife": 120},
    "risk_profile": "medium",
    "min_bars": 65,
    "reference": "Ornstein-Uhlenbeck mean-reversion model",
    "factors_used": [],
}


class SignalEngine:
    """OU过程均值回归信号引擎。"""

    def __init__(self, **params: Any) -> None:
        self.lookback: int = int(params.get("lookback", 60))
        self.min_halflife: float = float(params.get("min_halflife", 5))
        self.max_halflife: float = float(params.get("max_halflife", 120))
        if self.lookback < 2:
            raise ValueError(f"lookback ({self.lookback}) must be >= 2")
        if self.min_halflife <= 0:
            raise ValueError(f"min_halflife ({self.min_halflife}) must be > 0")
        if self.max_halflife <= self.min_halflife:
            raise ValueError(
                f"max_halflife ({self.max_halflife}) must be > "
                f"min_halflife ({self.min_halflife})"
            )

    def generate(
        self, data_map: Dict[str, pd.DataFrame]
    ) -> Dict[str, pd.Series]:
        """为每个标的生成OU过程均值回归信号。

        Returns:
            Dict mapping ticker -> pd.Series of signals in [-1.0, 1.0].
        """
        signals: Dict[str, pd.Series] = {}

        for code, df in data_map.items():
            if "close" not in df.columns:
                continue
            signals[code] = self._generate_one(df)

        return signals

    def _generate_one(self, df: pd.DataFrame) -> pd.Series:
        """对单个标的生成OU过程均值回归信号。

        OU process: dX = theta * (mu - X) * dt + sigma * dW
        Discrete approximation: X_t - X_{t-1} = a + b * X_{t-1} + e_t
        where b = -theta * dt, a = theta * mu * dt.
        So theta = -b, mu = -a / b = a / theta.

        Rolling OLS: regress dX on X_{t-1} to get (a, b).
        """
        close: pd.Series = df["close"].astype(float)

        # dX = X_t - X_{t-1}
        dx = close.diff()

        # X_{t-1} (lagged close)
        x_lag = close.shift(1)

        # Rolling OLS: dX_t = a + b * X_{t-1}
        # Using rolling covariance formulas:
        #   b = cov(dX, X_lag) / var(X_lag)
        #   a = mean(dX) - b * mean(X_lag)
        window = self.lookback

        roll_mean_dx = dx.rolling(window=window, min_periods=window).mean()
        roll_mean_x = x_lag.rolling(window=window, min_periods=window).mean()
        roll_cov = dx.rolling(window=window, min_periods=window).cov(x_lag)
        roll_var_x = x_lag.rolling(window=window, min_periods=window).var()

        # Guard against zero variance
        safe_var = roll_var_x.where(roll_var_x > 0, other=np.nan)

        b = roll_cov / safe_var
        a = roll_mean_dx - b * roll_mean_x

        # OU parameters: theta = -b (mean-reversion speed), mu = a / theta
        theta = -b

        # mu is only meaningful when theta > 0
        safe_theta = theta.where(theta > 0, other=np.nan)
        mu = a / safe_theta

        # Half-life = ln(2) / theta
        halflife = np.log(2) / safe_theta

        # Residual volatility: sigma = std(dX - a - b * X_lag) over window
        fitted = a + b * x_lag
        residual = dx - fitted
        sigma = residual.rolling(window=window, min_periods=window).std()
        safe_sigma = sigma.where(sigma > 0, other=np.nan)

        # Signal: proportional to (mu - X) / sigma when conditions are met
        deviation = (mu - close) / safe_sigma

        # Build signal array
        n = len(close)
        signal_arr = np.full(n, np.nan)

        theta_arr = theta.values
        halflife_arr = halflife.values
        dev_arr = deviation.values

        for i in range(n):
            t = theta_arr[i]
            hl = halflife_arr[i]
            d = dev_arr[i]

            # Skip if any input is NaN
            if np.isnan(t) or np.isnan(hl) or np.isnan(d):
                continue

            # theta <= 0 means not mean-reverting: signal = 0
            if t <= 0:
                signal_arr[i] = 0.0
                continue

            # Half-life outside acceptable range: signal = 0
            if hl < self.min_halflife or hl > self.max_halflife:
                signal_arr[i] = 0.0
                continue

            # Signal proportional to deviation, capped at [-1, 1]
            # Normalize so that ~2 sigma deviation gives full signal
            signal_arr[i] = np.clip(d / 2.0, -1.0, 1.0)

        signal = pd.Series(signal_arr, index=close.index)
        signal = signal.clip(lower=-1.0, upper=1.0)

        return signal
