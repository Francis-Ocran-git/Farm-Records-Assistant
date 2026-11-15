"""Farm Records Assistant - data logic.

Everything numerical happens here, in Python. There are no Streamlit imports, so
these functions can be tested on their own (see tests/test_farm_logic.py).
"""
import io
import re
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

# --- Configuration lists / dictionaries that drive the app's behaviour --------
REQUIRED = ["date", "crop", "harvested_kg", "sales_revenue_ghs", "expenses_ghs"]  # columns the CSV must have
NUMERIC = ["harvested_kg", "sales_revenue_ghs", "expenses_ghs"]                    # columns that must be numbers >= 0
LABELS = {                                                                         # friendly names for tables/charts
    "harvested_kg": "Harvest (kg)",
    "sales_revenue_ghs": "Sales revenue (GHS)",
    "expenses_ghs": "Expenses (GHS)",
    "recorded_balance_ghs": "Recorded balance (GHS)",
}


@dataclass
class ValidationResult:
    df: pd.DataFrame | None      # clean records, or None when nothing usable remains
    excluded: pd.DataFrame       # rows left out, with the reason for each
    messages: list               # [(level, text)] where level is error/warning/success
    stats: dict                  # counts shown as metrics


def _fail(text, stats=None):
    return ValidationResult(None, pd.DataFrame(), [("error", text)], stats or {})


# --- 1. Validation ------------------------------------------------------------
def validate_records(raw_bytes: bytes) -> ValidationResult:
    """Validate an uploaded CSV. Unusable rows are excluded and explained, never silently fixed."""
    try:
        raw = pd.read_csv(io.BytesIO(raw_bytes), dtype=str, encoding="utf-8-sig")
    except pd.errors.EmptyDataError:
        return _fail("The file is empty. Upload a CSV with a header row and at least one record.")
    except UnicodeDecodeError:
        return _fail("This does not look like a text CSV file. Save it as CSV (UTF-8) and try again.")
    except Exception:
        return _fail("The file could not be read as a CSV. Check that it is comma-separated with one header row.")

    raw.columns = [str(c).strip().lower().replace(" ", "_") for c in raw.columns]
    missing_cols = [c for c in REQUIRED if c not in raw.columns]
    if missing_cols:
        return _fail(
            f"Missing required column(s): {', '.join(missing_cols)}. "
            f"Required columns: {', '.join(REQUIRED)}. Columns found: {', '.join(raw.columns) or 'none'}. "
            "No totals are shown until this is fixed.",
            {"rows_received": len(raw)},
        )
    if raw.empty:
        return _fail("The file has headers but no records. Add at least one row of data.")

    extra_cols = [c for c in raw.columns if c not in REQUIRED]
    raw = raw[REQUIRED].copy()
    raw["csv_row"] = range(2, len(raw) + 2)  # row number as seen in a spreadsheet (row 1 = header)

    # Parse each field; anything that cannot be parsed becomes NaT/NaN and is flagged below.
    parsed = pd.DataFrame(index=raw.index)
    parsed["date"] = pd.to_datetime(raw["date"], errors="coerce")
    parsed["crop"] = raw["crop"].str.strip().str.replace(r"\s+", " ", regex=True).str.title()
    for col in NUMERIC:
        parsed[col] = pd.to_numeric(raw[col].str.strip(), errors="coerce")

    # Collect the reason(s) each row is unusable.
    reasons = {i: [] for i in raw.index}

    def flag(mask, text_for_row):
        for i in raw.index[mask.fillna(False).to_numpy(dtype=bool)]:
            reasons[i].append(text_for_row(i))

    flag(raw["date"].isna(), lambda i: "date is missing")
    flag(raw["date"].notna() & parsed["date"].isna(), lambda i: f"date '{str(raw.at[i, 'date'])[:20]}' is not a valid date (use YYYY-MM-DD)")
    flag(parsed["crop"].isna() | (parsed["crop"] == ""), lambda i: "crop is missing")
    for col in NUMERIC:
        flag(raw[col].isna(), lambda i, c=col: f"{c} is missing")
        not_number = raw[col].notna() & (parsed[col].isna() | ~np.isfinite(parsed[col].astype(float)))
        flag(not_number, lambda i, c=col: f"{c} '{str(raw.at[i, c])[:20]}' is not a number")
        flag(parsed[col] < 0, lambda i, c=col: f"{c} is negative ({parsed.at[i, c]:g})")

    invalid_idx = [i for i, r in reasons.items() if r]
    valid = parsed.drop(index=invalid_idx)

    # Exact duplicates among otherwise valid rows: keep the first, exclude later copies.
    dup_mask = valid.duplicated(subset=REQUIRED, keep="first")
    dup_idx = valid.index[dup_mask.to_numpy()].tolist()
    for i in dup_idx:
        reasons[i].append("exact duplicate of an earlier row (first copy kept)")
    valid = valid.drop(index=dup_idx)

    excluded_rows = []
    for i in sorted(invalid_idx + dup_idx):
        excluded_rows.append({"csv_row": int(raw.at[i, "csv_row"]), "reason": "; ".join(reasons[i]),
                              **{c: raw.at[i, c] for c in REQUIRED}})
    excluded = pd.DataFrame(excluded_rows, columns=["csv_row", "reason"] + REQUIRED)

    stats = {"rows_received": len(raw), "valid_rows": len(valid), "excluded_rows": len(excluded),
             "duplicate_rows": len(dup_idx), "invalid_rows": len(invalid_idx)}

    messages = []
    if invalid_idx:
        messages.append(("warning", f"{len(invalid_idx)} row(s) have missing, non-numeric, negative or invalid values "
                                    "and were excluded from every total and chart. See the excluded-rows table for the reason."))
    if dup_idx:
        messages.append(("warning", f"{len(dup_idx)} exact duplicate row(s) found. The first copy was kept; later copies were excluded."))
    if extra_cols:
        messages.append(("info", f"Extra column(s) ignored: {', '.join(extra_cols)}."))

    if valid.empty:
        messages.append(("error", "No valid records remain after checking, so no totals can be shown. Correct the CSV and upload it again."))
        return ValidationResult(None, excluded, messages, stats)

    # Same date + crop with different values: suspicious for a "daily aggregate", but not provably wrong, so kept and flagged.
    conflict = valid.duplicated(subset=["date", "crop"], keep=False)
    if conflict.any():
        stats["conflicting_rows"] = int(conflict.sum())
        messages.append(("warning", f"{int(conflict.sum())} rows share the same date and crop but have different values. "
                                    "They were kept, but check whether they are daily aggregates that should be a single row."))
    if not messages:
        messages.append(("success", "All records passed the checks: required columns present, valid dates and numbers, "
                                    "no missing values, no duplicates, no negative values."))

    clean = valid[REQUIRED].copy()
    clean["date"] = clean["date"].dt.date
    clean["crop"] = clean["crop"].astype(str)
    clean = clean.sort_values(["date", "crop"]).reset_index(drop=True)
    return ValidationResult(clean, excluded, messages, stats)


