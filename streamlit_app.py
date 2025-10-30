
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
    """Return EID as zero-padded string of digits (no sign), or '' if invalid/empty."""
    if eid is None or (isinstance(eid, float) and np.isnan(eid)):
        return ""
    s = str(eid).strip()
    # remove non-digits
    digits = re.sub(r"[^0-9]", "", s)
    return digits


def extract_eid_from_badge(badge: str) -> Tuple[str, bool]:
    """
    Extract EID from badge pattern 'PLX-########-ABC'.
    Returns (eid_digits, valid_flag).
    """
    if not isinstance(badge, str):
        return ("", False)
    m = re.match(r"(?i)^PLX-([0-9]{1,})-([A-Za-z]{3})$", badge.strip())
    if not m:
        # Try to at least pull the digits in the middle if present
        digits = re.findall(r"([0-9]{3,})", badge.strip())
        if digits:
            return (digits[0], False)
        return ("", False)
    return (m.group(1), True)


def find_day_columns(columns: List[str]) -> Dict[str, List[str]]:
    """
    Given a list of column names, identify which map to each day.
    Returns dict day_name -> list of matching columns (case-insensitive, includes aliases).
    """
    mapping = {d: [] for d in DAY_NAMES}
    for col in columns:
        low = str(col).strip().lower()
        for alias, day in DAY_ALIASES.items():
            if re.search(rf"\b{alias}\b", low):
                mapping[day].append(col)
                break
        else:
            # also match full day names
            for day in DAY_NAMES:
                if day.lower() in low:
                    mapping[day].append(col)
                    break
    # Only keep days that actually matched something
    mapping = {k: v for k, v in mapping.items() if v}
    return mapping


# ---------------------------------
# Loaders / Normalizers for Uploads
# ---------------------------------

def load_plx(file) -> pd.DataFrame:
    """
    Load ProLogistix excel (xls/xlsx). Assumptions:
      - Row 4 contains the column headers (1-indexed), so header=3 (0-indexed)
      - Contains an EID column often labeled "File" or similar, and a Name column
      - Contains Reg Hrs split by day columns (Mon-Sun) somewhere
      - Contains OT Hrs columns (can be per-day or total). We try to detect any 'OT' columns.
    Output normalized columns:
      ['EID','Name','Reg_Hours','OT_Hours','Total_Hours']
      (Optionally: Day_* columns if we can detect per-day reg)
    """
    try:
        df = pd.read_excel(file, header=3, dtype=str)
    except Exception:
        # Fallback: try default header (row 0) if provided row fails
        file.seek(0)
        df = pd.read_excel(file, dtype=str)

    # Trim col names and rows
    df.columns = [str(c).strip() for c in df.columns]
    df = df.replace({np.nan: None})

    # Identify EID and Name
    eid_col = None
    name_col = None
    for c in df.columns:
        cl = c.lower()
        if eid_col is None and ("file" in cl or "eid" in cl or re.search(r"\bemployee\s*id\b", cl)):
            eid_col = c
        if name_col is None and ("name" in cl):
            name_col = c
    if eid_col is None:
        # Try a more aggressive guess
        for c in df.columns:
            if re.match(r"(?i)file\s*#?$", c) or re.match(r"(?i)id$", c):
                eid_col = c
                break

    # If still not found, create placeholder
    if eid_col is None:
        df["EID"] = ""
        eid_col = "EID"
    if name_col is None:
        df["Name"] = ""
        name_col = "Name"

    # Detect day columns for REG
    day_map = find_day_columns(df.columns)
    day_cols = sorted({col for cols in day_map.values() for col in cols})

    # Detect OT columns – heuristics: any column containing 'ot' or 'overtime'
    ot_candidates = [c for c in df.columns if re.search(r"(?i)\bOT\b|\bovertime\b", c)]
    # Avoid double-counting: if an OT column is clearly per-day, we can still sum them
    # Otherwise, if there's a single 'OT Hrs' total, we'll pick that too.
    # Convert numeric
    numeric_df = df.copy()
    for c in day_cols + ot_candidates:
        numeric_df[c] = numeric_df[c].apply(to_number)

    # Sum reg hours across detected day columns
    reg_hours = numeric_df[day_cols].sum(axis=1) if day_cols else 0.0

    # Sum OT hours across 'ot' candidates (works for per-day or single total)
    ot_hours = numeric_df[ot_candidates].sum(axis=1) if ot_candidates else 0.0

    norm = pd.DataFrame({
        "EID": df[eid_col].apply(normalize_eid),
        "Name": df[name_col].fillna("").astype(str).str.strip(),
        "Reg_Hours": reg_hours,
        "OT_Hours": ot_hours,
    })
    norm["Total_Hours"] = norm["Reg_Hours"].fillna(0) + norm["OT_Hours"].fillna(0)

    # Keep day columns for optional per-day overrides
    for d, cols in day_map.items():
        norm[f"Day_{d}"] = numeric_df[cols].sum(axis=1)

    # Drop empty EIDs and fully empty rows
    norm = norm[~(norm["EID"] == "") | (norm["Total_Hours"] > 0)]
    norm = norm.reset_index(drop=True)
    return norm


