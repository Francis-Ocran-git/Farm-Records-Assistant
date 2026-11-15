"""Farm Records Assistant - Streamlit interface.

Journey: Upload farm records -> Check the numbers -> Explore the story.
Python calculates every figure (farm_logic.py); the AI only explains them (ai_helper.py).
"""
import os
from datetime import timedelta
from pathlib import Path

import plotly.express as px
import streamlit as st

import ai_helper
import farm_logic as fl

st.set_page_config(page_title="Farm Records Assistant", page_icon="🌾", layout="wide")

DATA_DIR = Path(__file__).parent / "data"
SAMPLE = DATA_DIR / "agriculture_sample_records.csv"
EXAMPLE_QUESTIONS = [
    "Which crop generated the most recorded sales revenue?",
    "What were our recorded expenses, and what should I look at more closely?",
    "Why did maize revenue change?",
]
# The API key is read automatically from the terminal that started the app (never typed into the interface or stored in code).
API_KEY = os.getenv("OPENAI_API_KEY", "").strip().strip("\"'")
ALERTS = {"error": st.error, "warning": st.warning, "success": st.success, "info": st.info}

# --- Session state: survives Streamlit reruns --------------------------------------
DEFAULTS = {"result": None, "source": "", "upload_sig": None, "chat_history": [], "chat_display": [],
            "uploader_key": 0, "data_version": 0}
for _k, _v in DEFAULTS.items():
    st.session_state.setdefault(_k, list(_v) if isinstance(_v, list) else _v)


@st.cache_data  # public, static sample data: safe to cache
def read_file(path: str) -> bytes:
    return Path(path).read_bytes()


@st.cache_data
def validate_sample():
    return fl.validate_records(read_file(str(SAMPLE)))


def set_data(result, source, sig=None):
    """Store a new dataset and start a fresh conversation (new data version also resets the filter widgets)."""
    st.session_state.update(result=result, source=source, upload_sig=sig, chat_history=[], chat_display=[],
                            data_version=st.session_state.data_version + 1)


def reset_app():
    """Clear everything, including the uploaded file (a new uploader key empties the file box)."""
    keep = {k: st.session_state[k] for k in ("uploader_key", "data_version")}
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.session_state.update(keep, uploader_key=keep["uploader_key"] + 1)


def clear_chat():
    st.session_state.chat_history, st.session_state.chat_display = [], []


# --- Header and instructions -------------------------------------------------------
st.title("🌾 Farm Records Assistant")
st.caption("Turn your farm records into checked summaries, clear charts and plain-language answers.")

with st.expander("How to use this assistant", expanded=st.session_state.result is None):
    st.markdown(
        "1. **Upload** a farm records CSV, or press **Load sample records**.\n"
        "2. **Check** the data-quality feedback. Problem rows are excluded and explained, never silently fixed.\n"
        "3. **Explore** summaries and charts, filtering by crop and date.\n"
        "4. **Ask** a question. Answers use the same calculated figures shown on screen.\n"
        "5. **Download** a report that separates observed facts from possible explanations.")
    st.info("Required columns: `date` (YYYY-MM-DD), `crop`, `harvested_kg`, `sales_revenue_ghs`, `expenses_ghs`. "
            "Each row is a daily total for one crop; harvest is measured in kg and money in GHS.")
    st.download_button("Download sample CSV", read_file(str(SAMPLE)), "agriculture_sample_records.csv", "text/csv")

# --- Upload / load sample / reset ----------------------------------------------------
left, right = st.columns([2, 1])
with left:
    uploaded = st.file_uploader("Upload farm records (CSV)", type=["csv"], key=f"uploader_{st.session_state.uploader_key}")
with right:
    st.write("")  # spacing to line the buttons up with the uploader
    if st.button("Load sample records"):
        # Remember the current upload so it is not re-processed over the sample on the next rerun.
        set_data(validate_sample(), "sample", (uploaded.name, uploaded.size) if uploaded else None)
    st.button("Reset application", on_click=reset_app)

if uploaded is not None:
    signature = (uploaded.name, uploaded.size)
    if st.session_state.upload_sig != signature:  # process only when a new file is chosen
        set_data(fl.validate_records(uploaded.getvalue()), uploaded.name, signature)

result = st.session_state.result
if result is None:
    st.info("Upload a CSV or press **Load sample records** to begin.")
    st.stop()

# --- 1. Data quality ---------------------------------------------------------------------
st.subheader("1 · Data quality check")
st.caption(f"Source: {st.session_state.source}")
for level, text in result.messages:
    ALERTS[level](text)
