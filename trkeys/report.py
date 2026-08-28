"""XLSX output.

One ALL_KEYS sheet plus four filtered views and a SUMMARY. The filtered
sheets are views over the same rows, not recomputations, so a sheet can
never disagree with ALL_KEYS about a key's status.
"""

import os

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .model import Status

COLUMNS = [
    "application_code", "key", "used_in_web", "used_in_mobile",
    "dynamic", "actual_usage", "exists_in_dictionary",
    "exists_in_db_sources", "db_source", "db_source_table",
    "db_source_column", "expected_source", "status", "action", "details",
]

SUMMARY_COLUMNS = [
    "application_code", "total_keys", "web_keys", "mobile_keys",
    "backend_keys", "multi_application_keys", "unused_keys", "missing_keys",
    "source_missing", "source_mismatch", "multiple_source_conflicts",
    "dictionary_keys", "db_source_keys",
]

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(color="FFFFFF", bold=True)

STATUS_FILL = {
    Status.SOURCE_MISSING: PatternFill("solid", fgColor="FFF2CC"),
    Status.SOURCE_MISMATCH: PatternFill("solid", fgColor="FCE4D6"),
    Status.MULTIPLE_SOURCES: PatternFill("solid", fgColor="E4DFEC"),
    Status.MISSING_IN_DATABASE: PatternFill("solid", fgColor="F8CBAD"),
    Status.UNUSED: PatternFill("solid", fgColor="E2EFDA"),
    Status.DYNAMIC_ONLY: PatternFill("solid", fgColor="DDEBF7"),
}

# Excel refuses to open a file with a sheet over this many rows.
MAX_ROWS = 1048575


def _sheet(wb, title, columns, rows, colourise=True):
    ws = wb.create_sheet(title)
    ws.append(columns)
    for c in range(1, len(columns) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")

    truncated = 0
    if len(rows) > MAX_ROWS:
        truncated = len(rows) - MAX_ROWS
        rows = rows[:MAX_ROWS]

    for r in rows:
        ws.append([r.get(c, "") for c in columns])

    # Colour the status column with one conditional-formatting rule per
    # status rather than by styling each cell. Styling per cell means an
    # ws.max_row lookup per row, which is O(n) each and makes the whole
    # sheet O(n^2) -- minutes on a 36k-row export.
    if colourise and "status" in columns and rows:
        letter = get_column_letter(columns.index("status") + 1)
        span = "%s2:%s%d" % (letter, letter, len(rows) + 1)
        for status, fill in STATUS_FILL.items():
            ws.conditional_formatting.add(
                span,
                FormulaRule(
                    formula=['EXACT($%s2,"%s")' % (letter, status)],
                    fill=fill,
                    stopIfTrue=False,
                ),
            )

    ws.freeze_panes = "C2" if "key" in columns else "A2"
    ws.auto_filter.ref = ws.dimensions

    widths = {
        "application_code": 16, "key": 52, "used_in_web": 12,
        "used_in_mobile": 14, "dynamic": 10, "actual_usage": 20,
        "exists_in_dictionary": 20, "exists_in_db_sources": 20,
        "db_source": 20, "db_source_table": 30, "db_source_column": 22,
        "expected_source": 22, "status": 20, "action": 40, "details": 60,
    }
    for i, c in enumerate(columns, 1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(c, 20)

    if truncated:
        ws.cell(row=1, column=len(columns) + 1,
                value="TRUNCATED: %d further rows omitted" % truncated)
    return ws


def write(path, rows, summary, notes=None):
    wb = Workbook()
    wb.remove(wb.active)

    _sheet(wb, "SUMMARY", SUMMARY_COLUMNS, summary, colourise=False)
    _sheet(wb, "ALL_KEYS", COLUMNS, rows)

    mismatch = [r for r in rows if r["status"] in
                (Status.SOURCE_MISMATCH, Status.SOURCE_MISSING)]
    _sheet(wb, "SOURCE_MISMATCH", COLUMNS, mismatch)

    _sheet(wb, "UNUSED", COLUMNS,
           [r for r in rows if r["status"] == Status.UNUSED])

    _sheet(wb, "MISSING_KEYS", COLUMNS,
           [r for r in rows if r["status"] == Status.MISSING_IN_DATABASE])

    _sheet(wb, "MULTIPLE_SOURCES", COLUMNS,
           [r for r in rows if r["status"] == Status.MULTIPLE_SOURCES])
    # Used, but only through DB content -- no platform is readable from the
    # code, so these carry no recommendation and must not be read as unused.
    _sheet(wb, "DYNAMIC_ONLY", COLUMNS,
           [r for r in rows if r["status"] == Status.DYNAMIC_ONLY])

    if notes:
        ws = wb.create_sheet("RUN_NOTES")
        ws.append(["note"])
        ws.cell(row=1, column=1).fill = HEADER_FILL
        ws.cell(row=1, column=1).font = HEADER_FONT
        for n in notes:
            ws.append([n])
        ws.column_dimensions["A"].width = 120

    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    wb.save(path)
    return path
