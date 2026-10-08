import io
import pandas as pd
import streamlit as st

# Page setup
st.set_page_config(
    page_title="Battery Data Capacity Extractor", page_icon="🔋", layout="wide"
)

st.title("🔋 Battery Capacity & Coulombic Efficiency Extractor")
st.markdown(
    "Drag and drop raw battery cycler data files (`.txt` or `.csv`) to extract step capacities and calculate Coulombic Efficiency."
)


def process_battery_file(
    uploaded_file, cc_step=None, cv_step=None, dc_step=None
):
    """Parses raw text/csv cycler data and calculates cycle metrics."""
    content = uploaded_file.getvalue().decode("utf-8", errors="ignore")
    lines = content.splitlines()

    # Locate header row dynamically
    header_line = 0
    for i, line in enumerate(lines):
        if (
            line.startswith("Rec")
            or "Cycle C" in line
            or "Cap." in line
            or "Cycle" in line
        ):
            header_line = i
            break

    # Read data into DataFrame
    buffer = io.StringIO(content)
    try:
        df = pd.read_csv(buffer, skiprows=header_line, sep="\t")
        if df.shape[1] == 1:
            buffer.seek(0)
            df = pd.read_csv(buffer, skiprows=header_line, sep=",")
    except Exception as e:
        st.error(f"Error reading file: {e}")
        return None

    df.columns = df.columns.str.strip()

    if "Cycle C" not in df.columns or "Cap. [Ah]" not in df.columns:
        st.error(
            "File header format not recognized. Missing 'Cycle C' or 'Cap. [Ah]' columns."
        )
        return None

    # Filter invalid cycle entries
    df = df.dropna(subset=["Cycle C"]).copy()
    df["Cycle C"] = df["Cycle C"].astype(int)

    results = []

    for cycle, cycle_data in df.groupby("Cycle C"):
        if cc_step and cv_step and dc_step:
            # Custom step index mode
            cc_sub = cycle_data[cycle_data["Step"] == cc_step]
            cv_sub = cycle_data[cycle_data["Step"] == cv_step]
            dc_sub = cycle_data[cycle_data["Step"] == dc_step]

            cc_cap = cc_sub["Cap. [Ah]"].iloc[-1] if not cc_sub.empty else 0.0
            cv_cap = cv_sub["Cap. [Ah]"].iloc[-1] if not cv_sub.empty else 0.0
            dc_cap = dc_sub["Cap. [Ah]"].iloc[-1] if not dc_sub.empty else 0.0
        else:
            # Auto-detect charge/discharge steps
            if "Md" in cycle_data.columns:
                steps_in_cycle = (
                    cycle_data.groupby("Step")["Md"].first().reset_index()
                )
                c_steps = steps_in_cycle[steps_in_cycle["Md"] == "C"][
                    "Step"
                ].tolist()
                d_steps = steps_in_cycle[steps_in_cycle["Md"] == "D"][
                    "Step"
                ].tolist()

                cc_step_id = c_steps[0] if len(c_steps) > 0 else None
                cv_step_id = c_steps[1] if len(c_steps) > 1 else None
                dc_step_id = d_steps[0] if len(d_steps) > 0 else None

                cc_cap = (
                    cycle_data[cycle_data["Step"] == cc_step_id][
                        "Cap. [Ah]"
                    ].iloc[-1]
                    if cc_step_id
                    else 0.0
                )
                cv_cap = (
                    cycle_data[cycle_data["Step"] == cv_step_id][
                        "Cap. [Ah]"
                    ].iloc[-1]
                    if cv_step_id
                    else 0.0
                )
                dc_cap = (
                    cycle_data[cycle_data["Step"] == dc_step_id][
                        "Cap. [Ah]"
                    ].iloc[-1]
                    if dc_step_id
                    else 0.0
                )
            else:
                cc_cap = cycle_data["Cap. [Ah]"].max()
                cv_cap = 0.0
                dc_cap = 0.0

        total_charge_cap = cc_cap + cv_cap
        coulombic_eff = (
            (dc_cap / total_charge_cap * 100) if total_charge_cap > 0 else 0.0
        )

        results.append(
            {
                "Cycle": cycle,
                "CC Capacity (Ah)": round(cc_cap, 6),
                "CV Capacity (Ah)": round(cv_cap, 6),
                "Total Charge Capacity (Ah)": round(total_charge_cap, 6),
                "Discharge Capacity (Ah)": round(dc_cap, 6),
                "Coulombic Efficiency (%)": round(coulombic_eff, 2),
            }
        )

    return pd.DataFrame(results)


# File Uploader Widget
uploaded_files = st.file_uploader(
    "Drag & drop your files here",
    type=["txt", "csv"],
    accept_multiple_files=True,
)

if uploaded_files:
    for uploaded_file in uploaded_files:
        st.write("---")
        st.subheader(f"📄 File: `{uploaded_file.name}`")

        results_df = process_battery_file(uploaded_file)

        if results_df is not None:
            # Display Table
            st.dataframe(results_df, use_container_width=True)

            # Display Visualizations
            col1, col2 = st.columns(2)
            with col1:
                st.caption("Capacities vs Cycle")
                st.line_chart(
                    results_df.set_index("Cycle")[
                        [
                            "Total Charge Capacity (Ah)",
                            "Discharge Capacity (Ah)",
                        ]
                    ]
                )
            with col2:
                st.caption("Coulombic Efficiency (%) vs Cycle")
                st.line_chart(
                    results_df.set_index("Cycle")["Coulombic Efficiency (%)"]
                )

            # Download Option
            csv_data = results_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 Download Extracted Results CSV",
                data=csv_data,
                file_name=f"processed_{uploaded_file.name}.csv",
                mime="text/csv",
            )