# --- 2. Filtering and summaries -----------------------------------------------
def filter_records(df, crops, start, end):
    """The single place filters are applied, so cards, charts, table, report and AI all agree."""
    return df[df["crop"].isin(crops) & (df["date"] >= start) & (df["date"] <= end)].copy()


def summarize(df) -> dict:
    """All authoritative totals. The AI only ever sees these numbers."""
    by_crop = df.groupby("crop", as_index=False).agg(
        records=("harvested_kg", "size"), harvested_kg=("harvested_kg", "sum"),
        sales_revenue_ghs=("sales_revenue_ghs", "sum"), expenses_ghs=("expenses_ghs", "sum"))
    by_crop["recorded_balance_ghs"] = by_crop["sales_revenue_ghs"] - by_crop["expenses_ghs"]
    by_crop = by_crop.sort_values("sales_revenue_ghs", ascending=False).reset_index(drop=True)

    month = pd.to_datetime(df["date"]).dt.to_period("M").astype(str)
    by_month = (df.assign(month=month).groupby(["month", "crop"], as_index=False)[NUMERIC].sum()
                .sort_values(["month", "crop"]).reset_index(drop=True))
    by_date = df.groupby("date", as_index=False)[NUMERIC].sum().sort_values("date")

    revenue, expenses = float(df["sales_revenue_ghs"].sum()), float(df["expenses_ghs"].sum())
    return {
        "records": int(len(df)),
        "harvested_kg": round(float(df["harvested_kg"].sum()), 2),
        "sales_revenue_ghs": round(revenue, 2),
        "expenses_ghs": round(expenses, 2),
        "recorded_balance_ghs": round(revenue - expenses, 2),
        "date_min": min(df["date"]), "date_max": max(df["date"]),
        "by_crop": by_crop, "by_month": by_month, "by_date": by_date,
    }


