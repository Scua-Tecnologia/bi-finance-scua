from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd


class EnhancedJSONEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if is_dataclass(obj):
            return asdict(obj)
        if isinstance(obj, (date, pd.Timestamp)):
            return obj.isoformat()
        if isinstance(obj, Path):
            return str(obj)
        return super().default(obj)


def dump_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, cls=EnhancedJSONEncoder),
        encoding="utf-8",
    )


def safe_to_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=False)


def coalesce_columns(df: pd.DataFrame, candidates: list[str], target: str) -> pd.DataFrame:
    values = None
    for col in candidates:
        if col in df.columns:
            values = df[col] if values is None else values.fillna(df[col])
    if values is not None:
        df[target] = values
    return df


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_dataframe(df: pd.DataFrame, base_path: Path, name: str) -> dict[str, str]:
    ensure_directory(base_path)
    parquet_path = base_path / f"{name}.parquet"
    df.to_parquet(parquet_path, index=False)
    return {"parquet": str(parquet_path)}