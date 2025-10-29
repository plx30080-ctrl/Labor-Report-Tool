import pandas as pd
import streamlit as st

st.title("Employee Hours Discrepancy Checker")

# Upload files
excel_file = st.file_uploader("Upload Excel File (.xlsx only)", type=["xlsx"])
csv_file = st.file_uploader("Upload CSV File", type=["csv"])

if excel_file and csv_file:
    try:
        # Read Excel file using openpyxl engine
        excel_df = pd.read_excel(excel_file, engine='openpyxl', skiprows=6)
        csv_df = pd.read_csv(csv_file)

        st.subheader("Select Columns to Compare")

        excel_eid_col = st.selectbox("Excel EID Column", options=excel_df.columns)
        excel_hours_col = st.selectbox("Excel Hours Column", options=excel_df.columns)

        csv_badge_col = st.selectbox("CSV Badge Column", options=csv_df.columns)
        csv_hours_col = st.selectbox("CSV Hours Column", options=csv_df.columns)

        if st.button("Compare Files"):
            # Extract EID from badge
            csv_df["EID"] = csv_df[csv_badge_col].astype(str).str.extract(r'(\d+)')
            csv_df_grouped = csv_df.groupby("EID")[csv_hours_col].sum().reset_index()

            # Prepare Excel data
            excel_df["EID"] = excel_df[excel_eid_col].astype(str)
            excel_df_grouped = excel_df.groupby("EID")[excel_hours_col].sum().reset_index()

            # Merge and compare
            comparison_df = pd.merge(excel_df_grouped, csv_df_grouped, on="EID", how="outer", suffixes=("_Excel", "_CSV"))
            comparison_df.fillna(0, inplace=True)
            comparison_df["Discrepancy"] = comparison_df[f"{excel_hours_col}_Excel"] - comparison_df[csv_hours_col]

            unmatched = comparison_df[(comparison_df[f"{excel_hours_col}_Excel"] == 0) | (comparison_df[csv_hours_col] == 0)]
            mismatched = comparison_df[(comparison_df["Discrepancy"] != 0) & (comparison_df[f"{excel_hours_col}_Excel"] != 0) & (comparison_df[csv_hours_col] != 0)]

            result_df = pd.concat([unmatched, mismatched]).drop_duplicates()

            st.subheader("Discrepancy Report")
            st.dataframe(result_df)

            # Download
            st.download_button(
                label="Download Discrepancy Report",
                data=result_df.to_csv(index=False).encode('utf-8'),
                file_name="discrepancy_report.csv",
                mime="text/csv"
            )

    except Exception as e:
        st.error(f"Error processing files: {e}")