def load_crescent(file) -> pd.DataFrame:
    """
    Load Crescent csv/xlsx. Assumptions:
      - Columns include 'Badge', 'Payable Hours', 'Line' (case-insensitive tolerant)
      - Badge format is 'PLX-########-ABC' but we will try to parse even if malformed
      - There can be multiple rows per EID across different lines; we'll keep granular rows
    Output normalized columns:
      ['Badge','EID','EID_Valid','Last3','Line','Payable_Hours']
    """
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

    # Identify columns
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

    # Fallbacks
    if badge_col is None:
        # aggressively try
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

    # Extract EID and validity
    badges = df[badge_col].fillna("")
    eid_extracted = []
    valid_flags = []
    last3s = []
    for b in badges:
        eid_d, valid = extract_eid_from_badge(b)
        eid_extracted.append(eid_d)
        valid_flags.append(valid)
        # Extract last3 if present
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

    # Remove fully empty rows
    out = out[~((out["Badge"] == "") & (out["Payable_Hours"] == 0))]
    out = out.reset_index(drop=True)
    return out


# ----------------------
# Discrepancy Detection
# ----------------------

def summarize_plx(plx: pd.DataFrame) -> pd.DataFrame:
    """ Return per-EID totals from PLX """
    g = plx.groupby(["EID", "Name"], dropna=False, as_index=False)[["Reg_Hours","OT_Hours","Total_Hours"]].sum()
    return g


def summarize_crescent(cres: pd.DataFrame) -> pd.DataFrame:
    """ Return per-EID totals from Crescent """
    # Some rows may have empty/invalid EIDs; keep those for invalid category
    valid = cres[cres["EID"] != ""].copy()
    g = valid.groupby(["EID"], as_index=False)["Payable_Hours"].sum()
    return g


def detect_discrepancies(plx: pd.DataFrame, cres: pd.DataFrame) -> pd.DataFrame:
    plx_sum = summarize_plx(plx)
    cres_sum = summarize_crescent(cres)

    merged = plx_sum.merge(cres_sum, on="EID", how="outer", suffixes=("_PLX", "_CRES"))
    merged["Total_Hours_PLX"] = merged["Total_Hours_PLX"].fillna(0.0)
    merged["Payable_Hours"] = merged["Payable_Hours"].fillna(0.0)

    # attach one sample Line and Badge for messaging if available
    sample = cres.groupby("EID", as_index=False).agg({
        "Line": lambda s: next((x for x in s if x), ""),
        "Badge": lambda s: next((x for x in s if x), ""),
        "Name": lambda s: next((x for x in s if x), ""),
    })
    merged = merged.merge(sample, on="EID", how="left")

    # Also keep PLX Name
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
        elif plx_total != cres_total:
            cat = "Mismatched Hours"
        else:
            continue  # no discrepancy

        rows.append({
            "EID": eid,
            "Name_PLX": r.get("Name_PLX", ""),
            "Name_CRES": r.get("Name", ""),
            "Badge": r.get("Badge", ""),
            "Line": r.get("Line", ""),
            "PLX_Hours": plx_total,
            "CRES_Hours": cres_total,
            "Diff": plx_total - cres_total,
            "Category": cat,
            "DayOfWeek": "",
            "Status": "",
            "Notes": "",
        })

    # Invalid EIDs: any Crescent row with empty/malformed EID but non-zero hours
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
        # add typed columns
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
    st.header("Help")
    st.markdown("• **PLX**: Row 4 should contain headers (e.g., days of week). The EID column is often labeled **File**.")
    st.markdown("• **Crescent**: Includes **Badge**, **Payable Hours**, **Line**. Badges look like `PLX-00000000-ABC`.")

if "plx_df" not in st.session_state:
    st.session_state["plx_df"] = pd.DataFrame()
if "cres_df" not in st.session_state:
    st.session_state["cres_df"] = pd.DataFrame()
if "disc_df" not in st.session_state:
    st.session_state["disc_df"] = pd.DataFrame()

# Load & normalize
if plx_file:
    st.session_state["plx_df"] = load_plx(plx_file)

if cres_file:
    st.session_state["cres_df"] = load_crescent(cres_file)

plx_df = st.session_state["plx_df"]
cres_df = st.session_state["cres_df"]

# ---------------
# 2) Process View
# ---------------
st.header("2) Unified Data Views")

c1, c2 = st.columns(2, gap="large")

