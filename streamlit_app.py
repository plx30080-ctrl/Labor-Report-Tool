
import io
import re
from typing import List, Tuple, Dict

import pandas as pd
import numpy as np
import streamlit as st

# ---------------
# Helpers & Config
# ---------------

st.set_page_config(page_title="PLX vs Crescent Hours Reconciliation", page_icon="🧮", layout="wide")

DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
DAY_ALIASES = {
    "sun": "Sunday",
    "mon": "Monday",
    "tue": "Tuesday",
    "tues": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "thur": "Thursday",
    "thurs": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
}

STATUS_OPTIONS = ["", "Resolved", "Crescent Error", "Badge Correction Needed"]
DISCREPANCY_TYPES = ["PLX-only", "Crescent-only", "Mismatched Hours", "Invalid EID"]


def to_number(x):
    try:
        if pd.isna(x):
            return 0.0
        if isinstance(x, str):
            x = x.strip()
            if x == "":
                return 0.0
            x = x.replace(",", "")
        return float(x)
    except Exception:
        return 0.0


def normalize_eid(eid):
    """Return EID as digits-only string, or '' if invalid/empty."""
    if eid is None or (isinstance(eid, float) and np.isnan(eid)):
        return ""
    s = str(eid).strip()
    digits = re.sub(r"[^0-9]", "", s)
    return digits


def extract_eid_from_badge(badge: str) -> Tuple[str, bool]:
    """Extract EID from badge pattern 'PLX-########-ABC'. Returns (eid_digits, valid_flag)."""
    if not isinstance(badge, str):
        return ("", False)
    m = re.match(r"(?i)^PLX-([0-9]{1,})-([A-Za-z]{3})$", badge.strip())
    if not m:
        digits = re.findall(r"([0-9]{3,})", badge.strip())
        if digits:
            return (digits[0], False)
        return ("", False)
    return (m.group(1), True)


def find_day_columns(columns: List[str]) -> Dict[str, List[str]]:
    mapping = {d: [] for d in DAY_NAMES}
    for col in columns:
        low = str(col).strip().lower()
        found = False
        for alias, day in DAY_ALIASES.items():
            if re.search(rf"\b{alias}\b", low):
                mapping[day].append(col)
                found = True
                break
        if not found:
            for day in DAY_NAMES:
                if day.lower() in low:
                    mapping[day].append(col)
                    break
    mapping = {k: v for k, v in mapping.items() if v}
    return mapping


# ---------------------------------
# Loaders / Normalizers for Uploads
# ---------------------------------

def choose_name_column(cols):
    for c in cols:
        if str(c).strip().lower() == "name":
            return c
    for c in cols:
        cl = str(c).strip().lower()
        if cl.endswith(" name") and "department" not in cl:
            return c
    for c in cols:
        cl = str(c).strip().lower()
        if "associate" in cl and "name" in cl:
            return c
    for c in cols:
        cl = str(c).strip().lower()
        if "name" in cl and "department" not in cl:
            return c
    return None


def choose_eid_column(cols):
    for c in cols:
        if str(c).strip().lower() == "file":
            return c
    for c in cols:
        cl = str(c).strip().lower()
        if "eid" in cl or re.search(r"\bemployee\s*id\b", cl):
            return c
    for c in cols:
        if re.match(r"(?i)file\s*#?$", str(c)) or re.match(r"(?i)^id$", str(c)):
            return c
    return None


def load_plx(file) -> pd.DataFrame:
    try:
        df = pd.read_excel(file, header=3, dtype=str)
    except Exception:
        file.seek(0)
        df = pd.read_excel(file, dtype=str)

    df.columns = [str(c).strip() for c in df.columns]
    df = df.replace({np.nan: None})

    eid_col = choose_eid_column(df.columns)
    name_col = choose_name_column(df.columns)

    if eid_col is None:
        df["EID"] = ""
        eid_col = "EID"
    if name_col is None:
        df["Name"] = ""
        name_col = "Name"

    day_map = find_day_columns(df.columns)
    day_cols = sorted({col for cols in day_map.values() for col in cols})
    ot_candidates = [c for c in df.columns if re.search(r"(?i)\bOT\b|\bovertime\b", c)]

    numeric_df = df.copy()
    for c in day_cols + ot_candidates:
        numeric_df[c] = numeric_df[c].apply(to_number)

    reg_hours = numeric_df[day_cols].sum(axis=1) if day_cols else 0.0
    ot_hours = numeric_df[ot_candidates].sum(axis=1) if ot_candidates else 0.0

    norm = pd.DataFrame({
        "EID": df[eid_col].apply(normalize_eid),
        "Name": df[name_col].fillna("").astype(str).str.strip(),
        "Reg_Hours": reg_hours,
        "OT_Hours": ot_hours,
    })
    norm["Total_Hours"] = norm["Reg_Hours"].fillna(0) + norm["OT_Hours"].fillna(0)

    for d, cols in day_map.items():
        norm[f"Day_{d}"] = numeric_df[cols].sum(axis=1)

    norm = norm[~((norm["EID"] == "") & (norm["Total_Hours"] == 0))].reset_index(drop=True)
    return norm


