import pandas as pd
import streamlit as st
import re

st.set_page_config(page_title="Discrepancy Checker", layout="wide")
st.title("Employee Hours Discrepancy Checker")

# Upload files
plx_file = st.file_uploader("Upload ProLogistix Report (.xls or .xlsx)", type=["xls", "xlsx"])
crescent_file = st.file_uploader("Upload Crescent Report (.csv or .xlsx)", type=["csv", "xlsx"])

if plx_file and crescent_file:
    try:
        # Read Crescent file
        if crescent_file.name.endswith(".csv"):
            crescent_df = pd.read_csv(crescent_file)
        else:
            crescent_df = pd.read_excel(crescent_file, engine="openpyxl")

        # Extract EID and Last3 from Badge
        crescent_df["Badge"] = crescent_df["Badge"].astype(str)
        crescent_df["EID"] = crescent_df["Badge"].str.extract(r'PLX-(\d+)-')
        crescent_df["Last3"] = crescent_df["Badge"].str.extract(r'-(\w{3})$')
        crescent_df["Payable hours"] = pd.to_numeric(crescent_df["Payable hours"], errors="coerce")
        crescent_hours = crescent_df.groupby("EID")["Payable hours"].sum().reset_index()

        # Read ProLogistix file
        if plx_file.name.endswith(".xls"):
            raw_excel = pd.read_excel(plx_file, engine="xlrd", header=None)
        else:
            raw_excel = pd.read_excel(plx_file, engine="openpyxl", header=None)

        # Detect header row and clean column names
        header_row_index = 4
        headers = raw_excel.iloc[header_row_index].fillna("").astype(str).str.strip()
        data_df = raw_excel.iloc[header_row_index + 2:].copy()
        data_df.columns = headers
        data_df.columns = data_df.columns.str.strip()

        # Identify EID and Name columns
        eid_column = next((col for col in data_df.columns if data_df[col].astype(str).str.match(r'^\d{7,9}$').sum() > 5), None)
        name_column = next((col for col in data_df.columns if data_df[col].astype(str).str.contains(",").sum() > 5), None)

        # Day-of-week selection
        day_options = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        selected_day = st.selectbox("Select Day of Week", options=day_options)

        # Find Reg Hrs column for selected day
        reg_hrs_column = next((col for col in data_df.columns if selected_day in col and "Reg Hrs" in col), None)

        if eid_column and name_column and reg_hrs_column:
            data_df["EID"] = data_df[eid_column].astype(str).str.extract(r'(\d{7,9})')
            data_df["Name"] = data_df[name_column].astype(str).str.strip()
            data_df["Reg Hrs"] = pd.to_numeric(data_df[reg_hrs_column], errors="coerce")
            plx_hours = data_df.groupby(["EID", "Name"])["Reg Hrs"].sum().reset_index()

            # Merge and compare
            merged_df = pd.merge(plx_hours, crescent_hours, on="EID", how="outer", suffixes=("_PLX", "_Crescent"))
            merged_df["Reg Hrs"] = merged_df["Reg Hrs"].fillna(0)
            merged_df["Payable hours"] = merged_df["Payable hours"].fillna(0)
            merged_df["Discrepancy"] = merged_df["Reg Hrs"] - merged_df["Payable hours"]

            # Add Last3 from Crescent
            crescent_last3 = crescent_df[["EID", "Last3"]].drop_duplicates()
            merged_df = pd.merge(merged_df, crescent_last3, on="EID", how="left")

            # Categorize discrepancies
            plx_only = merged_df[(merged_df["Reg Hrs"] > 0) & (merged_df["Payable hours"] == 0)]
            crescent_only = merged_df[(merged_df["Reg Hrs"] == 0) & (merged_df["Payable hours"] > 0)]
            mismatched = merged_df[(merged_df["Reg Hrs"] > 0) & (merged_df["Payable hours"] > 0) & (merged_df["Discrepancy"] != 0)]
            invalid_eid = crescent_df[crescent_df["EID"].isna()]

            # Display results
            st.subheader("PLX Discrepancies (Missing in Crescent)")
            st.dataframe(plx_only)

            st.subheader("Crescent Discrepancies (Missing in PLX)")
            st.dataframe(crescent_only)

            st.subheader("Mismatched Hours")
            st.dataframe(mismatched)

            st.subheader("Invalid EIDs in Crescent Report")
            st.dataframe(invalid_eid)

            # Summary
            matched_count = merged_df[(merged_df["Discrepancy"] == 0) & (merged_df["Reg Hrs"] > 0)].shape[0]
            st.success(f"✅ {matched_count} associates match both files with no discrepancies.")

        else:
            st.error("Could not detect EID, Name, or Reg Hrs column. Please check the file format.")

    except Exception as e:
        st.error(f"Error processing files: {e}")
