import csv
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

DATA_DIR = Path(__file__).resolve().parent / "data" / "execution_charts"
RAW_DIR = DATA_DIR / "raw"
PARSED_DIR = DATA_DIR / "parsed"
INDEX_FILE = DATA_DIR / "index.json"

CHART_COLUMNS = [
    "time", "time_full", "session", "open", "high", "low", "close",
    "vwap", "regular_open_line", "trade_value_eok", "trade_price",
    "strength", "strength_raw", "buy_volume", "sell_volume",
    "total_execution_volume", "has_execution",
]

_CELL_RE = re.compile(r"([A-Z]+)(\d+)")
_CODE_RE = re.compile(r"(\d{6})")


def _ensure_dirs():
    for path in (DATA_DIR, RAW_DIR, PARSED_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _safe_code(value):
    text = str(value or "").strip()
    match = _CODE_RE.search(text)
    return match.group(1) if match else text


def _date_key(value):
    text = str(value or "").strip()
    digits = re.sub(r"\D", "", text)
    return digits[:8] if len(digits) >= 8 else digits


def _date_label(date_key):
    if len(date_key) == 8:
        return f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"
    return date_key


def _time_key(value):
    if value is None:
        return ""
    if isinstance(value, (int, float)) and value < 1:
        total_seconds = int(round(value * 86400))
        return f"{total_seconds // 3600:02d}:{(total_seconds % 3600) // 60:02d}"
    text = str(value).strip()
    if not text:
        return ""
    match = re.search(r"(\d{1,2}):(\d{2})", text)
    if match:
        return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 4:
        hour = int(digits[-6:-4] or digits[:2])
        minute = int(digits[-4:-2] if len(digits) >= 6 else digits[2:4])
        return f"{hour:02d}:{minute:02d}"
    return text


def _full_time(time_key):
    return f"{time_key}:00" if re.match(r"^\d{2}:\d{2}$", time_key) else time_key


def _session_for_time(time_key):
    try:
        hour, minute = [int(part) for part in time_key.split(":")[:2]]
    except Exception:
        return "unknown"
    minutes = hour * 60 + minute
    if minutes < 9 * 60:
        return "premarket"
    if minutes < 15 * 60 + 30:
        return "regular"
    if minutes < 15 * 60 + 40:
        return "closing_call"
    if minutes < 20 * 60:
        return "aftermarket"
    return "closed"


def _number(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if value == value else None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--"}:
        return None
    text = text.replace("+", "")
    try:
        return float(text)
    except ValueError:
        return None


def _int_number(value):
    number = _number(value)
    return int(number) if number is not None else None


def _excel_col_index(col):
    total = 0
    for char in col:
        total = total * 26 + ord(char) - ord("A") + 1
    return total - 1


def _cell_value(cell, shared_strings):
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        text_node = cell.find(".//{*}t")
        return text_node.text if text_node is not None else ""
    value_node = cell.find("{*}v")
    if value_node is None:
        return ""
    raw = value_node.text or ""
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except (ValueError, IndexError):
            return raw
    if cell_type in {"str", "b"}:
        return raw
    try:
        number = float(raw)
        return int(number) if number.is_integer() else number
    except ValueError:
        return raw


def _read_shared_strings(zf):
    try:
        data = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(data)
    values = []
    for si in root.findall("{*}si"):
        parts = [node.text or "" for node in si.findall(".//{*}t")]
        values.append("".join(parts))
    return values


def _first_sheet_path(zf):
    try:
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        first_sheet = workbook.find(".//{*}sheet")
        rel_id = first_sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") if first_sheet is not None else None
        rel_targets = {rel.attrib.get("Id"): rel.attrib.get("Target") for rel in rels.findall("{*}Relationship")}
        target = rel_targets.get(rel_id)
        if target:
            return "xl/" + target.lstrip("/")
    except Exception:
        pass
    names = sorted(name for name in zf.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
    if not names:
        raise ValueError("xlsx worksheet not found")
    return names[0]


def _read_xlsx_grid(data):
    with zipfile.ZipFile(data) as zf:
        shared_strings = _read_shared_strings(zf)
        sheet_xml = zf.read(_first_sheet_path(zf))
    root = ET.fromstring(sheet_xml)
    grid = []
    for row_node in root.findall(".//{*}sheetData/{*}row"):
        row_values = []
        for cell in row_node.findall("{*}c"):
            ref = cell.attrib.get("r", "")
            match = _CELL_RE.match(ref)
            col_idx = _excel_col_index(match.group(1)) if match else len(row_values)
            while len(row_values) <= col_idx:
                row_values.append("")
            row_values[col_idx] = _cell_value(cell, shared_strings)
        if any(value not in ("", None) for value in row_values):
            grid.append(row_values)
    return grid


def _read_csv_grid(data):
    for encoding in ("utf-8-sig", "cp949", "utf-8"):
        try:
            text = data.getvalue().decode(encoding)
            return list(csv.reader(text.splitlines()))
        except UnicodeDecodeError:
            continue
    raise ValueError("csv encoding not supported")


def read_table(filename, data):
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".csv":
        grid = _read_csv_grid(data)
    elif suffix in {".xlsx", ".xlsm", ".xls"}:
        try:
            grid = _read_xlsx_grid(data)
        except zipfile.BadZipFile as error:
            raise ValueError("키움 xls는 xlsx로 다시 저장한 뒤 업로드해 주세요") from error
    else:
        raise ValueError("xlsx/csv file required")
    if not grid:
        raise ValueError("empty file")
    return grid


def _header_index(grid):
    best_row_index = 0
    best_score = -1
    for index, row in enumerate(grid[:20]):
        text = "|".join(str(value) for value in row)
        score = sum(keyword in text for keyword in ("시간", "시가", "고가", "저가", "종가", "체결가", "체결강도", "VWAP"))
        if score > best_score:
            best_score = score
            best_row_index = index
    header = [str(value).strip() for value in grid[best_row_index]]
    return best_row_index, header


def _row_dicts(grid):
    header_row, header = _header_index(grid)
    rows = []
    for row in grid[header_row + 1:]:
        item = {header[idx]: row[idx] if idx < len(row) else "" for idx in range(len(header)) if header[idx]}
        if any(value not in ("", None) for value in item.values()):
            rows.append(item)
    return rows


def _first_key(row, keys):
    for key in keys:
        if key in row and row[key] not in ("", None):
            return row[key]
    return None


def _first_key_contains(row, patterns):
    lowered = [pattern.lower() for pattern in patterns]
    for key, value in row.items():
        if value in ("", None):
            continue
        text = str(key).lower()
        if any(pattern in text for pattern in lowered):
            return value
    return None


def _value_at(row, index):
    values = list(row.values())
    return values[index] if index < len(values) else None


def _detect_table_type(rows):
    keys = set().union(*(row.keys() for row in rows[:5])) if rows else set()
    if "체결가" in keys or "체결강도" in keys or "매수체결" in keys:
        return "execution"
    if {"시가", "고가", "저가", "종가"} & keys or any("VWAP" in key.upper() for key in keys):
        return "ohlc"
    if rows:
        first_values = list(rows[0].values())
        numeric_count = sum(_number(value) is not None for value in first_values[2:6])
        if len(first_values) >= 6 and numeric_count >= 4:
            return "ohlc"
    return "unknown"


def parse_uploaded_files(files):
    parsed = {"ohlc": [], "execution": []}
    raw_meta = []
    for fileinfo in files:
        filename = fileinfo.get("filename") or "upload.xlsx"
        grid = read_table(filename, fileinfo["data"])
        rows = _row_dicts(grid)
        table_type = _detect_table_type(rows)
        if table_type in parsed:
            parsed[table_type].extend(rows)
        raw_meta.append({"filename": filename, "type": table_type, "rows": len(rows)})
    return parsed, raw_meta


def _extract_ohlc(rows, target_date):
    points = {}
    for row in rows:
        date = _date_key(_first_key(row, ["날짜", "일자", "date"]) or _value_at(row, 0) or target_date)
        if target_date and date and date != target_date:
            continue
        time_key = _time_key(_first_key(row, ["시간", "체결시간", "time"]) or _value_at(row, 1))
        if not time_key:
            continue
        point = {
            "time": time_key,
            "time_full": _full_time(time_key),
            "session": _session_for_time(time_key),
            "open": _int_number(_first_key(row, ["시가", "open"]) or _value_at(row, 2)),
            "high": _int_number(_first_key(row, ["고가", "high"]) or _value_at(row, 3)),
            "low": _int_number(_first_key(row, ["저가", "low"]) or _value_at(row, 4)),
            "close": _int_number(_first_key(row, ["종가", "close"]) or _value_at(row, 5)),
            "vwap": _number(_first_key(row, ["VWAP_09리셋", "VWAP", "vwap"]) or _first_key_contains(row, ["VWAP"]) or _value_at(row, 8)),
            "regular_open_line": _int_number(_first_key(row, ["09시 시가선", "시가선"]) or _value_at(row, 9)),
            "trade_value_eok": _number(_first_key(row, ["분봉 거래대금(억)", "거래대금(억)", "trade_value_eok"]) or _value_at(row, 10)),
        }
        points[time_key] = point
    return points


def _extract_execution(rows, target_date):
    points = {}
    for row in rows:
        date = _date_key(_first_key(row, ["날짜", "일자", "date"]) or target_date)
        if target_date and date and date != target_date:
            continue
        time_key = _time_key(_first_key(row, ["시간", "체결시간", "time"]))
        if not time_key:
            continue
        strength_raw = _number(_first_key(row, ["체결강도", "strength", "execution_strength"]))
        strength = strength_raw * 100 if strength_raw is not None and 0 < abs(strength_raw) <= 10 else strength_raw
        buy = _int_number(_first_key(row, ["매수체결", "buy_volume", "매수"]))
        sell = _int_number(_first_key(row, ["매도체결", "sell_volume", "매도"]))
        trade_price = _int_number(_first_key(row, ["체결가", "trade_price", "price"]))
        total = (buy or 0) + (sell or 0) if buy is not None or sell is not None else None
        points[time_key] = {
            "trade_price": trade_price,
            "strength": strength,
            "strength_raw": strength_raw,
            "buy_volume": buy,
            "sell_volume": sell,
            "total_execution_volume": total,
            "has_execution": any(value is not None for value in (trade_price, strength, buy, sell)),
        }
    return points


def _time_sort_key(time_key):
    try:
        hour, minute = [int(part) for part in time_key.split(":")[:2]]
        return hour * 60 + minute
    except Exception:
        return 9999


def build_chart_payload(date, stock_code, stock_name, ohlc_rows, execution_rows=None, raw_files=None):
    date = _date_key(date)
    stock_code = _safe_code(stock_code)
    stock_name = str(stock_name or "").strip() or stock_code
    ohlc_points = _extract_ohlc(ohlc_rows, date)
    execution_points = _extract_execution(execution_rows or [], date)
    times = sorted(set(ohlc_points) | set(execution_points), key=_time_sort_key)
    if not times:
        raise ValueError("no chart rows after date/time normalization")

    regular_open = None
    if "09:00" in ohlc_points:
        regular_open = ohlc_points["09:00"].get("open") or ohlc_points["09:00"].get("close")
    if regular_open is None:
        for time_key in times:
            if _time_sort_key(time_key) >= 9 * 60:
                point = ohlc_points.get(time_key) or {}
                regular_open = point.get("open") or point.get("close")
                if regular_open is not None:
                    break

    series = []
    for time_key in times:
        point = {
            "time": time_key,
            "time_full": _full_time(time_key),
            "session": _session_for_time(time_key),
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "vwap": None,
            "regular_open_line": regular_open if _time_sort_key(time_key) >= 9 * 60 else None,
            "trade_value_eok": None,
            "trade_price": None,
            "strength": None,
            "strength_raw": None,
            "buy_volume": None,
            "sell_volume": None,
            "total_execution_volume": None,
            "has_execution": False,
        }
        point.update(ohlc_points.get(time_key, {}))
        if point.get("regular_open_line") is None and _time_sort_key(time_key) >= 9 * 60:
            point["regular_open_line"] = regular_open
        point.update(execution_points.get(time_key, {}))
        series.append(point)

    prices = [value for point in series for value in (point.get("open"), point.get("high"), point.get("low"), point.get("close")) if value is not None]
    vwaps = [point.get("vwap") for point in series if point.get("vwap") is not None]
    trade_values = [point.get("trade_value_eok") for point in series if point.get("trade_value_eok") is not None]
    execution_count = sum(1 for point in series if point.get("has_execution"))
    summary = {
        "time_range": f"{times[0]}~{times[-1]}",
        "row_count": len(series),
        "chart_points": len(series),
        "execution_points": execution_count,
        "missing_execution_points": len(series) - execution_count,
        "regular_open_price": regular_open,
        "first_price": series[0].get("close") or series[0].get("trade_price"),
        "last_price": series[-1].get("close") or series[-1].get("trade_price"),
        "high": max(prices) if prices else None,
        "low": min(prices) if prices else None,
        "last_vwap": vwaps[-1] if vwaps else None,
        "total_trade_value_eok": sum(trade_values) if trade_values else None,
        "raw_files": raw_files or [],
    }
    compact_series = [[point.get(column) for column in CHART_COLUMNS] for point in series]
    return {
        "meta": {
            "date": date,
            "date_label": _date_label(date),
            "stock_code": stock_code,
            "stock_name": stock_name,
            "market": "통합장",
            "source": "local_upload_json",
            "timeframe": "1m",
        },
        "summary": summary,
        "columns": CHART_COLUMNS,
        "series": compact_series,
    }


def _load_index():
    _ensure_dirs()
    if not INDEX_FILE.exists():
        return {"version": 1, "updated_at": None, "items": []}
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "updated_at": None, "items": []}


def _save_index(index):
    _ensure_dirs()
    index["updated_at"] = datetime.now().isoformat(timespec="seconds")
    INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def save_chart(payload):
    _ensure_dirs()
    meta = payload.get("meta", {})
    date = meta.get("date")
    code = meta.get("stock_code")
    if not date or not code:
        raise ValueError("payload meta requires date and stock_code")
    parsed_dir = PARSED_DIR / date
    parsed_dir.mkdir(parents=True, exist_ok=True)
    chart_path = parsed_dir / f"{code}.json"
    chart_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    index = _load_index()
    rel_path = chart_path.relative_to(DATA_DIR).as_posix()
    item = {
        "date": date,
        "date_label": meta.get("date_label") or _date_label(date),
        "stock_code": code,
        "stock_name": meta.get("stock_name"),
        "market": meta.get("market"),
        "timeframe": meta.get("timeframe", "1m"),
        "chart_path": rel_path,
        "row_count": payload.get("summary", {}).get("row_count"),
    }
    items = [old for old in index.get("items", []) if not (old.get("date") == date and old.get("stock_code") == code)]
    items.append(item)
    items.sort(key=lambda value: (value.get("date", ""), value.get("stock_code", "")))
    index["items"] = items
    _save_index(index)
    return {"saved": True, "chart_path": rel_path, "index": index, "item": item, "payload": payload}


def get_index():
    return _load_index()


def get_chart(date, code):
    date = _date_key(date)
    code = _safe_code(code)
    path = PARSED_DIR / date / f"{code}.json"
    if not path.exists():
        raise FileNotFoundError(f"execution chart not found: {date}/{code}")
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_content_disposition(text):
    parts = text.split(";")
    result = {}
    for part in parts[1:]:
        if "=" in part:
            key, value = part.strip().split("=", 1)
            result[key.lower()] = value.strip().strip('"')
    return result


def parse_multipart(content_type, body):
    match = re.search(r"boundary=([^;]+)", content_type or "")
    if not match:
        raise ValueError("multipart boundary missing")
    boundary = match.group(1).strip().strip('"').encode("utf-8")
    delimiter = b"--" + boundary
    fields = {}
    files = []
    for part in body.split(delimiter):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2].strip(b"\r\n")
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        headers = {}
        for line in head.decode("utf-8", errors="replace").split("\r\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.lower()] = value.strip()
        disposition = _parse_content_disposition(headers.get("content-disposition", ""))
        name = disposition.get("name")
        filename = disposition.get("filename")
        if not name:
            continue
        if filename:
            import io
            files.append({"field": name, "filename": filename, "data": io.BytesIO(data)})
        else:
            fields[name] = data.decode("utf-8", errors="replace").strip()
    return fields, files


def save_upload(content_type, body):
    fields, files = parse_multipart(content_type, body)
    if not files:
        raise ValueError("upload requires at least one xlsx/csv file")
    parsed, raw_meta = parse_uploaded_files(files)
    date = fields.get("date") or ""
    if not date:
        date_candidates = [_date_key(_first_key(row, ["날짜", "일자", "date"]) or _value_at(row, 0)) for row in parsed.get("ohlc", [])[:20]]
        date = next((candidate for candidate in date_candidates if candidate), "")
    stock_code = fields.get("stock_code") or fields.get("code") or "000000"
    stock_name = fields.get("stock_name") or fields.get("name") or stock_code
    if not parsed.get("ohlc"):
        raise ValueError("OHLC xlsx/csv file is required")
    payload = build_chart_payload(date, stock_code, stock_name, parsed.get("ohlc", []), parsed.get("execution", []), raw_meta)
    return save_chart(payload)
