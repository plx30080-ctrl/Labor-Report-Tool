import streamlit as st
import pandas as pd
import re
from io import BytesIO

st.title("Discrepancy Checker: ProLogistix vs Crescent Report")

# Upload files
plx_file = st.file_uploader("Upload ProLogistix Report (.xls or .xlsx)", type=["xls", "xlsx"])
crescent_file = st.file_uploader("Upload Crescent Report (.csv or .xlsx)", type=["csv", "xlsx"])

if plx_file and crescent_file:
    try:
        # Load Crescent Report
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

        # Load ProLogistix Report
        if plx_file.name.endswith(".xls"):
            raw_plx = pd.read_excel(plx_file, header=None, engine="xlrd")
        else:
            raw_plx = pd.read_excel(plx_file, header=None, engine="openpyxl")

        # Extract headers from rows 4 and 5
        header_row_1 = raw_plx.iloc[3].fillna("")
        header_row_2 = raw_plx.iloc[4].fillna("")
        combined_headers = [f"{a.strip()} - {b.strip()}" if b else a.strip() for a, b in zip(header_row_1, header_row_2)]
        plx_df = raw_plx[5:].copy()
        plx_df.columns = combined_headers

        # Select day of week
        day_options = [h for h in combined_headers if "Reg Hrs" in h and any(d in h for d in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])]
        selected_day = st.selectbox("Select Day of Week to Compare", options=day_options)

        # Prepare PLX data
        plx_df["EID"] = plx_df["File"].astype(str)
        plx_df["Name"] = plx_df["Name"].astype(str)
        plx_df[selected_day] = pd.to_numeric(plx_df[selected_day], errors="coerce")
        plx_hours = plx_df.groupby("EID")[[selected_day]].sum().reset_index()
        plx_hours.rename(columns={selected_day: "PLX Hours"}, inplace=True)

        # Merge and compare
        merged = pd.merge(plx_hours, crescent_hours, on="EID", how="outer")
        merged.rename(columns={"Payable hours": "Crescent Hours"}, inplace=True)
        merged["PLX Hours"] = merged["PLX Hours"].fillna(0)
        merged["Crescent Hours"] = merged["Crescent Hours"].fillna(0)
        merged["Discrepancy"] = merged["PLX Hours"] - merged["Crescent Hours"]

        # Add name and badge info
        name_map = plx_df[["EID", "Name"]].drop_duplicates()
        merged = pd.merge(merged, name_map, on="EID", how="left")
        badge_map = crescent_df[["EID", "Badge", "Last3"]].drop_duplicates()
        merged = pd.merge(merged, badge_map, on="EID", how="left")

        # Categorize discrepancies
        plx_only = merged[(merged["PLX Hours"] > 0) & (merged["Crescent Hours"] == 0)]
        crescent_only = merged[(merged["PLX Hours"] == 0) & (merged["Crescent Hours"] > 0)]
        mismatched = merged[(merged["PLX Hours"] != merged["Crescent Hours"]) & (merged["PLX Hours"] > 0) & (merged["Crescent Hours"] > 0)]
        invalid_eid = crescent_df[crescent_df["EID"].isna()]

        # Display match count
        matched_count = merged[(merged["Discrepancy"] == 0) & (merged["PLX Hours"] > 0)].shape[0]
        st.success(f"✅ {matched_count} associates match both files with no discrepancies.")

        # Display categorized discrepancies
        st.subheader("PLX Discrepancies (Missing in Crescent)")
        st.dataframe(plx_only)

        st.subheader("Crescent Discrepancies (Missing in PLX)")
        st.dataframe(crescent_only)

        st.subheader("Mismatched Hours")
        st.dataframe(mismatched)

        st.subheader("Invalid EIDs in Crescent Report")
        st.dataframe(invalid_eid)

        # Combine all discrepancies
        all_discrepancies = pd.concat([plx_only, crescent_only, mismatched], ignore_index=True)
        all_discrepancies = all_discrepancies.reset_index(drop=True)
        all_discrepancies["Error #"] = all_discrepancies.index + 1

        # Dropdowns for error resolution
        st.subheader("Resolve Discrepancies")
        resolution_data = []
        for i, row in all_discrepancies.iterrows():
            st.markdown(f"**Error {row['Error #']} - {row['Name']} ({row['EID']})**")
            option = st.selectbox(f"Resolution for Error {row['Error #']}", ["", "EID Match", "Crescent Error", "PLX Error"], key=f"res_{i}")
            correction = st.text_input(f"Correction for Error {row['Error #']} (if applicable)", key=f"corr_{i}")
            match_error = st.text_input(f"Match Error # (if applicable)", key=f"match_{i}")
            resolution_data.append({
                "Error #": row["Error #"],
                "EID": row["EID"],
                "Name": row["Name"],
                "Badge": row["Badge"],
                "PLX Hours": row["PLX Hours"],
                "Crescent Hours": row["Crescent Hours"],
                "Discrepancy": row["Discrepancy"],
                "Resolution": option,
                "Correction": correction,
                "Match Error #": match_error
            })

        # File validation
        if st.button("Validate Files"):
            corrected_df = pd.DataFrame(resolution_data)
            corrected_df["Corrected PLX"] = corrected_df.apply(lambda x: float(x["Correction"]) if x["Resolution"] == "PLX Error" and x["Correction"] else x["PLX Hours"], axis=1)
            corrected_df["Corrected Crescent"] = corrected_df.apply(lambda x: float(x["Correction"]) if x["Resolution"] == "Crescent Error" and x["Correction"] else x["Crescent Hours"], axis=1)

            total_plx = corrected_df["Corrected PLX"].sum()
            total_crescent = corrected_df["Corrected Crescent"].sum()

            st.subheader("Validation Summary")
            st.write(f"Total PLX Hours: {total_plx}")
            st.write(f"Total Crescent Hours: {total_crescent}")

            if abs(total_plx - total_crescent) < 0.01:
                st.success("✅ Files validated successfully. Total hours match.")
            else:
                st.error("❌ Files do not match. Please review corrections.")

            # Generate client summary
            st.subheader("Client Summary")
            summary_lines = []
            for _, row in corrected_df.iterrows():
                if row["Resolution"] in ["PLX Error", "Crescent Error"]:
                    correct = row["Correction"]
                    incorrect = row["PLX Hours"] if row["Resolution"] == "PLX Error" else row["Crescent Hours"]
                    summary = f"{row['Name']} - Worked Line X for {correct} (correct hours), not {incorrect} (incorrect hours). [{row['Badge']}]"
                    summary_lines.append(summary)
            summary_text = "\n".join(summary_lines)
            st.text_area("Summary to Email Client", value=summary_text, height=300)

    except Exception as e:
        st.error(f"Error processing files: {e}")