stats = result.stats
if "valid_rows" in stats:
    m = st.columns(4)
    m[0].metric("Rows received", stats["rows_received"])
    m[1].metric("Valid rows", stats["valid_rows"])
    m[2].metric("Excluded rows", stats["excluded_rows"])
    m[3].metric("Duplicate rows", stats["duplicate_rows"])
if not result.excluded.empty:
    with st.expander(f"Rows excluded from calculations ({len(result.excluded)})"):
        st.dataframe(result.excluded.rename(columns={"csv_row": "CSV row", "reason": "Why excluded"}), hide_index=True)
with st.expander("How problems are handled"):
    st.markdown(
        "- **Missing required column:** analysis stops with an explanation; no totals are shown.\n"
        "- **Missing, non-numeric or invalid date/number values:** the row is excluded and listed with its reason.\n"
        "- **Negative harvest, revenue or expenses:** the row is excluded and listed (zero is accepted).\n"
        "- **Exact duplicate rows:** the first copy is kept, later copies are excluded.\n"
        "- **Same date and crop with different values:** kept, but flagged for you to check.\n"
        "- **Crop names** are trimmed and capitalised, so `maize` and `Maize` count as one crop.")
if result.df is None:
    st.stop()  # nothing usable: do not display misleading totals

# --- 2. Filters and summary cards ---------------------------------------------------------
base = result.df
st.subheader("2 · Filter and summarise")
v = st.session_state.data_version
all_crops = sorted(base["crop"].unique())
min_d, max_d = min(base["date"]), max(base["date"])
f1, f2 = st.columns(2)
crops = f1.multiselect("Crop", all_crops, default=all_crops, key=f"crops_{v}")
date_range = f2.date_input("Date range", value=(min_d, max_d), min_value=min_d, max_value=max_d, key=f"dates_{v}")
if not crops:
    st.warning("Select at least one crop.")
    st.stop()
if not (isinstance(date_range, (tuple, list)) and len(date_range) == 2):
    st.info("Pick both a start date and an end date.")
    st.stop()
start, end = date_range
filtered = fl.filter_records(base, crops, start, end)
if filtered.empty:
    st.warning("No records match these filters. Change the crop or date range.")
    st.stop()

summary = fl.summarize(filtered)
filters_text = f"crops: {', '.join(crops)}; dates: {start} to {end}"
if stats.get("excluded_rows"):
    st.warning(f"These totals exclude {stats['excluded_rows']} row(s) that failed the data checks (see section 1).")
c = st.columns(4)
c[0].metric("Total harvest", f"{summary['harvested_kg']:,.2f} kg")
c[1].metric("Recorded sales revenue", f"GHS {summary['sales_revenue_ghs']:,.2f}")
c[2].metric("Recorded expenses", f"GHS {summary['expenses_ghs']:,.2f}")
c[3].metric("Recorded balance*", f"GHS {summary['recorded_balance_ghs']:,.2f}")
st.caption("*Recorded balance = recorded revenue minus recorded expenses. It is not verified profit: the file may omit costs or other income.")

# --- 3. Charts and tables (all built from the same filtered records) -------------------------
st.subheader("3 · Explore the patterns")
by_crop = summary["by_crop"]
tab_charts, tab_crops, tab_rows = st.tabs(["Charts", "Crop totals", "Filtered records"])
with tab_charts:
    money = by_crop.melt(id_vars="crop", value_vars=["sales_revenue_ghs", "expenses_ghs"], var_name="measure", value_name="ghs")
    money["measure"] = money["measure"].map(fl.LABELS)
    g1, g2 = st.columns(2)
    g1.plotly_chart(px.bar(money, x="crop", y="ghs", color="measure", barmode="group", text_auto=",.2f",
                           title="Recorded sales revenue and expenses by crop (GHS)",
                           labels={"crop": "Crop", "ghs": "Amount (GHS)", "measure": "Measure"}))
    g2.plotly_chart(px.bar(by_crop, x="crop", y="harvested_kg", text_auto=",.2f", title="Total harvest by crop (kg)",
                           labels={"crop": "Crop", "harvested_kg": "Harvest (kg)"}))
    trend = summary["by_date"].melt(id_vars="date", value_vars=["sales_revenue_ghs", "expenses_ghs"], var_name="measure", value_name="ghs")
    trend["measure"] = trend["measure"].map(fl.LABELS)
    st.plotly_chart(px.line(trend, x="date", y="ghs", color="measure", markers=True,
                            title="Recorded sales revenue and expenses by date (GHS)",
                            labels={"date": "Date", "ghs": "Amount (GHS)", "measure": "Measure"}))
    st.caption("Charts use the same filtered records as the summary cards and the tables. Harvest is in kg; money is in GHS.")
