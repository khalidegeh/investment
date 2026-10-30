"""Chunked CSV and Parquet export."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
from app.export import (
    export_filename,
    sanitize_cell,
    stream_csv,
    write_csv_file,
    write_parquet_file,
)

NOW = datetime(2026, 10, 30, 14, 5, 9, tzinfo=timezone.utc)


def frame(n: int = 12) -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.date_range("2024-01-01", periods=n), name="date")
    return pd.DataFrame({"close": [float(i) for i in range(n)], "note": ["x"] * n}, index=idx)


class ExportTests(unittest.TestCase):
    def test_filename_is_timestamped_and_safe(self):
        self.assertEqual(export_filename("aapl", "ohlcv", "csv", NOW), "AAPL_ohlcv_20261030T140509Z.csv")
        self.assertEqual(export_filename("^GSPC", "income", "parquet", NOW),
                         "_GSPC_income_20261030T140509Z.parquet")
        east = NOW.astimezone(timezone(timedelta(hours=-5)))
        self.assertEqual(export_filename("A", "ohlcv", "csv", east), "A_ohlcv_20261030T140509Z.csv")
        for args in [("../x", "ohlcv", "csv"), ("A", "nope", "csv"), ("A", "ohlcv", "exe")]:
            with self.assertRaises(ValueError):
                export_filename(*args, NOW)
        with self.assertRaises(ValueError):
            export_filename("A", "ohlcv", "csv", datetime(2026, 1, 1))  # noqa: DTZ001  (naive on purpose)

    def test_chunked_stream_equals_one_shot_csv(self):
        df = frame(12)
        for chunk in (1, 5, 12, 100):
            body = b"".join(stream_csv(df, chunk)).decode()
            self.assertEqual(body, df.to_csv(), f"chunk={chunk}")
        with self.assertRaises(ValueError):
            list(stream_csv(df, 0))

    def test_formula_cells_neutralised(self):
        self.assertEqual(sanitize_cell("=SUM(A1)"), "'=SUM(A1)")
        self.assertEqual(sanitize_cell("@cmd"), "'@cmd")
        self.assertEqual(sanitize_cell("ok"), "ok")
        self.assertEqual(sanitize_cell(5), 5)
        df = pd.DataFrame({"name": ["=1+1", "fine"]})
        self.assertIn("'=1+1", b"".join(stream_csv(df)).decode())

    def test_write_csv_atomic_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = write_csv_file(frame(30), Path(tmp), "NVDA", "ohlcv", NOW, chunk_rows=7)
            self.assertEqual(res.path.name, "NVDA_ohlcv_20261030T140509Z.csv")
            self.assertEqual(res.rows, 30)
            self.assertEqual(res.bytes, res.path.stat().st_size)
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])
            back = pd.read_csv(StringIO(res.path.read_text()), index_col="date")
            self.assertEqual(len(back), 30)

    def test_write_parquet_roundtrips_in_row_groups(self):
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as tmp:
            res = write_parquet_file(frame(25), Path(tmp), "NVDA", "ohlcv", NOW, row_group_rows=10)
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])
            f = pq.ParquetFile(res.path)
            self.assertEqual(f.metadata.num_rows, 25)
            self.assertEqual(f.metadata.num_row_groups, 3)
            back = f.read().to_pandas()
            self.assertEqual(list(back.columns), ["date", "close", "note"])
            self.assertEqual(float(back["close"].iloc[-1]), 24.0)

    def test_empty_frame_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = frame(3).iloc[0:0]
            self.assertEqual(write_csv_file(empty, Path(tmp), "A", "ohlcv", NOW).rows, 0)
            self.assertEqual(write_parquet_file(empty, Path(tmp), "A", "ohlcv", NOW).rows, 0)


if __name__ == "__main__":
    unittest.main()
