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
        # Build re-readable buffers so we can read files multiple times safely
        plx_buf = BytesIO(plx_file.read())
        crescent_buf = BytesIO(crescent_file.read())

        # Load Crescent report
        if crescent_file.name.lower().endswith(".csv"):
            crescent_df = pd.read_csv(crescent_buf)
        else:
            crescent_df = pd.read_excel(crescent_buf)

        # Load ProLogistix report (raw, no header) to detect header row
        raw_plx_df = pd.read_excel(plx_buf, header=None)

        # Detect header row in ProLogistix report
        header_row_index = None
        for i in range(len(raw_plx_df)):
            row_has_reg = raw_plx_df.iloc[i].astype(str).str.contains("Reg Hrs", case=False, na=False).any()
            if row_has_reg:
                header_row_index = i
                break

        if header_row_index is None:
            st.error("Could not detect header row in ProLogistix report.")
        else:
            # Rewind and re-read PLX with the detected header
            plx_buf.seek(0)
            plx_df = pd.read_excel(plx_buf, header=header_row_index)
            plx_df.columns = plx_df.columns.astype(str).str.strip()

            st.subheader("🔧 Column Selection")

            eid_col = st.selectbox("Select EID column from ProLogistix", options=list(plx_df.columns))
            name_col = st.selectbox("Select Name column from ProLogistix", options=list(plx_df.columns))
            reg_hrs_col = st.selectbox("Select Reg Hrs column from ProLogistix", options=list(plx_df.columns))

            badge_col = st.selectbox("Select Badge column from Crescent", options=list(crescent_df.columns))
            crescent_hours_col = st.selectbox("Select Payable Hours column from Crescent", options=list(crescent_df.columns))

            if st.button("Compare Reports"):
                # Prepare ProLogistix data
                plx_df["EID"] = plx_df[eid_col].astype(str).str.extract(r"(\d{7,9})", expand=False)
                plx_df["Name"] = plx_df[name_col].astype(str)
                plx_df["PLX Hours"] = pd.to_numeric(plx_df[reg_hrs_col], errors="coerce").fillna(0)
                plx_summary = (
                    plx_df.groupby("EID", dropna=False)
                          .agg({"PLX Hours": "sum", "Name": "first"})
                          .reset_index()
                )

                # Prepare Crescent data
                crescent_df["EID"] = crescent_df[badge_col].astype(str).str.extract(r"(\d{7,9})", expand=False)
                crescent_df["Last3"] = crescent_df[badge_col].astype(str).str.extract(r"-(\w{3})$", expand=False)
                crescent_df["Badge"] = crescent_df[badge_col]
                crescent_df["Crescent Hours"] = pd.to_numeric(crescent_df[crescent_hours_col], errors="coerce").fillna(0)
                crescent_summary = (
                    crescent_df.groupby("EID", dropna=False)
                               .agg({"Crescent Hours": "sum", "Last3": "first", "Badge": "first"})
                               .reset_index()
                )

                # Merge and compare
                merged = pd.merge(plx_summary, crescent_summary, on="EID", how="outer")
                merged["Discrepancy"] = merged["PLX Hours"].fillna(0) - merged["Crescent Hours"].fillna(0)
                merged["Error Type"] = ""
                merged["Correction"] = 0.0
                merged["Error #"] = ""

                # Categorize
                plx_only = merged[merged["Crescent Hours"].isna()]
                crescent_only = merged[merged["PLX Hours"].isna()]
                mismatched = merged[
                    merged["PLX Hours"].notna() & merged["Crescent Hours"].notna() & (merged["Discrepancy"] != 0)
                ]
                invalid_eid = crescent_df[crescent_df["EID"].isna()]

                st.subheader("📌 Discrepancy Summary")
                matches = len(merged) - len(plx_only) - len(crescent_only) - len(mismatched)
                st.markdown(f"✅ {matches} associates match both files with no discrepancies.")

                st.subheader("🔍 PLX Discrepancies (Missing in Crescent)")
                st.dataframe(plx_only)

                st.subheader("🔍 Crescent Discrepancies (Missing in PLX)")
                st.dataframe(crescent_only)

                st.subheader("🔍 Mismatched Hours")
                st.dataframe(mismatched)

                st.subheader("⚠️ Invalid EIDs in Crescent Report")
                st.dataframe(invalid_eid)

                st.subheader("🛠️ Resolve Discrepancies")
                for i in merged.index:
                    merged.at[i, "Error #"] = f"Error {i+1}"
                    resolution = st.selectbox(
                        f"{merged.at[i, 'Error #']} - {merged.at[i, 'Name']}",
                        ["", "EID Match", "Crescent Error", "PLX Error"],
                        key=f"res_{i}"
                    )
                    merged.at[i, "Error Type"] = resolution
                    if resolution in ["Crescent Error", "PLX Error"]:
                        correction = st.number_input(
                            f"Enter corrected hours for {merged.at[i, 'Error #']}",
                            min_value=0.0, value=0.0, key=f"corr_{i}"
                        )
                        merged.at[i, "Correction"] = correction

                if st.button("✅ Validate Files"):
                    corrected_plx = merged.copy()
                    corrected_plx["Final PLX Hours"] = corrected_plx["PLX Hours"].fillna(0)
                    corrected_plx["Final Crescent Hours"] = corrected_plx["Crescent Hours"].fillna(0)

                    for i in corrected_plx.index:
                        if corrected_plx.at[i, "Error Type"] == "Crescent Error":
                            corrected_plx.at[i, "Final Crescent Hours"] = corrected_plx.at[i, "Correction"]
                        elif corrected_plx.at[i, "Error Type"] == "PLX Error":
                            corrected_plx.at[i, "Final PLX Hours"] = corrected_plx.at[i, "Correction"]

                    total_plx = corrected_plx["Final PLX Hours"].sum()
                    total_crescent = corrected_plx["Final Crescent Hours"].sum()

                    st.subheader("📊 Validation Summary")
                    st.markdown(f"**Total PLX Hours:** {total_plx:.2f}")
                    st.markdown(f"**Total Crescent Hours:** {total_crescent:.2f}")

                    if abs(total_plx - total_crescent) < 0.01:
                        st.success("✅ Total hours match after corrections.")
                    else:
                        st.error("❌ Total hours do not match after corrections.")

                    # Generate client-ready summary
                    st.subheader("📄 Client Summary")
                    summary_lines = []
                    for i in corrected_plx.index:
                        if corrected_plx.at[i, "Error Type"] in ["Crescent Error", "PLX Error"]:
                            name = corrected_plx.at[i, "Name"]
                            eid = corrected_plx.at[i, "EID"]
                            badge = corrected_plx.at[i, "Badge"] if "Badge" in corrected_plx.columns else ""
                            correct = corrected_plx.at[i, "Correction"]
                            incorrect = (
                                corrected_plx.at[i, "Crescent Hours"]
                                if corrected_plx.at[i, "Error Type"] == "Crescent Error"
                                else corrected_plx.at[i, "PLX Hours"]
                            )
                            line = f"{name} (EID {eid}) – Worked Line X for {correct} (correct hours), not {incorrect}. [{badge}]"
                            summary_lines.append(line)

                    summary_text = "\n".join(summary_lines)
                    st.text_area("Summary to Email", summary_text, height=300)

    except Exception as e:
        st.error(f"Error processing files: {e}")