def compare_ranges(df, a_start, a_end, b_start, b_end) -> pd.DataFrame:
    """Optional extension: totals for two date ranges side by side (df is already crop-filtered)."""
    rows = {}
    for name, (s, e) in {"Range A": (a_start, a_end), "Range B": (b_start, b_end)}.items():
        part = df[(df["date"] >= s) & (df["date"] <= e)]
        rows[name] = {"Records": len(part), **{LABELS[c]: round(float(part[c].sum()), 2) for c in NUMERIC}}
    out = pd.DataFrame(rows)
    out["Change (B - A)"] = out["Range B"] - out["Range A"]
    return out


# --- 3. Text built from the calculated summary ----------------------------------
def _safe(text):
    """Crop names come from the user's file, so keep them short and free of odd characters before they reach a prompt."""
    return re.sub(r"[^\w \-'&().,/]", "", str(text))[:40]


def build_context(summary, filters_text, excluded_count=0) -> str:
    """The only farm information sent to the AI: the same figures shown on screen."""
    s, bc = summary, summary["by_crop"]
    total_rev = s["sales_revenue_ghs"]
    lines = [
        f"Filters: {filters_text}",
        f"Records included: {s['records']} (date range {s['date_min']} to {s['date_max']})",
        f"Rows excluded earlier by data-quality checks: {excluded_count}",
        f"Total harvest: {s['harvested_kg']:,.2f} kg",
        f"Recorded sales revenue: GHS {s['sales_revenue_ghs']:,.2f}",
        f"Recorded expenses: GHS {s['expenses_ghs']:,.2f}",
        f"Recorded balance (revenue minus recorded expenses, NOT verified profit): GHS {s['recorded_balance_ghs']:,.2f}",
        f"Crop with highest recorded sales revenue: {_safe(bc.iloc[0]['crop'])}",
        f"Crop with highest recorded expenses: {_safe(bc.sort_values('expenses_ghs', ascending=False).iloc[0]['crop'])}",
        "", "Totals by crop:",
    ]
    for _, r in bc.iterrows():
        share = f", {r['sales_revenue_ghs'] / total_rev * 100:.1f}% of recorded revenue" if total_rev else ""
        lines.append(f"- {_safe(r['crop'])}: {int(r['records'])} records, harvest {r['harvested_kg']:,.2f} kg, "
                     f"revenue GHS {r['sales_revenue_ghs']:,.2f}, expenses GHS {r['expenses_ghs']:,.2f}, "
                     f"balance GHS {r['recorded_balance_ghs']:,.2f}{share}")
    lines += ["", "Monthly totals by crop (month | crop | harvest kg | revenue GHS | expenses GHS):"]
    for _, r in s["by_month"].head(60).iterrows():
        lines.append(f"- {r['month']} | {_safe(r['crop'])} | {r['harvested_kg']:,.2f} | "
                     f"{r['sales_revenue_ghs']:,.2f} | {r['expenses_ghs']:,.2f}")
    return "\n".join(lines)


_DATE_RE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
_NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def _numbers(text):
    values = []
    for token in _NUM_RE.findall(_DATE_RE.sub(" ", text)):
        try:
            values.append(float(token.rstrip(",").replace(",", "")))
        except ValueError:
            pass
    return values


def unsupported_numbers(answer: str, context: str) -> list:
    """Check AI output: return figures in the answer that do not appear in the calculated summary.

    Small numbers (<= 10) and years are ignored; other figures must match a context value within rounding.
    """
    allowed = _numbers(context)
    bad = []
    for v in _numbers(answer):
        if v <= 10 or (v == int(v) and 1990 <= v <= 2100):
            continue
        if not any(abs(v - a) <= 0.51 for a in allowed):
            bad.append(f"{v:,.2f}".rstrip("0").rstrip("."))
    return sorted(set(bad))


