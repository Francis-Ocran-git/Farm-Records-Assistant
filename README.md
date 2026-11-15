# 🌾 Farm Records Assistant

A Streamlit app that turns a simple farm CSV into **checked summaries, clear charts and plain-language answers**.
Built for the Get Skills Network *Python with AI* Level 1 capstone (Agriculture).

**Journey:** Upload farm records → Check the numbers → Explore the story.

## What it does
| Brief requirement | Where it lives |
|---|---|
| Validate columns, dates and numbers; flag missing values, duplicates and negatives | `farm_logic.validate_records` |
| Total harvest (kg), recorded sales revenue and expenses, filtered by crop and date | `farm_logic.filter_records`, `farm_logic.summarize` |
| Clearly labelled charts that match the table and cards | `app.py` section 3 (built from the same filtered data) |
| Answers grounded in calculated summaries | `farm_logic.build_context` + `ai_helper.ask_ai` |
| Excel report with observed facts vs possible explanations **and the three charts** (native, editable Excel charts) | `farm_logic.build_report_xlsx` |
| Optional extension: compare two date ranges | `farm_logic.compare_ranges` + expander in `app.py` |

**Python does all the arithmetic. The AI never sees raw rows and is never asked to total anything.** It receives only the
calculated summary for the records currently on screen, and its answer is checked: any figure that is not in that summary
is flagged to the user.

## Setup and run
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```
Set your OpenAI key **in the same terminal** before starting the app. The app reads it automatically:
```bash
export OPENAI_API_KEY="your-key"        # macOS / Linux
$env:OPENAI_API_KEY="your-key"          # Windows PowerShell
set OPENAI_API_KEY=your-key             # Windows Command Prompt
streamlit run app.py
```
Without a key, the chat section simply shows "The chatbot is not available right now" and everything else still works. The key is
never typed into the interface, displayed or stored in the code.

**AI provider:** OpenAI (`gpt-4o-mini` by default; override with the `OPENAI_MODEL` environment variable).

## Sample and test data (`data/`)
| File | Purpose |
|---|---|
| `agriculture_sample_records.csv` | Starter data: **480 kg**, **GHS 3,820** revenue, **GHS 1,280** expenses |
| `flawed_records.csv` | (for testers: upload it yourself, it is not offered in the app) Text in a number column, missing value, negative value, bad date, blank crop, exact duplicate, same date+crop conflict |
| `flawed_missing_column.csv` | (upload it yourself) `expenses_ghs` column removed - analysis must stop with an explanation |

## How the data checks work
- `REQUIRED` and `NUMERIC` (lists in `farm_logic.py`) define the expected columns and which must be numbers.
- Each row gets a list of *reasons* in a dictionary (`reasons[row] -> [text, ...]`). Any row with a reason is excluded and shown
  in the "Rows excluded" table with its CSV row number, so nothing is silently fixed or hidden.
- A missing column stops the analysis: **no totals are shown** rather than misleading ones.
- Exact duplicates keep the first copy. Same date + crop with different values is kept but flagged.
- Crop names are trimmed and capitalised so `maize` and `Maize` are one crop.
- Zero values are accepted; the recorded balance may be negative.

## Memory, caching and reset
- Progress (data, validation result, chat) lives in `st.session_state`, so it survives Streamlit reruns.
- `st.cache_data` caches only the public sample file. Private uploads are never cached.
- The AI is called **only when the user asks**, and only the last 6 messages (3 exchanges) are resent.
- **Reset application** clears everything, including the uploaded file.

## Tests
```bash
pip install pytest
python -m pytest -q
```
Covers the starter totals, filter/chart/card consistency, missing columns, the flawed file, unreadable files, edge values,
the AI-figure check and the Excel report (numeric cells, sheets, blank cells, formula-text safety, and that every chart's
data resolves to exactly the calculated figures).

## Demo checks (from the brief)
1. **Normal journey:** *Load sample records* → harvest 480 kg, revenue GHS 3,820, expenses GHS 1,280.
2. **Data quality:** upload `flawed_missing_column.csv` (clear error, no totals) or `flawed_records.csv` (row-by-row reasons).
3. **Evidence boundary:** ask *"Why did maize revenue change?"* - the answer states what the records show and what extra
   information is needed, without inventing causes.
4. **API failure:** start the app without a key (chat shows "not available") or go offline and ask a question (friendly message); the rest of the app still works.

## Deployment (Streamlit Community Cloud)
Push to GitHub → *New app* → choose `app.py` → *Advanced settings → Secrets* and paste `OPENAI_API_KEY = "..."`.
Streamlit Cloud exposes top-level secrets as environment variables, so the same code works. Never commit keys or `secrets.toml`.

## Known limitations
- The recorded balance is revenue minus recorded expenses, **not verified profit**; the file may omit costs or income.
- The data has no quantities sold, yield per hectare or causes, so none are inferred; "why" answers describe the records and
  list what extra information is needed.
- Dates must be in an unambiguous format such as `YYYY-MM-DD`; others are flagged as invalid.
- The figure check catches numbers that are not in the calculated summary; it cannot judge whether a sentence is wise.
- English-language prompts only.

## Contributors
| Name: Francis Ocran| Contribution: Developer |
|---|---|
| _Your name_ | _e.g. validation logic, Streamlit interface, AI integration, tests_ |
## App link:  https://farm-records-assistant-axcbjykqkzsr4dhuvqkzez.streamlit.app/

## Suggested commit history
`Add sample and flawed CSV files` → `Add validation logic with tests` → `Add summaries, filters and charts` →
`Add AI Q&A with safe error handling` → `Add report download and date-range comparison` → `Add README and deployment notes`
