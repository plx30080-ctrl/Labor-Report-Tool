import pandas as pd
import streamlit as st

st.title("Employee Hours Discrepancy Checker")

# Upload files
excel_file = st.file_uploader("Upload Excel File (.xls or .xlsx)", type=["xls", "xlsx"])
csv_file = st.file_uploader("Upload CSV File", type=["csv"])

def load_excel_with_headers(file):
    # Read the file with xlrd engine for .xls
    df_raw = pd.read_excel(file, header=None, engine='xlrd')
    # Extract headers from row 4 (index 3) and row 5 (index 4)
    day_row = df_raw.iloc[3]
    header_row = df_raw.iloc[4]
    # Combine day and header to create multi-level column names
    combined_headers = []
    for day, header in zip(day_row, header_row):
        if pd.notna(day):
            combined_headers.append(f"{day.strip()} - {header.strip()}" if pd.notna(header) else day.strip())
        else:
            combined_headers.append(header.strip() if pd.notna(header) else "")
    # Read the actual data starting from row 6
    df_data = df_raw.iloc[6:].copy()
    df_data.columns = combined_headers
    return df_data, combined_headers

if excel_file and csv_file:
    try:
        excel_df, excel_headers = load_excel_with_headers(excel_file)
        csv_df = pd.read_csv(csv_file)

        st.subheader("Select Columns to Compare")

        # Select day of the week
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        selected_day = st.selectbox("Select Day of Week", options=days)

        # Filter Excel columns for selected day and Reg Hrs
        day_reg_columns = [col for col in excel_headers if selected_day in col and "Reg Hrs" in col]
        excel_eid_col = st.selectbox("Excel EID Column", options=[col for col in excel_headers if "File" in col])
        excel_hours_col = st.selectbox("Excel Hours Column", options=day_reg_columns)

        csv_badge_col = st.selectbox("CSV Badge Column", options=csv_df.columns)
        csv_hours_col = st.selectbox("CSV Hours Column", options=csv_df.columns)

        if st.button("Compare Files"):
            # Extract EID from badge
            csv_df["EID"] = csv_df[csv_badge_col].str.extract(r'(\d+)')
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
