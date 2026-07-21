from __future__ import annotations

import math
from typing import Mapping

import numpy as np
import xarray as xr


def logistic(x: xr.DataArray | np.ndarray | float):
    return 1.0 / (1.0 + np.exp(-x))


def apply_refined_model(fields: Mapping[str, xr.DataArray], model_config: dict) -> tuple[xr.DataArray, xr.DataArray]:
    """Apply the standardized logistic model to gridded environmental fields.

    Returns
    -------
    probability, index
        Probability is 0-1. Index is integer-like 0-10.
    """
    missing = [name for name in model_config["variables"] if name not in fields]
    if missing:
        raise ValueError(
            "Missing fields required by refined model: " + ", ".join(missing)
        )

    first = next(iter(fields.values()))
    logit = xr.zeros_like(first, dtype=float) + float(model_config["intercept"])

    for name, meta in model_config["variables"].items():
        arr = fields[name].astype(float)
        mean = float(meta["mean"])
        std = float(meta["std"])
        coef = float(meta["coefficient"])
        if math.isclose(std, 0.0):
            raise ValueError(f"Model variable {name} has zero standard deviation")
        z = (arr - mean) / std
        logit = logit + coef * z

    probability = logistic(logit).clip(0, 1)
    index = xr.apply_ufunc(np.rint, probability * 10.0).clip(0, 10)
    index.name = "experimental_index_0_10"
    probability.name = "damaging_wind_probability"
    return probability, index