def load_crescent(file) -> pd.DataFrame:
    name = getattr(file, "name", "").lower()
    try:
        if name.endswith(".csv"):
            df = pd.read_csv(file, dtype=str)
        else:
            df = pd.read_excel(file, dtype=str)
    except Exception:
        file.seek(0)
        df = pd.read_csv(file, dtype=str)

    df.columns = [str(c).strip() for c in df.columns]
    df = df.replace({np.nan: None})

    badge_col = None
    hours_col = None
    line_col = None
    name_col = None

    for c in df.columns:
        cl = c.lower()
        if badge_col is None and "badge" in cl:
            badge_col = c
        if hours_col is None and ("payable" in cl or (("hours" in cl or "hrs" in cl) and "pay" in cl)):
            hours_col = c
        if line_col is None and ("line" in cl):
            line_col = c
        if name_col is None and "name" in cl:
            name_col = c

    if badge_col is None:
        for c in df.columns:
            if re.search(r"(?i)\bbadge\b", c):
                badge_col = c
                break
    if hours_col is None:
        for c in df.columns:
            if re.search(r"(?i)\b(payable\s*hours|hours|hrs)\b", c):
                hours_col = c
                break
    if line_col is None:
        df["Line"] = ""
        line_col = "Line"
    if name_col is None:
        df["Name"] = ""
        name_col = "Name"

    badges = df[badge_col].fillna("")
    eid_extracted = []
    valid_flags = []
    last3s = []
    for b in badges:
        eid_d, valid = extract_eid_from_badge(b)
        eid_extracted.append(eid_d)
        valid_flags.append(valid)
        m = re.match(r"(?i)^PLX-[0-9]{1,}-([A-Za-z]{3})$", b.strip())
        last3s.append(m.group(1).upper() if m else "")

    out = pd.DataFrame({
        "Badge": badges.astype(str).str.strip(),
        "EID": [normalize_eid(x) for x in eid_extracted],
        "EID_Valid": valid_flags,
        "Last3": last3s,
        "Line": df[line_col].fillna("").astype(str).str.strip(),
        "Payable_Hours": df[hours_col].apply(to_number) if hours_col in df.columns else 0.0,
        "Name": df[name_col].fillna("").astype(str).str.strip(),
    })

    out = out[~((out["Badge"] == "") & (out["Payable_Hours"] == 0))].reset_index(drop=True)
    return out


# ----------------------
# Discrepancy Detection
# ----------------------

def summarize_plx(plx: pd.DataFrame) -> pd.DataFrame:
    g = plx.groupby(["EID", "Name"], dropna=False, as_index=False)[["Reg_Hours","OT_Hours","Total_Hours"]].sum()
    return g


def summarize_crescent(cres: pd.DataFrame) -> pd.DataFrame:
    valid = cres[cres["EID"] != ""].copy()
    g = valid.groupby(["EID"], as_index=False)["Payable_Hours"].sum()
    return g