with tab_crops:
    st.dataframe(by_crop.rename(columns={"crop": "Crop", "records": "Records", **fl.LABELS}).round(2), hide_index=True)
with tab_rows:
    st.dataframe(filtered, hide_index=True)
    st.download_button("Download filtered CSV", filtered.to_csv(index=False).encode("utf-8"), "filtered_farm_records.csv", "text/csv")

with st.expander("Optional: compare two date ranges"):
    mid = start + (end - start) / 2
    next_day = min(mid + timedelta(days=1), end)
    k = f"{v}_{start}_{end}"
    r1, r2 = st.columns(2)
    range_a = r1.date_input("Range A", value=(start, mid), min_value=start, max_value=end, key=f"cmpa_{k}")
    range_b = r2.date_input("Range B", value=(next_day, end), min_value=start, max_value=end, key=f"cmpb_{k}")
    if all(isinstance(r, (tuple, list)) and len(r) == 2 for r in (range_a, range_b)):
        (a0, a1), (b0, b1) = range_a, range_b
        if a0 <= b1 and b0 <= a1:
            st.warning("The ranges overlap, so records in the overlap count in both columns.")
        st.dataframe(fl.compare_ranges(filtered, a0, a1, b0, b1))
        t1, t2 = st.columns(2)
        t1.caption(f"Records in Range A ({a0} to {a1})")
        t1.dataframe(filtered[(filtered["date"] >= a0) & (filtered["date"] <= a1)], hide_index=True)
        t2.caption(f"Records in Range B ({b0} to {b1})")
        t2.dataframe(filtered[(filtered["date"] >= b0) & (filtered["date"] <= b1)], hide_index=True)
    else:
        st.info("Pick a start and an end date for both ranges.")

# --- 4. Report -----------------------------------------------------------------------------------
st.subheader("4 · Download a report")
shown = st.session_state.chat_display
qa_pairs = [(shown[i]["content"], shown[i + 1]["content"]) for i in range(0, len(shown) - 1, 2)]
try:
    report_xlsx = fl.build_report_xlsx(summary, filters_text, result.messages, stats, result.excluded, filtered, qa_pairs)
    st.download_button("Download report (Excel)", report_xlsx, "farm_report.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.caption("An Excel workbook with the observed facts (kept separate from possible explanations), three charts, "
               "crop and daily totals, the filtered records and any excluded rows, each on its own sheet. "
               "AI answers from the chat get their own sheet.")
except Exception:
    st.warning("The Excel report could not be created. Make sure all packages are installed (pip install -r requirements.txt).")

# --- 5. Ask the assistant -------------------------------------------------------------------------
st.subheader("5 · Ask about your records")
st.caption("Answers use the calculated summary for the current filters, not raw-row arithmetic by the AI.")
if not API_KEY:
    st.info("🤖 The chatbot is not available right now.")
    st.stop()
clicked = None
for col, q in zip(st.columns(len(EXAMPLE_QUESTIONS)), EXAMPLE_QUESTIONS):
    if col.button(q, key=f"example_{q}"):
        clicked = q
question = st.chat_input("e.g. Which crop generated the most recorded sales revenue?") or clicked

if question:  # the AI is only called when the user asks
    context = fl.build_context(summary, filters_text, stats.get("excluded_rows", 0))
    st.session_state.chat_display.append({"role": "user", "content": question})
    try:
        with st.spinner("Reviewing the calculated summary..."):
            answer = ai_helper.ask_ai(question, context, st.session_state.chat_history, API_KEY)
        turn = [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
        st.session_state.chat_history = (st.session_state.chat_history + turn)[-ai_helper.MAX_HISTORY:]
        unsupported = fl.unsupported_numbers(answer, context)  # check AI output against the calculated figures
        if unsupported:
            answer += (f"\n\n⚠️ **Please check:** the figure(s) {', '.join(unsupported)} do not appear in the calculated "
                       "summary above, so treat them as unverified.")
    except ai_helper.AIUnavailable as exc:
        answer = str(exc)
    except Exception:  # never show raw provider errors, which can contain sensitive details
        answer = "The chatbot couldn't answer just now. Please try again in a moment. Your summaries, charts and report are unaffected."
    st.session_state.chat_display.append({"role": "assistant", "content": answer})
    st.session_state.chat_display = st.session_state.chat_display[-20:]
    st.rerun()  # re-draw the page so the report above includes this new answer

for item in st.session_state.chat_display:
    with st.chat_message(item["role"]):
        st.markdown(item["content"])
if st.session_state.chat_display:
    st.button("Clear chat", on_click=clear_chat)
