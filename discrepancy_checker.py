import pandas as pd
import streamlit as st

st.title("Employee Hours Discrepancy Checker")

# Upload files
excel_file = st.file_uploader("Upload Excel File (.xls or .xlsx)", type=["xls", "xlsx"])
csv_file = st.file_uploader("Upload CSV File", type=["csv"])

if excel_file and csv_file:
    try:
        # Read Excel file with appropriate engine
        if excel_file.name.endswith('.xls'):
            excel_df_raw = pd.read_excel(excel_file, engine='xlrd', header=None)
        else:
            excel_df_raw = pd.read_excel(excel_file, engine='openpyxl', header=None)

        # Extract header rows
        day_row = excel_df_raw.iloc[3]
        label_row = excel_df_raw.iloc[4]

        # Combine day and label to create meaningful column names
        combined_headers = []
        for day, label in zip(day_row, label_row):
            if pd.isna(label):
                combined_headers.append("")
            elif pd.isna(day):
                combined_headers.append(label.strip())
            else:
                combined_headers.append(f"{day.strip()} - {label.strip()}")

        excel_df_raw.columns = combined_headers
        excel_df = excel_df_raw.iloc[6:].copy()
        excel_df.reset_index(drop=True, inplace=True)

        # Read CSV file
        csv_df = pd.read_csv(csv_file)

        st.subheader("Select Columns to Compare")

        # Select day of week for Excel hours
        day_options = [col for col in combined_headers if "Reg Hrs" in col]
        selected_day = st.selectbox("Select Day of Week (Excel)", options=day_options)

        # Select EID column from Excel
        excel_eid_col = st.selectbox("Excel EID Column", options=excel_df.columns)

        # Select Badge and Hours column from CSV
        csv_badge_col = st.selectbox("CSV Badge Column", options=csv_df.columns)
        csv_hours_col = st.selectbox("CSV Hours Column", options=csv_df.columns)

        if st.button("Compare Files"):
            # Extract EID and last name prefix from badge
            csv_df["EID"] = csv_df[csv_badge_col].str.extract(r'[Pp][Ll][Xx]-(\d+)-')[0]
            csv_df["Last3"] = csv_df[csv_badge_col].str.extract(r'-(\w{3})$')[0]
            csv_df_grouped = csv_df.groupby("EID")[csv_hours_col].sum().reset_index()
            csv_df_grouped["Last3"] = csv_df.groupby("EID")["Last3"].first().values

            # Prepare Excel data
            excel_df["EID"] = excel_df[excel_eid_col].astype(str).str.extract(r'(\d+)')[0]
            excel_df_grouped = excel_df.groupby("EID")[selected_day].sum().reset_index()

            # Rename columns for merge
            excel_df_grouped.rename(columns={selected_day: "Excel Hours"}, inplace=True)
            csv_df_grouped.rename(columns={csv_hours_col: "CSV Hours"}, inplace=True)

            # Merge and compare
            comparison_df = pd.merge(excel_df_grouped, csv_df_grouped, on="EID", how="outer")
            comparison_df.fillna(0, inplace=True)
            comparison_df["Discrepancy"] = comparison_df["Excel Hours"] - comparison_df["CSV Hours"]

            unmatched = comparison_df[(comparison_df["Excel Hours"] == 0) | (comparison_df["CSV Hours"] == 0)]
            mismatched = comparison_df[(comparison_df["Discrepancy"] != 0) & (comparison_df["Excel Hours"] != 0) & (comparison_df["CSV Hours"] != 0)]

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