def detect_discrepancies(plx: pd.DataFrame, cres: pd.DataFrame) -> pd.DataFrame:
    plx_sum = summarize_plx(plx)
    cres_sum = summarize_crescent(cres)

    merged = plx_sum.merge(cres_sum, on="EID", how="outer", suffixes=("_PLX", "_CRES"))
    merged["Total_Hours_PLX"] = merged["Total_Hours_PLX"].fillna(0.0)
    merged["Payable_Hours"] = merged["Payable_Hours"].fillna(0.0)

    sample = cres.groupby("EID", as_index=False).agg({
        "Line": lambda s: next((x for x in s if x), ""),
        "Badge": lambda s: next((x for x in s if x), ""),
        "Name": lambda s: next((x for x in s if x), ""),
    })
    merged = merged.merge(sample, on="EID", how="left")

    plx_name = plx_sum[["EID","Name"]].rename(columns={"Name":"Name_PLX"})
    merged = merged.merge(plx_name, on="EID", how="left")

    rows = []
    for _, r in merged.iterrows():
        eid = r["EID"] if isinstance(r["EID"], str) else ""
        plx_total = to_number(r.get("Total_Hours_PLX", 0))
        cres_total = to_number(r.get("Payable_Hours", 0))

        if eid == "":
            continue

        if plx_total > 0 and cres_total == 0:
            cat = "PLX-only"
        elif plx_total == 0 and cres_total > 0:
            cat = "Crescent-only"
        elif abs(plx_total - cres_total) > 1e-6:
            cat = "Mismatched Hours"
        else:
            continue

        rows.append({
            "EID": eid,
            "Name_PLX": r.get("Name_PLX", ""),
            "Name_CRES": r.get("Name", "") or "",
            "Badge": r.get("Badge", "") or "",
            "Line": r.get("Line", "") or "",
            "PLX_Hours": plx_total,
            "CRES_Hours": cres_total,
            "Diff": plx_total - cres_total,
            "Category": cat,
            "DayOfWeek": "",
            "Status": "",
            "Notes": "",
        })

    invalid_rows = cres[(cres["EID"] == "") & (cres["Payable_Hours"] > 0)]
    for _, r in invalid_rows.iterrows():
        rows.append({
            "EID": "",
            "Name_PLX": "",
            "Name_CRES": r.get("Name", ""),
            "Badge": r.get("Badge", ""),
            "Line": r.get("Line", ""),
            "PLX_Hours": 0.0,
            "CRES_Hours": to_number(r.get("Payable_Hours", 0)),
            "Diff": -to_number(r.get("Payable_Hours", 0)),
            "Category": "Invalid EID",
            "DayOfWeek": "",
            "Status": "",
            "Notes": "Badge format invalid; cannot match to EID",
        })

    out = pd.DataFrame(rows)
    if not out.empty:
        out["PLX_Hours"] = out["PLX_Hours"].astype(float)
        out["CRES_Hours"] = out["CRES_Hours"].astype(float)
        out["Diff"] = out["Diff"].astype(float)
    return out


# ----------------------
# UI Sections
# ----------------------

st.title("🧮 PLX vs Crescent — Hours Reconciliation")
st.caption("Upload the two reports, reconcile differences, and generate a client-ready summary.")

with st.sidebar:
    st.header("1) Upload Reports")
    plx_file = st.file_uploader("ProLogistix Report (.xls/.xlsx)", type=["xls","xlsx"], key="plx_up")
    cres_file = st.file_uploader("Crescent Report (.csv/.xlsx)", type=["csv","xlsx"], key="cres_up")

    st.markdown("---")
    st.header("Filter")
    day_choice = st.selectbox("Filter by Day of Week (PLX)",
                              options=["All Days"] + DAY_NAMES,
                              index=0,
                              help="Applies to comparisons & totals only. Raw tables remain unchanged.")

    st.markdown("---")
    st.header("Help")
    st.markdown("• **PLX**: Row 4 should contain headers (days of week). The EID column is often labeled **File**.")
    st.markdown("• **Crescent**: Includes **Badge**, **Payable Hours**, **Line**. Badges look like `PLX-00000000-ABC`.")

# Session state init
for key in ["plx_df","cres_df","disc_df"]:
    if key not in st.session_state:
        st.session_state[key] = pd.DataFrame()

# Load & normalize
if plx_file:
    st.session_state["plx_df"] = load_plx(plx_file)

if cres_file:
    st.session_state["cres_df"] = load_crescent(cres_file)

plx_df = st.session_state["plx_df"]
cres_df = st.session_state["cres_df"]

# ---------------
# 2) Unified Views
# ---------------
st.header("2) Unified Data Views")

c1, c2 = st.columns(2, gap="large")

with c1:
    st.subheader("ProLogistix (Normalized)")
    if not plx_df.empty:
        plx_view = st.data_editor(
            plx_df,
            key="plx_editor",
            num_rows="dynamic",
            width="stretch",
            column_config={
                "EID": st.column_config.TextColumn(help="Employee ID (digits only)"),
                "Name": st.column_config.TextColumn(),
                "Reg_Hours": st.column_config.NumberColumn(format="%.2f"),
                "OT_Hours": st.column_config.NumberColumn(format="%.2f"),
                "Total_Hours": st.column_config.NumberColumn(format="%.2f", help="Reg + OT", disabled=True),
            },
        )
        st.caption("Note: Day filter affects comparisons/totals below, not this raw view.")
    else:
        plx_view = pd.DataFrame()
        st.info("Upload a PLX report to view.")

