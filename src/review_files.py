"""Parse bounded review spreadsheets before any embeddings are requested."""

import csv
import hashlib
import re
import io
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from xml.etree.ElementTree import ParseError

COLUMNS = {"Feedback_ID", "Franchise", "Location", "Rating", "Comment"}
MAX_ROWS = 500
MAX_EXPANDED_BYTES = 20 * 1024 * 1024


def parse_review_file(filename, content, mapping=None, inspect=False):
    """Read UTF-8 CSV or the first XLSX worksheet; reject invalid headers/formulas."""
    extension = Path(filename or "").suffix.lower()
    if extension == ".csv":
        table = csv.reader(io.StringIO(content.decode("utf-8-sig")), strict=True)
        return read_table(table, mapping, inspect)
    if extension != ".xlsx":
        raise ValueError("Choose a .csv or .xlsx file")
    from openpyxl import load_workbook
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > MAX_EXPANDED_BYTES:
                raise ValueError("Expanded Excel workbook must be smaller than 20 MiB")
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
        try:
            if not workbook.worksheets:
                raise ValueError("Excel workbook must contain a worksheet")
            sheet = workbook.worksheets[0]
            # Ignore inaccurate dimensions supplied by third-party XLSX writers.
            sheet.reset_dimensions()
            def values():
                for cells in sheet.iter_rows(max_row=MAX_ROWS + 2):
                    if any(cell.data_type == "f" for cell in cells):
                        raise ValueError("Excel formulas are not supported; submit cell values instead")
                    row = [cell.value for cell in cells]
                    while row and row[-1] is None:
                        row.pop()
                    yield row
            return read_table(values(), mapping, inspect)
        finally:
            workbook.close()
    except (BadZipFile, KeyError, ParseError, OSError) as exc:
        raise ValueError("Invalid .xlsx workbook") from exc


def normalize_feedback_id(value):
    """Preserve schema IDs; hash nonempty external IDs without lossy truncation."""
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        text = str(value).strip()
        if not text:
            return value
        if len(text) <= 64 and re.fullmatch(r"FB-[A-Za-z0-9-]+", text):
            return text
        return "FB-EXT-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:56]
    return value


def read_table(table, mapping=None, inspect=False):
    """Read unique named columns, optionally mapping schema fields to source names."""
    header = next(table, None)
    if (not header or any(not isinstance(h, str) or not h.strip() for h in header)
            or len(set(header)) != len(header)):
        raise ValueError("Column headers must be nonempty, unique text values")
    if not inspect:
        if mapping is None:
            if len(header) != len(COLUMNS) or set(header) != COLUMNS:
                raise ValueError("Map file columns to Feedback_ID, Franchise, Location, Rating, Comment")
            mapping = {name: name for name in COLUMNS}
        if (not isinstance(mapping, dict) or set(mapping) != COLUMNS
                or any(not isinstance(v, str) or v not in header for v in mapping.values())
                or len(set(mapping.values())) != len(COLUMNS)):
            raise ValueError("Map every review field to a different existing file column")
    rows = []
    for line, values in enumerate(table, start=2):
        if line > MAX_ROWS + 1:
            raise ValueError("Worksheet must contain at most 500 review rows")
        if not values or all(value is None or value == "" for value in values):
            continue
        if len(values) > len(header):
            raise ValueError(f"Row {line} contains more values than column headers")
        # Excel omits empty trailing cells; validation rejects missing required values.
        source = dict(zip(header, list(values) + [None] * (len(header) - len(values))))
        if inspect:
            rows.append(source)
        else:
            row = {target: source[column] for target, column in mapping.items()}
            row["Feedback_ID"] = normalize_feedback_id(row["Feedback_ID"])
            rows.append(row)
    if inspect:
        return {"columns": header, "sample": rows[:3], "row_count": len(rows),
                "requires_mapping": set(header) != COLUMNS or len(header) != len(COLUMNS)}
    return rows
