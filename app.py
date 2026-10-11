import io
import os
import re
import struct
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Battery Data Analyzer", page_icon="🔋", layout="wide"
)

# --- PASSWORD PROTECTION BLOCK ---
def check_password():
    def password_entered():
        if st.session_state["password"] == st.secrets.get("APP_PASSWORD", "my_secret_123"):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("🔒 Enter Password:", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("🔒 Enter Password:", type="password", on_change=password_entered, key="password")
        st.error("❌ Incorrect Password.")
        return False
    return True

if not check_password():
    st.stop()

# --- MAIN APP LOGIC ---
st.title("🔋 Battery Data & Capacity Analyzer")
st.markdown("Drag & drop Maccor files (`.000`–`.xxx`, `.txt`, `.csv`) to extract step capacities and plot performance curves.")

def parse_maccor_binary_bytes(file_bytes):
    """Parses binary .000-.xxx files directly in memory."""
    header_offset = 128
    record_format = "<HHfffff" # Step, Cycle, TestTime, Current, Voltage, Capacity, Energy
    record_size = struct.calcsize(record_format)

    records = []
    data = file_bytes[header_offset:]

    for offset in range(0, len(data) - record_size + 1, record_size):
        chunk = data[offset : offset + record_size]
        try:
            step, cycle, test_time, current, voltage, capacity, energy = struct.unpack(record_format, chunk)
            if -100.0 <= current <= 500.0 and 0.0 <= voltage <= 20.0:
                records.append({
                    "Cycle C": int(cycle) if cycle > 0 else 1,
                    "Step": int(step),
                    "TestTime [s]": round(test_time, 2),
                    "Current [A]": round(current, 6),
                    "Voltage [V]": round(voltage, 4),
                    "Cap. [Ah]": round(capacity, 6),
                    "Ener. [Wh]": round(energy, 6),
                })
        except Exception:
            continue

    return pd.DataFrame(records) if records else None

def auto_detect_columns(df):
    """Smart auto-detection for Cycle and Capacity columns regardless of formatting."""
    cycle_col, cap_col = None, None
    
    # 1. Search Cycle column
    for col in df.columns:
        c_lower = str(col).lower().strip()
        if ("cycle" in c_lower or "cycl" in c_lower) and "p" not in c_lower:
            cycle_col = col
            break

    # 2. Search Capacity column
    for col in df.columns:
        c_lower = str(col).lower().strip()
        if "cap" in c_lower or "capacity" in c_lower or "ah" in c_lower:
            cap_col = col
            break

    return cycle_col, cap_col

def process_dataframe(df, filename=""):
    """Extracts step capacities per cycle and computes Coulombic Efficiency."""
    df.columns = df.columns.str.strip()

    cycle_col, cap_col = auto_detect_columns(df)

    # Fallback column selector if auto-detect fails
    if not cycle_col or not cap_col:
        st.warning(f"⚠️ Could not automatically identify columns in `{filename}`.")
        col1, col2 = st.columns(2)
        with col1:
            cycle_col = st.selectbox("Select Cycle Column:", options=df.columns, key=f"cycle_{filename}")
        with col2:
            cap_col = st.selectbox("Select Capacity Column:", options=df.columns, key=f"cap_{filename}")

    df = df.dropna(subset=[cycle_col]).copy()
    df[cycle_col] = pd.to_numeric(df[cycle_col], errors="coerce").fillna(0).astype(int)

    results = []

    for cycle, cycle_data in df.groupby(cycle_col):
        if cycle == 0:
            continue
            
        if "Md" in cycle_data.columns and "Step" in cycle_data.columns:
            steps = cycle_data.groupby("Step")["Md"].first().reset_index()
            c_steps = steps[steps["Md"] == "C"]["Step"].tolist()
            d_steps = steps[steps["Md"] == "D"]["Step"].tolist()

            cc_step = c_steps[0] if len(c_steps) > 0 else None
            cv_step = c_steps[1] if len(c_steps) > 1 else None
            dc_step = d_steps[0] if len(d_steps) > 0 else None

            cc_cap = cycle_data[cycle_data["Step"] == cc_step][cap_col].iloc[-1] if cc_step else 0.0
            cv_cap = cycle_data[cycle_data["Step"] == cv_step][cap_col].iloc[-1] if cv_step else 0.0
            dc_cap = cycle_data[cycle_data["Step"] == dc_step][cap_col].iloc[-1] if dc_step else 0.0
        else:
            cc_cap = cycle_data[cap_col].max()
            cv_cap = 0.0
            dc_cap = 0.0

        total_charge_cap = cc_cap + cv_cap
        coulombic_eff = (dc_cap / total_charge_cap * 100) if total_charge_cap > 0 else 0.0

        results.append({
            "Cycle": cycle,
            "CC Capacity (Ah)": round(cc_cap, 6),
            "CV Capacity (Ah)": round(cv_cap, 6),
            "Total Charge Capacity (Ah)": round(total_charge_cap, 6),
            "Discharge Capacity (Ah)": round(dc_cap, 6),
            "Coulombic Efficiency (%)": round(coulombic_eff, 2),
        })

    return pd.DataFrame(results)

# File Uploader
allowed_exts = ["txt", "csv"] + [f"{i:03d}" for i in range(1000)]

uploaded_files = st.file_uploader(
    "Drag & drop your files here (.000–.xxx, .txt, .csv)",
    type=allowed_exts,
    accept_multiple_files=True,
)

if uploaded_files:
    for uploaded_file in uploaded_files:
        st.write("---")
        st.subheader(f"📄 File: `{uploaded_file.name}`")

        ext = os.path.splitext(uploaded_file.name)[1].lstrip(".").lower()
        is_binary = bool(re.match(r"^\d{3}$", ext))
        file_bytes = uploaded_file.getvalue()

        if is_binary:
            st.info(f"⚡ Decoding binary data (`.{ext}`)...")
            raw_df = parse_maccor_binary_bytes(file_bytes)
        else:
            content = file_bytes.decode("latin-1")
            lines = content.splitlines()
            header_line = 0
            for i, line in enumerate(lines):
                if any(k in line for k in ["Rec", "Cycle C", "Cycle", "TestTime", "Step", "Cap"]):
                    header_line = i
                    break
            
            buffer = io.StringIO(content)
            try:
                # Attempts tab-separated first, falls back to comma-separated
                raw_df = pd.read_csv(buffer, skiprows=header_line, sep="\t")
                if raw_df.shape[1] <= 1:
                    buffer.seek(0)
                    raw_df = pd.read_csv(buffer, skiprows=header_line, sep=",")
            except Exception as e:
                st.error(f"Error parsing file: {e}")
                raw_df = None

        if raw_df is not None:
            results_df = process_dataframe(raw_df, filename=uploaded_file.name)

            if results_df is not None and not results_df.empty:
                st.dataframe(results_df, use_container_width=True)

                col1, col2 = st.columns(2)
                with col1:
                    st.caption("Capacities vs Cycle")
                    st.line_chart(
                        results_df.set_index("Cycle")[
                            [
                                "CC Capacity (Ah)",
                                "CV Capacity (Ah)",
                                "Total Charge Capacity (Ah)",
                                "Discharge Capacity (Ah)",
                            ]
                        ]
                    )
                with col2:
                    st.caption("Coulombic Efficiency (%) vs Cycle")
                    st.line_chart(results_df.set_index("Cycle")["Coulombic Efficiency (%)"])

                st.download_button(
                    label="📥 Download CSV Summary",
                    data=results_df.to_csv(index=False).encode("utf-8"),
                    file_name=f"processed_{uploaded_file.name}.csv",
                    mime="text/csv",
                )
