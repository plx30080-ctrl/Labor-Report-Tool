import pandas as pd
import streamlit as st

st.title("Employee Hours Discrepancy Checker")

# Upload files
prologistix_file = st.file_uploader("Upload ProLogistix Report (Excel)", type=["xls", "xlsx"])
crescent_file = st.file_uploader("Upload Crescent Report (CSV or Excel)", type=["csv", "xlsx"])

if prologistix_file and crescent_file:
    try:
        # Read Crescent Report
        if crescent_file.name.endswith(".csv"):
            crescent_df = pd.read_csv(crescent_file)
        else:
            crescent_df = pd.read_excel(crescent_file, engine="openpyxl")

        # Read ProLogistix Report and extract headers from rows 3 and 4
        if prologistix_file.name.endswith(".xls"):
            raw_excel = pd.read_excel(prologistix_file, header=None, engine="xlrd")
        else:
            raw_excel = pd.read_excel(prologistix_file, header=None, engine="openpyxl")

        # Extract headers from rows 3 and 4
        header_row_1 = raw_excel.iloc[3].fillna("")
        header_row_2 = raw_excel.iloc[4].fillna("")
        combined_headers = [f"{day.strip()} - {label.strip()}" if day and label else label.strip()
                            for day, label in zip(header_row_1, header_row_2)]
        data_df = raw_excel.iloc[6:].copy()
        data_df.columns = combined_headers

        # Select day of week
        st.subheader("Select Day of Week to Compare")
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        selected_day = st.selectbox("Day of Week", options=days)

        # Identify columns
        eid_col = "File"
        name_col = "Name"
        hours_col = f"{selected_day} - Reg Hrs"

        # Extract EID and Name from ProLogistix
        data_df["EID"] = data_df[eid_col].astype(str)
        data_df["Name"] = data_df[name_col]
        data_df["Excel Hours"] = pd.to_numeric(data_df[hours_col], errors="coerce").fillna(0)

        # Extract EID and Last3 from Crescent badge
        crescent_df["EID"] = crescent_df["Badge"].str.extract(r'PLX-(\d+)-')[0]
        crescent_df["Last3"] = crescent_df["Badge"].str.extract(r'-(\w{3})$')[0]
        crescent_df["Payable hours"] = pd.to_numeric(crescent_df["Payable hours"], errors="coerce").fillna(0)

        # Group Crescent data
        crescent_grouped = crescent_df.groupby("EID").agg({
            "Payable hours": "sum",
            "Last3": "first"
        }).reset_index()

        # Group ProLogistix data
        prologistix_grouped = data_df.groupby("EID").agg({
            "Excel Hours": "sum",
            "Name": "first"
        }).reset_index()

        # Merge and compare
        comparison_df = pd.merge(prologistix_grouped, crescent_grouped, on="EID", how="outer")
        comparison_df.fillna({"Excel Hours": 0, "Payable hours": 0}, inplace=True)
        comparison_df["Discrepancy"] = comparison_df["Excel Hours"] - comparison_df["Payable hours"]

        # Identify matches and discrepancies
        matched = comparison_df[(comparison_df["Discrepancy"] == 0) & (comparison_df["Excel Hours"] != 0)]
        discrepancies = comparison_df[comparison_df["Discrepancy"] != 0]

        st.subheader("Discrepancy Report")
        st.write(f"✅ {len(matched)} associates match both files with no discrepancies.")
        st.dataframe(discrepancies)

        # Download button
        st.download_button(
            label="Download Discrepancy Report",
            data=discrepancies.to_csv(index=False).encode("utf-8"),
            file_name="discrepancy_report.csv",
            mime="text/csv"
        )

    except Exception as e:
        st.error(f"Error processing files: {e}")