with c1:
    st.subheader("ProLogistix (Normalized)")
    if not plx_df.empty:
        st.data_editor(
            plx_df,
            key="plx_editor",
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "EID": st.column_config.TextColumn(help="Employee ID (digits only)"),
                "Name": st.column_config.TextColumn(),
                "Reg_Hours": st.column_config.NumberColumn(format="%.2f"),
                "OT_Hours": st.column_config.NumberColumn(format="%.2f"),
                "Total_Hours": st.column_config.NumberColumn(format="%.2f", help="Reg + OT", disabled=True),
            },
        )
    else:
        st.info("Upload a PLX report to view.")

with c2:
    st.subheader("Crescent (Normalized)")
    if not cres_df.empty:
        st.data_editor(
            cres_df,
            key="cres_editor",
            num_rows="dynamic",
            use_container_width=True,
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
        st.info("Upload a Crescent report to view.")

# Recompute totals on edits
if not plx_df.empty:
    edited_plx = st.session_state.get("plx_editor", plx_df).copy()
    edited_plx["EID"] = edited_plx["EID"].apply(normalize_eid)
    edited_plx["Reg_Hours"] = edited_plx["Reg_Hours"].apply(to_number)
    edited_plx["OT_Hours"] = edited_plx["OT_Hours"].apply(to_number)
    edited_plx["Total_Hours"] = edited_plx["Reg_Hours"] + edited_plx["OT_Hours"]
    st.session_state["plx_df"] = edited_plx

if not cres_df.empty:
    edited_cres = st.session_state.get("cres_editor", cres_df).copy()
    edited_cres["EID"] = edited_cres["EID"].apply(normalize_eid)
    edited_cres["Payable_Hours"] = edited_cres["Payable_Hours"].apply(to_number)
    st.session_state["cres_df"] = edited_cres

# --------------------------
# 3) Detect & Edit Issues
# --------------------------
st.header("3) Discrepancies & Resolutions")

if not st.session_state["plx_df"].empty and not st.session_state["cres_df"].empty:
    # (Re)build discrepancy table
    disc_df = detect_discrepancies(st.session_state["plx_df"], st.session_state["cres_df"])
    # merge previous statuses if any
    if not st.session_state["disc_df"].empty:
        prev = st.session_state["disc_df"][["EID","Badge","Category","Status","DayOfWeek","Notes"]].copy()
        disc_df = disc_df.merge(prev, on=["EID","Badge","Category"], how="left", suffixes=("","_prev"))
        for col in ["Status","DayOfWeek","Notes"]:
            disc_df[col] = disc_df[col].fillna(disc_df.get(f"{col}_prev"))
        drop_cols = [c for c in disc_df.columns if c.endswith("_prev")]
        disc_df.drop(columns=drop_cols, inplace=True, errors="ignore")

    st.caption("Use the dropdowns and notes to classify each discrepancy.")
    disc_editor = st.data_editor(
        disc_df,
        key="disc_editor",
        use_container_width=True,
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
    st.session_state["disc_df"] = disc_editor

    # Summary chips
    c3, c4, c5, c6 = st.columns(4)
    with c3:
        st.metric("PLX-only", int((disc_editor["Category"] == "PLX-only").sum()))
    with c4:
        st.metric("Crescent-only", int((disc_editor["Category"] == "Crescent-only").sum()))
    with c5:
        st.metric("Mismatched Hours", int((disc_editor["Category"] == "Mismatched Hours").sum()))
    with c6:
        st.metric("Invalid EID", int((disc_editor["Category"] == "Invalid EID").sum()))
else:
    st.info("Upload both files to generate discrepancies.")

# --------------------------
# 4) Validation / Totals
# --------------------------
st.header("4) Totals Validation")

def totals(plx: pd.DataFrame, cres: pd.DataFrame) -> Tuple[float, float]:
    return float(plx["Total_Hours"].sum()), float(cres["Payable_Hours"].sum())

if not st.session_state["plx_df"].empty and not st.session_state["cres_df"].empty:
    t_plx, t_cres = totals(st.session_state["plx_df"], st.session_state["cres_df"])
    cA, cB = st.columns(2)
    with cA:
        st.metric("PLX Total Hours", f"{t_plx:,.2f}")
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
    """
    Build summary lines for rows marked 'Crescent Error'.
    Template: Associate Name - Worked Line X for # (correct hours), not # (incorrect hours). [Badge]
    Logic:
      - Prefer Name_PLX; fallback to Name_CRES
      - Correct hours = PLX_Hours; Incorrect = CRES_Hours
      - If no Line, omit 'Line X'
    """
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
        use_container_width=True,
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
        use_container_width=True,
    )

# --------------------------
# Footer
# --------------------------
st.markdown("---")
st.caption("Tip: You can edit hours directly in the tables above. Use the Status column to classify each discrepancy.")
