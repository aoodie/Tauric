"""Isolated Qlib/LightGBM process. Accepts frozen numeric features, never credentials."""

import hashlib
import importlib.metadata
import json
import os
import sys
from pathlib import Path


def run(folder):
    import numpy as np
    import pandas as pd
    import qlib
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandlerLP
    from qlib.data.dataset.loader import StaticDataLoader

    job = json.loads((folder / "input.json").read_text())
    features = job["features"]
    rows = [r for part in features["partitions"].values() for r in part]
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp(r["time"]).tz_localize(None), "ASSET") for r in rows],
        names=["datetime", "instrument"],
    )
    columns = pd.MultiIndex.from_tuples(
        [("feature", name) for name in features["features"]] + [("label", "return")]
    )
    frame = pd.DataFrame(
        [r["features"] + [r["label"]] for r in rows], index=index, columns=columns
    ).sort_index()
    segments = {
        ("valid" if name == "validation" else name): (
            pd.Timestamp(part[0]["time"]).tz_localize(None),
            pd.Timestamp(part[-1]["time"]).tz_localize(None),
        )
        for name, part in features["partitions"].items()
    }
    # No stock universe, provider expressions, default Alpha158 labels or cross-sectional normalization.
    qlib.init(
        provider_uri=str(folder / "empty-provider"),
        expression_cache=None,
        dataset_cache=None,
        exp_manager={
            "class": "MLflowExpManager",
            "module_path": "qlib.workflow.expm",
            "kwargs": {
                "uri": "sqlite:///" + str(folder / "mlflow.db"),
                "default_exp_name": "tauric",
            },
        },
    )
    handler = DataHandlerLP(
        data_loader=StaticDataLoader(frame), infer_processors=[], learn_processors=[]
    )
    dataset = DatasetH(handler=handler, segments=segments)
    model = LGBModel(
        loss="mse",
        num_leaves=7,
        max_depth=3,
        learning_rate=0.03,
        min_data_in_leaf=10,
        num_threads=2,
        seed=42,
        feature_fraction=1.0,
        bagging_fraction=1.0,
        verbosity=-1,
        deterministic=True,
        force_col_wise=True,
    )
    model.fit(dataset, num_boost_round=120, early_stopping_rounds=15, verbose_eval=False)
    train_mean = float(np.mean([r["label"] for r in features["partitions"]["train"]]))
    metrics, predictions = {}, []
    for name in ("validation", "test"):
        scores = model.predict(dataset, segment="valid" if name == "validation" else name)
        part = features["partitions"][name]
        actual = np.array([r["label"] for r in part])
        values = scores.to_numpy(dtype=float)
        if len(values) != len(part) or not np.isfinite(values).all():
            raise ValueError("Model output must match the frozen finite evaluation rows")
        correlation = (
            float(np.corrcoef(actual, values)[0, 1])
            if np.std(values) > 0 and np.std(actual) > 0
            else None
        )
        metrics[name] = {
            "samples": len(part),
            "mse": float(np.mean((actual - values) ** 2)),
            "baseline_mse": float(np.mean((actual - train_mean) ** 2)),
            "direction_accuracy": float(np.mean((actual > 0) == (values > 0))),
            "return_correlation": correlation,
        }
        predictions.extend(
            {"time": r["time"], "index": r["index"], "prediction": float(v), "partition": name}
            for r, v in zip(part, values, strict=True)
        )
    model.model.save_model(str(folder / "model.txt"))
    digest = hashlib.sha256((folder / "model.txt").read_bytes()).hexdigest()
    latest = features["forecast"]
    forecast = float(model.model.predict(np.asarray([latest["features"]]))[0])
    result = {
        "engine": "qlib",
        "model": "LightGBM regression",
        "seed": 42,
        "versions": {
            p: importlib.metadata.version(p) for p in ("pyqlib", "lightgbm", "numpy", "pandas")
        },
        "model_sha256": digest,
        "metrics": metrics,
        "predictions": predictions,
        "forecast": {
            "as_of": latest["time"],
            "estimated_return": forecast,
            "horizon_bars": features["horizon_bars"],
            "probability": None,
        },
        "training_samples": len(features["partitions"]["train"]),
        "best_iteration": model.model.best_iteration,
        "warnings": [
            "Single-instrument research model; compare with the training-mean baseline.",
            "Predicted returns are estimates, not probabilities or trading approval.",
            "Targets include historical spread but exclude commission, slippage and financing.",
            "Provider revisions and corporate-action adjustments may contain hindsight.",
        ],
        "authority": "research_only",
    }
    # Reject non-JSON numeric outputs before anything is accepted by Tauric.
    (folder / "result.json").write_text(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    os.environ["MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING"] = "false"
    os.environ["MLFLOW_DISABLE_AGENT_HINT"] = "1"
    os.environ["MPLCONFIGDIR"] = str(Path(sys.argv[1]).resolve() / "matplotlib")
    try:
        run(Path(sys.argv[1]).resolve())
    except Exception as error:
        print(f"Qlib failed: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