# --- 4. Downloadable Excel report ---------------------------------------------------
def build_report_xlsx(summary, filters_text, quality_messages, stats, excluded, filtered, qa_pairs=None) -> bytes:
    """Build the Excel report. Numbers are real numeric cells, so the farmer can keep working with them in Excel."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    green, bold = PatternFill("solid", fgColor="DDEBD3"), Font(bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")
    money_fmt = "#,##0.00"

    def put(ws, row, col, value, **style):
        cell = ws.cell(row=row, column=col, value=value)
        if isinstance(value, str) and value.startswith("="):
            cell.data_type = "s"          # text from the user's CSV or the AI must never become an Excel formula
        if style.get("bold"): cell.font = bold
        if style.get("fill"): cell.fill = green
        if style.get("wrap"): cell.alignment = wrap
        if style.get("fmt"): cell.number_format = style["fmt"]
        return cell

    def section(ws, row, text):
        put(ws, row, 1, text, bold=True, fill=True); put(ws, row, 2, None, fill=True)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)

    def fit_to_page(ws):
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True

    def table(ws, df, row=1, headers=None):
        for j, name in enumerate(df.columns, 1):
            put(ws, row, j, (headers or {}).get(name, name), bold=True, fill=True)
        for i, rec in enumerate(df.itertuples(index=False), row + 1):
            for j, val in enumerate(rec, 1):
                val = None if pd.isna(val) else val          # empty cells stay empty (NaN would corrupt the file)
                put(ws, i, j, val, fmt=money_fmt if isinstance(val, float) else None)
        for j, name in enumerate(df.columns, 1):
            width = max([len(str((headers or {}).get(name, name)))] + [len(str(v)) for v in df[name].head(200)]) + 2
            ws.column_dimensions[get_column_letter(j)].width = min(max(width, 12), 70)
        ws.freeze_panes = ws.cell(row=row + 1, column=1)

    wb = Workbook()
    ws = wb.active
    ws.title = "Report"
    ws.column_dimensions["A"].width = 58
    ws.column_dimensions["B"].width = 62
    r = 1
    put(ws, r, 1, "Farm Records Report", bold=True).font = Font(bold=True, size=16); r += 1
    put(ws, r, 1, "Generated"); put(ws, r, 2, date.today().isoformat()); r += 1
    put(ws, r, 1, "Filters applied"); put(ws, r, 2, filters_text, wrap=True); r += 2

    section(ws, r, "1. Observed facts (calculated in Python from the records)"); r += 1
    top = summary["by_crop"].iloc[0]
    facts = [("Records included", summary["records"], None),
             ("Date range", f"{summary['date_min']} to {summary['date_max']}", None),
             ("Total harvest (kg)", summary["harvested_kg"], money_fmt),
             ("Recorded sales revenue (GHS)", summary["sales_revenue_ghs"], money_fmt),
             ("Recorded expenses (GHS)", summary["expenses_ghs"], money_fmt),
             ("Recorded balance (GHS) = revenue minus recorded expenses", summary["recorded_balance_ghs"], money_fmt),
             ("Crop with highest recorded sales revenue", f"{top['crop']} (GHS {top['sales_revenue_ghs']:,.2f})", None)]
    for label, value, fmt in facts:
        put(ws, r, 1, label); c = put(ws, r, 2, value, fmt=fmt); c.alignment = Alignment(horizontal="left"); r += 1
    r += 1

    section(ws, r, "2. Data quality"); r += 1
    for label, key in (("Rows received", "rows_received"), ("Valid rows", "valid_rows"), ("Excluded rows", "excluded_rows")):
        put(ws, r, 1, label); put(ws, r, 2, stats.get(key, 0)).alignment = Alignment(horizontal="left"); r += 1
    for _, text in quality_messages:
        put(ws, r, 1, text, wrap=True); ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
        ws.row_dimensions[r].height = 15 * (len(text) // 110 + 1); r += 1
    r += 1

    section(ws, r, "3. Possible explanations and what to check next (NOT established by the data)"); r += 1
    for text in ("The records alone cannot say why a value rose or fell. Possible factors to investigate: selling prices, quantity "
                 "actually sold versus stored, market days, input purchases, labour, transport, storage and weather. Compare these "
                 "against further records before drawing conclusions.",):
        put(ws, r, 1, text, wrap=True); ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2); ws.row_dimensions[r].height = 62; r += 1
    r += 1

    section(ws, r, "4. Limitations"); r += 1
    for text in ("The recorded balance is not verified profit: the file may omit costs or other income.",
                 "The data does not contain quantities sold, yield per hectare or the causes of changes, so none are inferred.",
                 "Rows that failed validation are excluded from every figure in this report (see the Excluded rows sheet)."):
        put(ws, r, 1, text, wrap=True); ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2); ws.row_dimensions[r].height = 32; r += 1

    charts_ws = wb.create_sheet("Charts")
    crop_ws = wb.create_sheet("Crop totals")
    table(crop_ws, summary["by_crop"], headers={"crop": "Crop", "records": "Records", **LABELS})
    daily = summary["by_date"].copy()
    daily["date"] = daily["date"].astype(str)                       # ISO text keeps the chart's date axis simple and reliable
    daily_ws = wb.create_sheet("Daily totals")
    table(daily_ws, daily, headers={"date": "Date", **LABELS})
    table(wb.create_sheet("Filtered records"), filtered, headers={"date": "Date", "crop": "Crop", **LABELS})

    # Native Excel charts that read their values from the sheets above, so they always match the tables and Report figures.
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.chart.label import DataLabelList

    def styled(chart, title, x_title, y_title, legend=True):
        chart.title, chart.width, chart.height = title, 22, 10
        chart.x_axis.title, chart.y_axis.title = x_title, y_title
        chart.x_axis.delete = chart.y_axis.delete = False          # keep axes visible in newer Excel versions
        if not legend:
            chart.legend = None
        return chart

    def with_value_labels(chart):
        chart.dataLabels = DataLabelList()
        chart.dataLabels.showVal = True
        chart.dataLabels.showSerName = chart.dataLabels.showCatName = chart.dataLabels.showLegendKey = False
        chart.dataLabels.numFmt = money_fmt

    n, m = len(summary["by_crop"]), len(daily)
    crop_cats = Reference(crop_ws, min_col=1, min_row=2, max_row=n + 1)
    money_chart = styled(BarChart(), "Recorded sales revenue and expenses by crop (GHS)", "Crop", "Amount (GHS)")
    money_chart.type, money_chart.grouping = "col", "clustered"
    money_chart.add_data(Reference(crop_ws, min_col=4, max_col=5, min_row=1, max_row=n + 1), titles_from_data=True)   # revenue, expenses
    money_chart.set_categories(crop_cats)
    with_value_labels(money_chart)

    harvest_chart = styled(BarChart(), "Total harvest by crop (kg)", "Crop", "Harvest (kg)", legend=False)
    harvest_chart.type = "col"
    harvest_chart.add_data(Reference(crop_ws, min_col=3, min_row=1, max_row=n + 1), titles_from_data=True)           # harvest
    harvest_chart.set_categories(crop_cats)
    with_value_labels(harvest_chart)

    trend_chart = styled(LineChart(), "Recorded sales revenue and expenses by date (GHS)", "Date", "Amount (GHS)")
    trend_chart.add_data(Reference(daily_ws, min_col=3, max_col=4, min_row=1, max_row=m + 1), titles_from_data=True)  # revenue, expenses
    trend_chart.set_categories(Reference(daily_ws, min_col=1, min_row=2, max_row=m + 1))
    for series in trend_chart.series:
        series.marker.symbol, series.smooth = "circle", False

    put(charts_ws, 1, 1, "Charts").font = Font(bold=True, size=16)
    put(charts_ws, 2, 1, f"Built from the same filtered records as the Report sheet ({filters_text}). "
                         "Harvest is in kg; money is in GHS. Click a chart to edit it in Excel.")
    charts_ws.add_chart(money_chart, "A4")
    charts_ws.add_chart(harvest_chart, "A25")
    charts_ws.add_chart(trend_chart, "A46")
    if excluded is not None and not excluded.empty:
        table(wb.create_sheet("Excluded rows"), excluded, headers={"csv_row": "CSV row", "reason": "Why excluded"})
    if qa_pairs:
        qa = wb.create_sheet("AI answers")
        qa.column_dimensions["A"].width = 50; qa.column_dimensions["B"].width = 100
        put(qa, 1, 1, "Question", bold=True, fill=True); put(qa, 1, 2, "AI answer (explanation only - verify against the Report sheet)", bold=True, fill=True)
        for i, (q, a) in enumerate(qa_pairs, 2):
            put(qa, i, 1, q, wrap=True); put(qa, i, 2, a, wrap=True)
            qa.row_dimensions[i].height = max(30, 15 * (len(a) // 95 + 1))

    for sheet in wb.worksheets:
        fit_to_page(sheet)
    charts_ws.page_setup.orientation, charts_ws.page_setup.fitToHeight = "portrait", 1   # all three charts on one printed page
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
