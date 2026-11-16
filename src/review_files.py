"""Parse bounded review spreadsheets before any embeddings are requested."""

import csv
import io
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from xml.etree.ElementTree import ParseError

COLUMNS = {"Feedback_ID", "Franchise", "Location", "Rating", "Comment"}
MAX_ROWS = 500
MAX_EXPANDED_BYTES = 20 * 1024 * 1024


def parse_review_file(filename, content):
    """Read UTF-8 CSV or the first XLSX worksheet; reject invalid headers/formulas."""
    extension = Path(filename or "").suffix.lower()
    if extension == ".csv":
        table = csv.reader(io.StringIO(content.decode("utf-8-sig")), strict=True)
        return read_table(table)
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
            return read_table(values())
        finally:
            workbook.close()
    except (BadZipFile, KeyError, ParseError, OSError) as exc:
        raise ValueError("Invalid .xlsx workbook") from exc


def read_table(table):
    """Validate the exact five columns and enforce the review batch limit."""
    header = next(table, None)
    if not header or len(header) != len(COLUMNS) or set(header) != COLUMNS:
        raise ValueError("Headers must be exactly: Feedback_ID, Franchise, Location, Rating, Comment")
    rows = []
    for line, values in enumerate(table, start=2):
        if line > MAX_ROWS + 1:
            raise ValueError("Worksheet must contain at most 500 review rows")
        if not values or all(value is None or value == "" for value in values):
            continue
        if len(values) != len(header):
            raise ValueError(f"Row {line} must contain exactly five columns")
        rows.append(dict(zip(header, values)))
        if len(rows) > MAX_ROWS:
            raise ValueError("Provide between 1 and 500 reviews")
    return rows
