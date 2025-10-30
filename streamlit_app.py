
import streamlit as st
import pandas as pd
import numpy as np

st.set_page_config(page_title="Labor Report Comparison Tool", layout="wide")

# -----------------------------------------
# Utility Functions
# -----------------------------------------

def to_number(x):
    try:
        return float(str(x).replace("$", "").replace(",", "").strip())
    except:
        return 0.0

def normalize_eid(x):
    try:
        return str(int(float(str(x).strip())))
    except:
        return str(x).strip()

# -----------------------------------------
# File Processing: ProLogistix (PLX)
# -----------------------------------------

def process_plx(file):
    df = pd.read_excel(file, header=4)
    df = df.loc[~df["Dept"].astype(str).str.contains("Total", case=False, na=False)]
    df = df[df["Dept"].notna()]

    # Normalize EID and Name
    df["EID"] = df["File"].apply(normalize_eid)
    df["Name"] = df["Name"].astype(str).str.strip()

    # Detect weekday columns dynamically
    day_map = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"]
    day_hours = {}
    for day in day_map:
        reg_cols = [c for c in df.columns if day in c and "Reg Hrs" in c]
        ot_cols = [c for c in df.columns if day in c and "OT Hrs" in c]
        df[f"{day}_Reg"] = df[reg_cols].sum(axis=1).apply(to_number) if reg_cols else 0
        df[f"{day}_OT"] = df[ot_cols].sum(axis=1).apply(to_number) if ot_cols else 0
        day_hours[day] = df[f"{day}_Reg"] + df[f"{day}_OT"]

    df["Total_Hours"] = sum(day_hours.values())

    df_norm = df[["EID","Name","Total_Hours"] + [f"{d}_Reg" for d in day_map] + [f"{d}_OT" for d in day_map]]
    return df_norm

# -----------------------------------------
# File Processing: Crescent
# -----------------------------------------

def process_crescent(file):
    df = pd.read_csv(file) if file.name.endswith(".csv") else pd.read_excel(file)
    df["EID"] = df["Badge"].astype(str).str.extract(r"PLX-(\d+)-")[0]
    df["EID"] = df["EID"].apply(normalize_eid)
    df["Payable_Hours"] = df["Payable Hours"].apply(to_number)
    df["Line"] = df.get("Line", "")
    return df[["EID","Badge","Payable_Hours","Line"]]

# -----------------------------------------
# Discrepancy Detection
# -----------------------------------------

def detect_discrepancies(plx, cres):
    plx_sum = plx.groupby("EID", as_index=False).agg({"Total_Hours":"sum","Name":"first"})
    cres_sum = cres.groupby("EID", as_index=False).agg({"Payable_Hours":"sum"})

    merged = plx_sum.merge(cres_sum, on="EID", how="outer", suffixes=("_PLX","_CRES"))

    # Handle Total_Hours_PLX column safely
    if "Total_Hours_PLX" not in merged.columns and "Total_Hours" in merged.columns:
        merged.rename(columns={"Total_Hours": "Total_Hours_PLX"}, inplace=True)

    merged["Total_Hours_PLX"] = merged.get("Total_Hours_PLX", pd.Series(0, index=merged.index))
    merged["Payable_Hours"] = merged.get("Payable_Hours", pd.Series(0, index=merged.index))

    merged = merged.fillna({"Total_Hours_PLX":0.0, "Payable_Hours":0.0})

    merged["Diff"] = merged["Total_Hours_PLX"] - merged["Payable_Hours"]
    merged["Category"] = np.select(
        [
            (merged["Total_Hours_PLX"] > 0) & (merged["Payable_Hours"] == 0),
            (merged["Payable_Hours"] > 0) & (merged["Total_Hours_PLX"] == 0),
            (merged["Total_Hours_PLX"] != merged["Payable_Hours"]),
        ],
        ["PLX-only","Crescent-only","Mismatched Hours"],
        default="Match",
    )
    return merged

# -----------------------------------------
# Streamlit UI
# -----------------------------------------

st.title("📊 Labor Report Discrepancy Tool")
st.sidebar.header("Options")

day_filter = st.sidebar.selectbox("Select Day of Week", ["All","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"])

plx_file = st.file_uploader("Upload ProLogistix Report (.xls/.xlsx)", type=["xls","xlsx"])
cres_file = st.file_uploader("Upload Crescent Report (.csv/.xlsx)", type=["csv","xlsx"])

if plx_file:
    st.subheader("ProLogistix Data (Parsed)")
    plx_df = process_plx(plx_file)
    st.dataframe(plx_df.head(25), width='stretch')
else:
    plx_df = pd.DataFrame()

if cres_file:
    st.subheader("Crescent Data (Parsed)")
    cres_df = process_crescent(cres_file)
    st.dataframe(cres_df.head(25), width='stretch')
else:
    cres_df = pd.DataFrame()

if not plx_df.empty and not cres_df.empty:
    if day_filter != "All":
        col_reg = f"{day_filter}_Reg"
        col_ot = f"{day_filter}_OT"
        if col_reg in plx_df.columns and col_ot in plx_df.columns:
            plx_df["Total_Hours"] = plx_df[col_reg] + plx_df[col_ot]

    st.subheader("🔍 Detected Discrepancies")
    disc_df = detect_discrepancies(plx_df, cres_df)
    st.dataframe(disc_df, width='stretch')

    st.write(f"**Total PLX Hours:** {plx_df['Total_Hours'].sum():,.2f}")
    st.write(f"**Total Crescent Hours:** {cres_df['Payable_Hours'].sum():,.2f}")

    diff = round(plx_df['Total_Hours'].sum() - cres_df['Payable_Hours'].sum(), 2)
    if diff == 0:
        st.success("✅ Totals match between reports.")
    else:
        st.warning(f"⚠️ Totals differ by {diff} hours.")
