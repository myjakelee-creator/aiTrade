import csv
from pathlib import Path

from stockboard_engine import _stock_code


DOCS_DIR = Path(__file__).resolve().parent / "docs"


def _load_tradable_stock_codes():
    master_path = DOCS_DIR / "tradable_stock_master.csv"
    with master_path.open("r", encoding="utf-8-sig", newline="") as master_file:
        reader = csv.reader(master_file)
        next(reader, None)
        return {
            code
            for row in reader
            if row and (code := _stock_code(row[0]))
        }
