"""Run with:  python -m pytest -q   (from the project folder)"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import farm_logic as fl  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
def load(name): return fl.validate_records((DATA / name).read_bytes())


def test_starter_totals_match_brief():
    res = load("agriculture_sample_records.csv")
    assert res.df is not None and [l for l, _ in res.messages] == ["success"]
    s = fl.summarize(res.df)
    assert (s["harvested_kg"], s["sales_revenue_ghs"], s["expenses_ghs"]) == (480, 3820, 1280)
    assert s["recorded_balance_ghs"] == 2540
    assert s["by_crop"].iloc[0]["crop"] == "Maize"          # highest recorded revenue


def test_chart_table_and_cards_agree_after_filtering():
    df = load("agriculture_sample_records.csv").df
    f = fl.filter_records(df, ["Maize", "Yam"], date(2025, 3, 3), date(2025, 3, 13))
    s = fl.summarize(f)
    assert round(s["by_crop"]["sales_revenue_ghs"].sum(), 2) == s["sales_revenue_ghs"] == round(f["sales_revenue_ghs"].sum(), 2)
    assert round(s["by_date"]["harvested_kg"].sum(), 2) == s["harvested_kg"]
    assert set(f["crop"]) <= {"Maize", "Yam"} and f["date"].max() <= date(2025, 3, 13)


def test_missing_column_blocks_totals():
    res = load("flawed_missing_column.csv")
    assert res.df is None and "expenses_ghs" in res.messages[0][1]


def test_flawed_file_is_explained_not_hidden():
    res = load("flawed_records.csv")
    reasons = " | ".join(res.excluded["reason"])
    assert "harvested_kg 'forty' is not a number" in reasons
    assert "sales_revenue_ghs is missing" in reasons
    assert "expenses_ghs is negative" in reasons
    assert "not a valid date" in reasons and "crop is missing" in reasons
    assert "exact duplicate" in reasons
    assert res.stats["rows_received"] == 10 and res.stats["valid_rows"] == 4 and res.stats["excluded_rows"] == 6
    assert any("same date and crop" in t for _, t in res.messages)      # conflicting Tomato rows kept + flagged
    assert res.df is not None and set(res.df["crop"]) == {"Maize", "Tomato", "Yam"}   # 'tomato' normalised


def test_unreadable_inputs():
    assert fl.validate_records(b"").df is None
    assert fl.validate_records(b"date,crop,harvested_kg,sales_revenue_ghs,expenses_ghs\n").df is None
    assert fl.validate_records(b"\xff\xfe\x00\x81binary").df is None
    allbad = fl.validate_records(b"date,crop,harvested_kg,sales_revenue_ghs,expenses_ghs\n2025-01-01,Maize,x,1,1\n")
    assert allbad.df is None and allbad.messages[-1][0] == "error"


def test_zero_values_accepted_and_balance_can_be_negative():
    res = fl.validate_records(b"date,crop,harvested_kg,sales_revenue_ghs,expenses_ghs\n2025-01-01,Maize,0,0,50\n")
    assert res.df is not None and fl.summarize(res.df)["recorded_balance_ghs"] == -50


def test_context_contains_only_calculated_figures_and_flags_unsupported_numbers():
    df = load("agriculture_sample_records.csv").df
    ctx = fl.build_context(fl.summarize(df), "all", 0)
    assert "GHS 3,820.00" in ctx and "NOT verified profit" in ctx
    assert fl.unsupported_numbers("Maize earned GHS 1,360 and total revenue was GHS 3,820.", ctx) == []
    assert fl.unsupported_numbers("Revenue fell by 56%, to GHS 9,999.", ctx) == ["9,999", "56"] or \
           fl.unsupported_numbers("Revenue fell by 56%, to GHS 9,999.", ctx) == ["56", "9,999"]


def test_compare_ranges():
    df = load("agriculture_sample_records.csv").df
    cmp = fl.compare_ranges(df, date(2025, 3, 3), date(2025, 3, 9), date(2025, 3, 10), date(2025, 3, 16))
    assert cmp.loc["Records", "Range A"] == 4 and cmp.loc["Sales revenue (GHS)", "Range A"] == 1640


def _xlsx(name, qa=None):
    import io
    from openpyxl import load_workbook
    res = load(name); f = res.df
    s = fl.summarize(f)
    raw = fl.build_report_xlsx(s, "all", res.messages, res.stats, res.excluded, f, qa)
    return load_workbook(io.BytesIO(raw))


def test_excel_report_has_numeric_facts_and_all_sheets():
    wb = _xlsx("agriculture_sample_records.csv", [("Q?", "A.")])
    assert wb.sheetnames == ["Report", "Charts", "Crop totals", "Daily totals", "Filtered records", "AI answers"]
    ws = wb["Report"]
    facts = {ws.cell(r, 1).value: ws.cell(r, 2).value for r in range(1, ws.max_row + 1)}
    assert facts["Total harvest (kg)"] == 480 and facts["Recorded sales revenue (GHS)"] == 3820
    assert facts["Recorded expenses (GHS)"] == 1280 and isinstance(facts["Total harvest (kg)"], (int, float))
    text = " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value)
    for heading in ("Observed facts", "Data quality", "Possible explanations", "Limitations"):
        assert heading in text
    assert wb["Filtered records"].max_row == 13 and wb["Crop totals"]["A2"].value == "Maize"


def _series_values(wb, ref):
    """Resolve a chart reference like 'Crop totals'!$D$2:$D$5 to the numbers/text in those cells."""
    import re
    sheet, rng = re.match(r"^'?(.+?)'?!(.+)$", ref).groups()
    cells = wb[sheet][rng.replace("$", "")]
    if not isinstance(cells, tuple):                       # a single cell, e.g. a series title
        return [cells.value]
    return [c.value for row in cells for c in (row if isinstance(row, tuple) else (row,))]


def test_excel_report_contains_charts_that_match_the_calculated_data():
    res = load("agriculture_sample_records.csv"); s = fl.summarize(res.df)
    wb = _xlsx("agriculture_sample_records.csv")
    charts = wb["Charts"]._charts
    assert len(charts) == 3
    money, harvest, trend = charts
    by_crop = s["by_crop"]
    # chart 1: revenue and expenses by crop; chart 2: harvest by crop; chart 3: revenue and expenses by date
    assert _series_values(wb, money.series[0].val.numRef.f) == list(by_crop["sales_revenue_ghs"])
    assert _series_values(wb, money.series[1].val.numRef.f) == list(by_crop["expenses_ghs"])
    assert _series_values(wb, (money.series[0].cat.strRef or money.series[0].cat.numRef).f) == list(by_crop["crop"])
    assert _series_values(wb, harvest.series[0].val.numRef.f) == list(by_crop["harvested_kg"])
    assert sum(_series_values(wb, trend.series[0].val.numRef.f)) == s["sales_revenue_ghs"] == 3820
    assert sum(_series_values(wb, trend.series[1].val.numRef.f)) == s["expenses_ghs"] == 1280
    assert _series_values(wb, money.series[0].tx.strRef.f) == ["Sales revenue (GHS)"]


def test_excel_report_handles_excluded_rows_blanks_and_formula_text():
    wb = _xlsx("flawed_records.csv")
    assert "Excluded rows" in wb.sheetnames and wb["Excluded rows"].max_row == 7      # header + 6 excluded
    import io
    from openpyxl import load_workbook
    bad = b"date,crop,harvested_kg,sales_revenue_ghs,expenses_ghs\n2025-01-01,=HYPERLINK(\"http://x\"),5,5,5\n"
    res = fl.validate_records(bad); s = fl.summarize(res.df)
    out = load_workbook(io.BytesIO(fl.build_report_xlsx(s, "all", res.messages, res.stats, res.excluded, res.df)))
    cell = out["Crop totals"]["A2"]
    assert cell.data_type == "s"                                                       # stored as text, never a formula


if __name__ == "__main__":                       # lets you run without pytest too
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
