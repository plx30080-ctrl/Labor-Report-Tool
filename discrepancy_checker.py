import pandas as pd
import streamlit as st
import re
from io import BytesIO

st.set_page_config(page_title="Labor Report Discrepancy Checker", layout="wide")
st.title("📊 Labor Report Discrepancy Checker")

st.markdown("""
This app compares two labor reports:
- **ProLogistix Report** (Excel: `.xls` or `.xlsx`)
- **Crescent Report** (CSV or Excel)

It identifies discrepancies in hours worked and allows you to resolve and validate them.
""")

# Upload files
plx_file = st.file_uploader("Upload ProLogistix Report (.xls or .xlsx)", type=["xls", "xlsx"])
crescent_file = st.file_uploader("Upload Crescent Report (.csv or .xlsx)", type=["csv", "xlsx"])

if plx_file and crescent_file:
    try:
        # --- Load Crescent report
        if crescent_file.name.lower().endswith(".csv"):
            crescent_df = pd.read_csv(crescent_file)
        else:
            crescent_df = pd.read_excel(crescent_file)  # let pandas choose engine

        # --- Load ProLogistix report (first pass to find header)
        # Read once into raw df
        if plx_file.name.lower().endswith(".xls"):
            # xlrd is required for old .xls; if not installed this will error
            raw_plx_df = pd.read_excel(plx_file, engine="xlrd", header=None)
        else:
            raw_plx_df = pd.read_excel(plx_file, header=None)

        # Detect header row in ProLogistix report
        header_row_index = None
        for i in range(len(raw_plx_df)):
            row = raw_plx_df.iloc[i].astype(str)
            if row.str.contains("Reg Hrs", case=False, regex=False).any():
                header_row_index = i
                break

        if header_row_index is None:
            st.error("Could not detect header row in ProLogistix report (no column contains 'Reg Hrs').")
            st.stop()

        # IMPORTANT: rewind the file before reading again
        plx_file.seek(0)

        # Second pass with detected header
        if plx_file.name.lower().endswith(".xls"):
            plx_df = pd.read_excel(plx_file, engine="xlrd", header=header_row_index)
        else:
            plx_df = pd.read_excel(plx_file, header=header_row_index)

        # Normalize column names
        plx_df.columns = plx_df.columns.astype(str).str.strip()

        st.subheader("🔧 Column Selection")
        eid_col = st.selectbox("Select EID column from ProLogistix", options=list(plx_df.columns))
        name_col = st.selectbox("Select Name column from ProLogistix", options=list(plx_df.columns))
        reg_hrs_col = st.selectbox("Select Reg Hrs column from ProLogistix", options=list(plx_df.columns))

        badge_col = st.selectbox("Select Badge column from Crescent", options=list(crescent_df.columns))
        crescent_hours_col = st.selectbox("Select Payable Hours column from Crescent", options=list(crescent_df.columns))

        if st.button("Compare Reports"):
            # --- Prepare ProLogistix data
            # Use expand=False so we get a 1-D Series (prevents the 2-D/arg error)
            plx_df["EID"] = plx_df[eid_col].astype(str).str.extract(r"(\d{7,9})", expand=False)
            plx_df["Name"] = plx_df[name_col].astype(str).str.strip()
            plx_df["PLX Hours"] = pd.to_numeric(plx_df[reg_hrs_col], errors="coerce").fillna(0.0)

            plx_summary = (
                plx_df.groupby("EID", dropna=False)
                      .agg(**{"PLX Hours": ("PLX Hours", "sum"), "Name": ("Name", "first")})
                      .reset_index()
            )

            # --- Prepare Crescent data
            crescent_df["EID"] = crescent_df[badge_col].astype(str).str.extract(r"(\d{7,9})", expand=False)
            crescent_df["Last3"] = crescent_df[badge_col].astype(str).str.extract(r"-(\w{3})$", expand=False)
            crescent_df["Badge"] = crescent_df[badge_col].astype(str)
            crescent_df["Crescent Hours"] = pd.to_numeric(crescent_df[crescent_hours_col], errors="coerce").fillna(0.0)

            crescent_summary = (
                crescent_df.groupby("EID", dropna=False)
                           .agg(**{"Crescent Hours": ("Crescent Hours", "sum"),
                                   "Last3": ("Last3", "first"),
                                   "Badge": ("Badge", "first")})
                           .reset_index()
            )

            # --- Merge and compare
            merged = pd.merge(plx_summary, crescent_summary, on="EID", how="outer")
            for col in ["PLX Hours", "Crescent Hours"]:
                if col not in merged:
                    merged[col] = pd.NA
            merged["Discrepancy"] = merged["PLX Hours"].fillna(0) - merged["Crescent Hours"].fillna(0)
            merged["Error Type"] = ""
            merged["Correction"] = pd.NA
            merged["]()
