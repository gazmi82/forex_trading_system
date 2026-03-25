from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from app.backtesting.data_loader import HistoricalDataLoader


DEFAULT_GRANULARITIES = ("M1", "H1", "H4", "D", "W")


@dataclass(frozen=True)
class ExportedDataset:
    instrument: str
    granularity: str
    price_component: str
    start: str
    end: str
    path: str
    rows: int


class HistoricalDatasetExporter:
    """
    Downloads OANDA history once and stores it in stable CSV datasets that can
    be reused by the backtesting loader without re-hitting the live API.
    """

    def __init__(
        self,
        loader: HistoricalDataLoader,
        *,
        output_root: Path | None = None,
        meta_root: Path | None = None,
    ):
        self.loader = loader
        self.output_root = Path(output_root or loader.cache_dir / "raw")
        self.meta_root = Path(meta_root or loader.cache_dir / "meta")
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.meta_root.mkdir(parents=True, exist_ok=True)

    def export_dataset(
        self,
        instrument: str,
        granularity: str,
        *,
        start: datetime,
        end: datetime,
        force_refresh: bool = False,
    ) -> ExportedDataset:
        instrument_slug = instrument.replace("/", "_").upper()
        granularity = granularity.upper()
        start_utc = _ensure_utc(start)
        end_utc = _ensure_utc(end)

        output_path = self._dataset_path(instrument_slug, granularity, start_utc, end_utc)
        if output_path.exists() and not force_refresh:
            frame = self.loader._read_cache(output_path)
        else:
            frame = self.loader.fetch_range(
                instrument_slug,
                granularity,
                start=start_utc,
                end=end_utc,
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(output_path, index_label="time")

        exported = ExportedDataset(
            instrument=instrument_slug,
            granularity=granularity,
            price_component="M",
            start=_utc_slug(start_utc),
            end=_utc_slug(end_utc),
            path=str(output_path),
            rows=len(frame),
        )
        self._write_dataset_metadata(exported)
        return exported

    def export_bundle(
        self,
        instrument: str,
        *,
        start: datetime,
        end: datetime,
        granularities: Iterable[str] = DEFAULT_GRANULARITIES,
        force_refresh: bool = False,
        progress_callback: Callable[[ExportedDataset], None] | None = None,
    ) -> list[ExportedDataset]:
        exported: list[ExportedDataset] = []
        for granularity in granularities:
            item = self.export_dataset(
                instrument,
                granularity,
                start=start,
                end=end,
                force_refresh=force_refresh,
            )
            exported.append(item)
            if progress_callback is not None:
                progress_callback(item)
        self._write_manifest(instrument, exported)
        return exported

    def _dataset_path(
        self,
        instrument_slug: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> Path:
        dataset_dir = self.output_root / instrument_slug
        filename = (
            f"{instrument_slug}_{granularity}_M_{_utc_slug(start)}_{_utc_slug(end)}.csv"
        )
        return dataset_dir / filename

    def _write_dataset_metadata(self, exported: ExportedDataset):
        path = Path(exported.path)
        meta_dir = self.meta_root / exported.instrument
        meta_dir.mkdir(parents=True, exist_ok=True)
        meta_path = meta_dir / f"{path.stem}.json"
        payload = asdict(exported)
        payload["generated_at_utc"] = _utc_slug(datetime.now(timezone.utc))
        payload["alignment_timezone"] = "America/New_York"
        payload["daily_alignment_hour"] = 17
        payload["weekly_alignment"] = "Friday"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def _write_manifest(self, instrument: str, exported: list[ExportedDataset]):
        instrument_slug = instrument.replace("/", "_").upper()
        meta_dir = self.meta_root / instrument_slug
        meta_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = meta_dir / f"{instrument_slug}_history_manifest.json"
        payload = {
            "instrument": instrument_slug,
            "generated_at_utc": _utc_slug(datetime.now(timezone.utc)),
            "datasets": [asdict(item) for item in exported],
        }
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_slug(value: datetime) -> str:
    return _ensure_utc(value).strftime("%Y%m%dT%H%M%SZ")
