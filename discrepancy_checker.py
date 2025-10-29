import pandas as pd
import numpy as np
import streamlit as st
from typing import Tuple

st.set_page_config(page_title="Employee Hours Discrepancy Checker", layout="wide")
st.title("Employee Hours Discrepancy Checker — Enhanced")

# -----------------------------
# Helper functions
# -----------------------------

def safe_read_prologistix(file) -> pd.DataFrame:
    """Read the ProLogistix Excel with header rows 3 & 4 and data starting row 7 (0-indexed)."""
    if file.name.endswith(".xls"):
        raw = pd.read_excel(file, header=None, engine="xlrd")
    else:
        raw = pd.read_excel(file, header=None, engine="openpyxl")

    # Extract headers from rows 3 and 4 (0-indexed)
    h1 = raw.iloc[3].fillna("").astype(str)
    h2 = raw.iloc[4].fillna("").astype(str)
    combined = [f"{a.strip()} - {b.strip()}" if a.strip() and b.strip() else (a.strip() or b.strip()) for a, b in zip(h1, h2)]

    df = raw.iloc[6:].copy()
    df.columns = combined
    return df


def safe_read_crescent(file) -> pd.DataFrame:
    """Read the Crescent report from CSV or Excel. Returns a DataFrame."""
    if file.name.endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file, engine="openpyxl")
    return df


def detect_possible_line_column(df: pd.DataFrame) -> str:
    """Try to find a reasonable 'line' or 'department' column name to use in the email text."""
    candidates = [
        "Line", "line", "Department", "department", "Dept", "dept", "Labor Dept", "Labor Department",
        "Work Area", "Area", "Cost Center", "CostCenter"
    ]
    for c in candidates:
        if c in df.columns:
            return c
    return ""