with c2:
    st.subheader("Crescent (Normalized)")
    if not cres_df.empty:
        cres_view = st.data_editor(
            cres_df,
            key="cres_editor",
            num_rows="dynamic",
            width="stretch",
            column_config={
                "Badge": st.column_config.TextColumn(),
                "EID": st.column_config.TextColumn(help="Extracted from Badge; editable if needed"),
                "EID_Valid": st.column_config.CheckboxColumn(disabled=True),
                "Last3": st.column_config.TextColumn(disabled=True),
                "Line": st.column_config.TextColumn(),
                "Payable_Hours": st.column_config.NumberColumn(format="%.2f"),
                "Name": st.column_config.TextColumn(),
            },
        )
    else:
        cres_view = pd.DataFrame()
        st.info("Upload a Crescent report to view.")

# Recompute totals on edits using returned DataFrames
if not plx_view.empty:
    edited_plx = plx_view.copy()
    for required in ["EID","Name","Reg_Hours","OT_Hours","Total_Hours"]:
        if required not in edited_plx.columns:
            edited_plx[required] = "" if required in ["EID","Name"] else 0.0
    edited_plx["EID"] = edited_plx["EID"].apply(normalize_eid)
    edited_plx["Reg_Hours"] = edited_plx["Reg_Hours"].apply(to_number)
    edited_plx["OT_Hours"] = edited_plx["OT_Hours"].apply(to_number)
    edited_plx["Total_Hours"] = edited_plx["Reg_Hours"].fillna(0) + edited_plx["OT_Hours"].fillna(0)
    st.session_state["plx_df"] = edited_plx

if not cres_view.empty:
    edited_cres = cres_view.copy()
    edited_cres["EID"] = edited_cres["EID"].apply(normalize_eid)
    edited_cres["Payable_Hours"] = edited_cres["Payable_Hours"].apply(to_number)
    st.session_state["cres_df"] = edited_cres

# Build PLX comparison view depending on day filter
def build_plx_for_comparison(plx: pd.DataFrame, selected_day: str) -> pd.DataFrame:
    comp = plx.copy()
    if selected_day and selected_day != "All Days":
        day_col = f"Day_{selected_day}"
        if day_col in comp.columns:
            comp["Reg_Hours"] = comp[day_col].apply(to_number)
            comp["OT_Hours"] = 0.0
            comp["Total_Hours"] = comp["Reg_Hours"]
        else:
            st.warning(f"Selected day '{selected_day}' not found in PLX columns. Using full totals instead.")
    return comp

# --------------------------
# 3) Discrepancies & Resolutions
# --------------------------
st.header("3) Discrepancies & Resolutions")

if not st.session_state["plx_df"].empty and not st.session_state["cres_df"].empty:
    plx_for_compare = build_plx_for_comparison(st.session_state["plx_df"], day_choice)
    disc_df = detect_discrepancies(plx_for_compare, st.session_state["cres_df"])

    if not st.session_state["disc_df"].empty:
        prev = st.session_state["disc_df"][["EID","Badge","Category","Status","DayOfWeek","Notes"]].copy()
        disc_df = disc_df.merge(prev, on=["EID","Badge","Category"], how="left", suffixes=("","_prev"))
        for col in ["Status","DayOfWeek","Notes"]:
            disc_df[col] = disc_df[col].fillna(disc_df.get(f"{col}_prev"))
        drop_cols = [c for c in disc_df.columns if c.endswith("_prev")]
        disc_df.drop(columns=drop_cols, inplace=True, errors="ignore")

    st.caption("Use the dropdowns and notes to classify each discrepancy.")
    disc_view = st.data_editor(
        disc_df,
        key="disc_editor",
        width="stretch",
        num_rows="dynamic",
        hide_index=True,
        column_config={
            "Category": st.column_config.TextColumn(disabled=True),
            "EID": st.column_config.TextColumn(),
            "Name_PLX": st.column_config.TextColumn(disabled=True, label="Name (PLX)"),
            "Name_CRES": st.column_config.TextColumn(disabled=True, label="Name (CRES)"),
            "Badge": st.column_config.TextColumn(),
            "Line": st.column_config.TextColumn(),
            "PLX_Hours": st.column_config.NumberColumn(format="%.2f", disabled=True),
            "CRES_Hours": st.column_config.NumberColumn(format="%.2f", disabled=True),
            "Diff": st.column_config.NumberColumn(format="%.2f", disabled=True),
            "DayOfWeek": st.column_config.SelectboxColumn(options=[""] + DAY_NAMES, help="Optional manual override"),
            "Status": st.column_config.SelectboxColumn(options=STATUS_OPTIONS),
            "Notes": st.column_config.TextColumn(),
        },
    )
    st.session_state["disc_df"] = disc_view

    c3, c4, c5, c6 = st.columns(4)
    with c3:
        st.metric("PLX-only", int((disc_view["Category"] == "PLX-only").sum()))
    with c4:
        st.metric("Crescent-only", int((disc_view["Category"] == "Crescent-only").sum()))
    with c5:
        st.metric("Mismatched Hours", int((disc_view["Category"] == "Mismatched Hours").sum()))
    with c6:
        st.metric("Invalid EID", int((disc_view["Category"] == "Invalid EID").sum()))