def build_comparison(
    plx_df: pd.DataFrame, cres_df: pd.DataFrame, selected_day: str
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    """
    Returns tuple of (comparison_all, plx_only, cres_only, mismatched, non_numeric_eid), plus detected line col.
    """
    # Identify canonical columns in PLX
    eid_col = "File"
    name_col = "Name"
    hours_col = f"{selected_day} - Reg Hrs"

    # Normalize PLX
    plx = plx_df.copy()
    if eid_col not in plx.columns or name_col not in plx.columns or hours_col not in plx.columns:
        missing = [c for c in [eid_col, name_col, hours_col] if c not in plx.columns]
        raise ValueError(f"Missing expected PLX columns: {missing}")

    plx["EID"] = plx[eid_col].astype(str).str.extract(r"(\d+)")
    plx["Name"] = plx[name_col].astype(str)
    plx["Excel Hours"] = pd.to_numeric(plx[hours_col], errors="coerce").fillna(0.0)

    # Collapse multiple rows per EID
    plx_grouped = (
        plx.groupby("EID").agg({"Excel Hours": "sum", "Name": "first"}).reset_index()
    )

    # Normalize Crescent
    cres = cres_df.copy()
    if "Badge" not in cres.columns:
        raise ValueError("Crescent file must contain a 'Badge' column.")
    cres["Badge"] = cres["Badge"].astype(str)
    cres["EID"] = cres["Badge"].str.extract(r"(?i)plx-(\d+)-")[0]  # Case-insensitive match for 'PLX'
    cres["Last3"] = cres["Badge"].str.extract(r"-(\w{3})$", flags=re.IGNORECASE)[0]


    if "Payable hours" not in cres.columns:
        # attempt case-insensitive / alternate naming rescue
        alt = [c for c in cres.columns if c.strip().lower() in {"payable hours", "payable_hours", "payablehrs", "payable hr", "payable"}]
        if alt:
            cres.rename(columns={alt[0]: "Payable hours"}, inplace=True)
        else:
            raise ValueError("Crescent file must contain a 'Payable hours' column.")

    cres["Payable hours"] = pd.to_numeric(cres["Payable hours"], errors="coerce").fillna(0.0)

    line_col = detect_possible_line_column(cres)

    # Non-numeric or missing EIDs in Crescent
    non_numeric_mask = cres["EID"].isna() | ~cres["EID"].astype(str).str.fullmatch(r"\d+").fillna(False)
    non_numeric_eid = cres.loc[non_numeric_mask].copy()

    # Group Crescent by numeric EID only
    cres_numeric = cres.loc[~non_numeric_mask].copy()
    cres_grouped = cres_numeric.groupby("EID").agg({
        "Payable hours": "sum",
        "Last3": "first",
        **({line_col: "first"} if line_col else {})
    }).reset_index()

    # Merge & compare
    comp = pd.merge(plx_grouped, cres_grouped, on="EID", how="outer")
    comp["Excel Hours"].fillna(0.0, inplace=True)
    comp["Payable hours"].fillna(0.0, inplace=True)
    comp["Name"].fillna("", inplace=True)
    comp["Discrepancy"] = comp["Excel Hours"] - comp["Payable hours"]

    # Categories
    plx_only = comp[(comp["Excel Hours"] > 0) & (comp["Payable hours"] == 0)].copy()
    cres_only = comp[(comp["Excel Hours"] == 0) & (comp["Payable hours"] > 0)].copy()
    mismatched = comp[(comp["Excel Hours"] > 0) & (comp["Payable hours"] > 0) & (comp["Discrepancy"] != 0)].copy()

    return comp, plx_only, cres_only, mismatched, non_numeric_eid, line_col


def add_error_scaffold(df: pd.DataFrame, source_label: str) -> pd.DataFrame:
    """Add ErrorID and review columns to a discrepancy dataframe."""
    out = df.copy().reset_index(drop=True)
    out.insert(0, "ErrorID", range(1, len(out) + 1))
    out.insert(1, "Source", source_label)
    # Review columns
    out["Action"] = "Unreviewed"
    out["MatchWith"] = ""
    out["CorrectHours"] = np.nan
    return out


def apply_corrections(df: pd.DataFrame) -> Tuple[float, float, pd.DataFrame]:
    """
    Apply row-level corrections to compute corrected totals.
    - If Action == 'Crescent Error' and CorrectHours set: override Crescent value for that row.
    - If Action == 'PLX Error' and CorrectHours set: override PLX value for that row.
    Return (corrected_plx_total, corrected_cres_total, df_with_effective_values)
    """
    work = df.copy()
    work["Excel_Effective"] = work["Excel Hours"].astype(float)
    work["Crescent_Effective"] = work["Payable hours"].astype(float)

    mask_cres = (work["Action"] == "Crescent Error") & work["CorrectHours"].notna()
    work.loc[mask_cres, "Crescent_Effective"] = work.loc[mask_cres, "CorrectHours"].astype(float)

    mask_plx = (work["Action"] == "PLX Error") & work["CorrectHours"].notna()
    work.loc[mask_plx, "Excel_Effective"] = work.loc[mask_plx, "CorrectHours"].astype(float)

    return work["Excel_Effective"].sum(), work["Crescent_Effective"].sum(), work


def build_email_lines(df: pd.DataFrame, line_col: str) -> str:
    """
    Build client-facing text lines for rows where a correction was entered (Crescent/PLX Error).
    Format:
    1. Associate Name - Worked Line X for # (correct hours), not X (incorrect hours).
       [Badge Number Here]
    """
    lines = []
    idx = 1
    for _, r in df.iterrows():
        if r["Action"] in {"Crescent Error", "PLX Error"} and pd.notna(r.get("CorrectHours", np.nan)):
            name = r.get("Name") or "(Name N/A)"
            line_val = r.get(line_col) if line_col and pd.notna(r.get(line_col)) else "N/A"
            badge = r.get("Badge") or r.get("Last3") or "N/A"
            correct = float(r["CorrectHours"]) if pd.notna(r["CorrectHours"]) else None
            if correct is None:
                continue
            if r["Action"] == "Crescent Error":
                incorrect = float(r.get("Payable hours", 0.0))
            else:
                incorrect = float(r.get("Excel Hours", 0.0))
            lines.append(
                f"{idx}. {name} - Worked Line {line_val} for {correct:g} (correct hours), not {incorrect:g} (incorrect hours).\n[{badge}]"
            )
            idx += 1
    return "\n\n".join(lines) if lines else "(No corrections entered.)"

# -----------------------------
# UI — File uploads
# -----------------------------
prologistix_file = st.file_uploader("Upload ProLogistix Report (Excel)", type=["xls", "xlsx"], key="plx")
crescent_file = st.file_uploader("Upload Crescent Report (CSV or Excel)", type=["csv", "xlsx"], key="cres")

if prologistix_file and crescent_file:
    try:
        crescent_df = safe_read_crescent(crescent_file)
        plx_df = safe_read_prologistix(prologistix_file)

        # Day selector
        st.subheader("Select Day of Week to Compare")
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        selected_day = st.selectbox("Day of Week", options=days, index=0)

        comp, plx_only, cres_only, mismatched, non_numeric_eid, line_col = build_comparison(
            plx_df, crescent_df, selected_day
        )

        # Prepare review tables
        plx_only_r = add_error_scaffold(plx_only, "PLX discrepancy (hours only on PLX)")
        cres_only_r = add_error_scaffold(cres_only, "Crescent discrepancy (hours only on Crescent)")
        mismatched_r = add_error_scaffold(mismatched, "Mismatch (both have hours, values differ)")

        # Non-numeric EID rows from Crescent — build a compact table
        non_numeric_view = non_numeric_eid.copy().reset_index(drop=True)
        non_numeric_view.insert(0, "ErrorID", range(1, len(non_numeric_view) + 1))
        non_numeric_view.insert(1, "Source", "Crescent row has non-numeric/missing EID")
        non_numeric_view["Action"] = "Unreviewed"
        non_numeric_view["MatchWith"] = ""
        non_numeric_view["CorrectHours"] = np.nan

        # Merge review tables into one for unified editing context
        review_df = pd.concat(
            [plx_only_r, cres_only_r, mismatched_r, non_numeric_view], ignore_index=True, sort=False
        )

        # Keep original badge/line for email text if present
        if "Badge" not in review_df.columns and "Badge" in crescent_df.columns:
            review_df = review_df.merge(
                crescent_df[["Badge", "EID"]], on="EID", how="left"
            )

        # Persist edits in session state
        if "review_df" not in st.session_state:
            st.session_state.review_df = review_df
        else:
            # When day or files change, reset
            # Detect shape/content change by basic heuristic
            if len(st.session_state.review_df) != len(review_df):
                st.session_state.review_df = review_df

        st.markdown("## Discrepancy Buckets")
        c1, c2, c3 = st.columns(3)
        c1.metric("PLX-only discrepancies", len(plx_only_r))
        c2.metric("Crescent-only discrepancies", len(cres_only_r))
        c3.metric("Mismatched (both sides)", len(mismatched_r))
        st.caption(
            "Rows below also include any Crescent lines with non-numeric/missing EIDs. Use the 'Action' dropdown to review."
        )

        # Editable grid
        st.markdown("### Review & Tag Each Discrepancy")
        edited = st.data_editor(
            st.session_state.review_df,
            num_rows="fixed",
            use_container_width=True,
            column_config={
                "Action": st.column_config.SelectboxColumn(
                    "Action",
                    help="Choose how to classify this discrepancy.",
                    options=["Unreviewed", "EID Match", "Crescent Error", "PLX Error"],
                    required=True,
                ),
                "CorrectHours": st.column_config.NumberColumn(
                    "Correct Hours",
                    help="If Crescent/PLX Error, enter the corrected hours.",
                    step=0.25,
                    format="%g",
                    min_value=0.0,
                ),
                "MatchWith": st.column_config.TextColumn(
                    "Match With (ErrorID)",
                    help="If 'EID Match', enter the ErrorID of the related row.",
                ),
            },
            hide_index=True,
        )

        st.session_state.review_df = edited

        # Display quick filters/tables for clarity
        with st.expander("View by bucket (read-only views)", expanded=False):
            st.write("**PLX-only**")
            st.dataframe(plx_only_r, use_container_width=True)
            st.write("**Crescent-only**")
            st.dataframe(cres_only_r, use_container_width=True)
            st.write("**Mismatched**")
            st.dataframe(mismatched_r, use_container_width=True)
            st.write("**Crescent rows with non-numeric/missing EID**")
            st.dataframe(non_numeric_view, use_container_width=True)

        # -----------------------------
        # File Validation Block
        # -----------------------------
        st.markdown("## File Validation")
        st.caption(
            "Click **Validate & Summarize** after you've tagged each row and entered corrections where needed."
        )
        if st.button("Validate & Summarize", type="primary"):
            # Compute corrected totals
            corr_plx_total, corr_cres_total, effective = apply_corrections(st.session_state.review_df)

            # Show totals
            tcol1, tcol2, tcol3 = st.columns([1,1,1])
            tcol1.metric("PLX total (original)", f"{st.session_state.review_df['Excel Hours'].fillna(0).sum():g}")
            tcol2.metric("Crescent total (original)", f"{st.session_state.review_df['Payable hours'].fillna(0).sum():g}")
            match_status = "MATCH" if abs(corr_plx_total - corr_cres_total) < 1e-6 else "MISMATCH"
            tcol3.metric("Status after corrections", match_status)

            st.write("**PLX total (corrected):** ", f"{corr_plx_total:g}")
            st.write("**Crescent total (corrected):** ", f"{corr_cres_total:g}")

            # Build client-facing email lines
            # Merge in any original Crescent columns we might need (Badge, line_col)
            eff_for_email = effective.copy()
            if line_col and line_col not in eff_for_email.columns and line_col in crescent_df.columns:
                eff_for_email = eff_for_email.merge(crescent_df[["EID", line_col]], on="EID", how="left")
            if "Badge" not in eff_for_email.columns and "Badge" in crescent_df.columns:
                eff_for_email = eff_for_email.merge(crescent_df[["EID", "Badge"]], on="EID", how="left")

            email_text = build_email_lines(eff_for_email, line_col)
            st.markdown("### Client Email Summary (copy/paste)")
            st.code(email_text)

            # Downloads
            csv_bytes = effective.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="Download Corrected Discrepancy Table (CSV)",
                data=csv_bytes,
                file_name="discrepancies_corrected.csv",
                mime="text/csv",
            )

            st.download_button(
                label="Download Client Email Text (.txt)",
                data=email_text.encode("utf-8"),
                file_name="client_summary.txt",
                mime="text/plain",
            )

        with st.expander("Advanced: Raw Comparison Table"):
            st.dataframe(comp, use_container_width=True)

    except Exception as e:
        st.error(f"Error processing files: {e}")
else:
    st.info("Upload both files to begin.")