else:
    st.info("Upload both files to generate discrepancies.")

# --------------------------
# 4) Totals Validation
# --------------------------
st.header("4) Totals Validation")

def totals(plx: pd.DataFrame, cres: pd.DataFrame) -> Tuple[float, float]:
    return float(plx["Total_Hours"].sum()), float(cres["Payable_Hours"].sum())

if not st.session_state["plx_df"].empty and not st.session_state["cres_df"].empty:
    plx_for_compare = build_plx_for_comparison(st.session_state["plx_df"], day_choice)
    t_plx, t_cres = totals(plx_for_compare, st.session_state["cres_df"])
    cA, cB = st.columns(2)
    with cA:
        st.metric(f"PLX Total Hours ({day_choice})", f"{t_plx:,.2f}")
    with cB:
        st.metric("Crescent Total Hours", f"{t_cres:,.2f}")

    if abs(t_plx - t_cres) < 1e-6:
        st.success("Totals match. ✅")
    else:
        st.warning("Totals do not match. Please review discrepancies and edits. ⚠️")

# --------------------------
# 5) Client Summary Output
# --------------------------
st.header("5) Generate Client Summary (Crescent Errors)")

def build_client_summary(disc_df: pd.DataFrame) -> str:
    parts = []
    df = disc_df.copy()
    df = df[df["Status"] == "Crescent Error"]
    for _, r in df.iterrows():
        name = r.get("Name_PLX") or r.get("Name_CRES") or "Associate"
        line = str(r.get("Line") or "").strip()
        badge = r.get("Badge") or ""
        correct = to_number(r.get("PLX_Hours", 0))
        incorrect = to_number(r.get("CRES_Hours", 0))
        seg_line = f" - Worked Line {line}" if line else ""
        seg_badge = f" [{badge}]" if badge else ""
        parts.append(f"{name}{seg_line} for {correct:.2f} (correct), not {incorrect:.2f} (incorrect).{seg_badge}")
    return "\n".join(parts).strip()

if not st.session_state["disc_df"].empty:
    summary_text = build_client_summary(st.session_state["disc_df"])
    st.text_area("Copy-ready summary:", summary_text, height=200)
    st.download_button(
        "⬇️ Download Crescent Errors Summary (.txt)",
        data=summary_text.encode("utf-8"),
        file_name="crescent_errors_summary.txt",
        mime="text/plain",
        help="Includes only items marked 'Crescent Error'.",
        use_container_width=False,
    )
else:
    st.info("Mark discrepancies as 'Crescent Error' above to generate the summary.")

# --------------------------
# 6) Export / Save
# --------------------------
st.header("6) Export Current State")

if not st.session_state["plx_df"].empty or not st.session_state["cres_df"].empty or not st.session_state["disc_df"].empty:
    with io.BytesIO() as buffer:
        with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
            if not st.session_state["plx_df"].empty:
                st.session_state["plx_df"].to_excel(writer, index=False, sheet_name="PLX_Normalized")
            if not st.session_state["cres_df"].empty:
                st.session_state["cres_df"].to_excel(writer, index=False, sheet_name="Crescent_Normalized")
            if not st.session_state["disc_df"].empty:
                st.session_state["disc_df"].to_excel(writer, index=False, sheet_name="Discrepancies")
        data = buffer.getvalue()

    st.download_button(
        "⬇️ Download Reconciliation Workbook (.xlsx)",
        data=data,
        file_name="plx_crescent_reconciliation.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        help="Exports normalized PLX/Crescent tabs plus Discrepancies.",
        use_container_width=False,
    )

# --------------------------
# Footer
# --------------------------
st.markdown("---")
st.caption("Data editors now use width='stretch'. Edits write back via return values to avoid session_state dicts.")
