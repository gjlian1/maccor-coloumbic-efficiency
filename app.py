import io
import os
import re
import struct
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Battery Data Analyzer", page_icon="🔋", layout="wide"
)

st.title("🔋 Battery Data & Specific Capacity Analyzer")
st.markdown(
    "Drag & drop Maccor files (`.000`–`.xxx`, `.txt`, `.csv`) to extract step specific capacities (**mAh/g**), discharge capacity (**Ah**), and plot performance curves."
)


def parse_header_metadata(lines):
  """Extracts Active Mass (SCap) from header metadata if available (e.g. 'SCap: 0.0347 g')."""
  mass_g = None
  for line in lines:
    if "SCap:" in line or "Mass:" in line:
      match = re.search(r"(\d+\.\d+)", line)
      if match:
        mass_g = float(match.group(1))
        break
  return mass_g


def parse_maccor_binary_bytes(file_bytes, mass_g=None):
  """Parses raw Maccor binary .000-.xxx files directly in memory."""
  header_offset = 128
  record_format = (
      "<HHfffff"  # Step, Cycle, TestTime, Current, Voltage, Capacity, Energy
  )
  record_size = struct.calcsize(record_format)

  records = []
  data = file_bytes[header_offset:]

  for offset in range(0, len(data) - record_size + 1, record_size):
    chunk = data[offset : offset + record_size]
    try:
      step, cycle, test_time, current, voltage, capacity, energy = (
          struct.unpack(record_format, chunk)
      )
      if -100.0 <= current <= 500.0 and 0.0 <= voltage <= 20.0:
        spec_cap = (
            (capacity * 1000.0 / mass_g) if (mass_g and mass_g > 0) else 0.0
        )
        records.append({
            "Cycle C": int(cycle) if cycle > 0 else 1,
            "Step": int(step),
            "Test Time [s]": round(test_time, 2),
            "Current [A]": round(current, 6),
            "Voltage [V]": round(voltage, 4),
            "Capacity": round(capacity, 6),
            "S.Capacity (mAh/g)": round(spec_cap, 3),
            "Energy": round(energy, 6),
            "MD": (
                "C"
                if current > 0.000001
                else ("D" if current < -0.000001 else "R")
            ),
        })
    except Exception:
      continue

  return pd.DataFrame(records) if records else None


def auto_detect_columns(df):
  """Locates Cycle, Specific Capacity / Capacity, Mode (MD), and Step columns."""
  cycle_col, spec_cap_col, cap_col, md_col, step_col = (
      None,
      None,
      None,
      None,
      None,
  )

  for col in df.columns:
    c_clean = str(col).strip()
    c_lower = c_clean.lower()

    # Cycle column (targets 'Cycle C', excludes 'Cycle P')
    if not cycle_col and "cycle" in c_lower and "p" not in c_lower:
      cycle_col = c_clean

    # Specific Capacity column (mAh/g)
    if (
        not spec_cap_col
        and ("s.capacity" in c_lower or "mah/g" in c_lower or "spec" in c_lower)
    ):
      spec_cap_col = c_clean

    # Raw Capacity column (Ah)
    if not cap_col and ("capacity" in c_lower or "cap" in c_lower):
      if c_clean != spec_cap_col:
        cap_col = c_clean

    # Mode column (MD / Mode)
    if not md_col and c_lower in ["md", "mode", "md."]:
      md_col = c_clean

    # Step column
    if not step_col and "step" in c_lower and "time" not in c_lower:
      step_col = c_clean

  return cycle_col, spec_cap_col, cap_col, md_col, step_col


def process_dataframe(df, filename="", active_mass_g=None):
  """Calculates CC, CV, Total Charge, Discharge Specific Capacities (mAh/g), Discharge Capacity (Ah), and Coulombic Efficiency."""
  df.columns = df.columns.str.strip()

  cycle_col, spec_cap_col, cap_col, md_col, step_col = auto_detect_columns(df)

  # Determine target capacity column for mAh/g
  if spec_cap_col and spec_cap_col in df.columns:
    target_cap_col = spec_cap_col
    unit_label = "mAh/g"
  elif cap_col and cap_col in df.columns:
    target_cap_col = cap_col
    unit_label = "mAh/g" if active_mass_g else "Ah"
    # Convert Ah to mAh/g if active mass is provided
    if active_mass_g and active_mass_g > 0:
      df[target_cap_col] = (df[target_cap_col] * 1000.0) / active_mass_g
  else:
    st.error(f"❌ Could not locate capacity data in `{filename}`.")
    return None

  df = df.dropna(subset=[cycle_col]).copy()
  df[cycle_col] = (
      pd.to_numeric(df[cycle_col], errors="coerce").fillna(0).astype(int)
  )

  results = []

  for cycle, cycle_data in df.groupby(cycle_col):
    if cycle == 0:
      continue

    if (
        md_col
        and md_col in cycle_data.columns
        and step_col
        and step_col in cycle_data.columns
    ):
      steps = cycle_data.groupby(step_col)[md_col].first().reset_index()
      c_steps = steps[steps[md_col] == "C"][step_col].tolist()
      d_steps = steps[steps[md_col] == "D"][step_col].tolist()

      cc_step = c_steps[0] if len(c_steps) > 0 else None
      cv_step = c_steps[1] if len(c_steps) > 1 else None
      dc_step = d_steps[0] if len(d_steps) > 0 else None

      # Specific capacities (mAh/g)
      cc_cap = (
          cycle_data[cycle_data[step_col] == cc_step][target_cap_col].iloc[-1]
          if cc_step
          else 0.0
      )
      cv_cap = (
          cycle_data[cycle_data[step_col] == cv_step][target_cap_col].iloc[-1]
          if cv_step
          else 0.0
      )
      dc_cap = (
          cycle_data[cycle_data[step_col] == dc_step][target_cap_col].iloc[-1]
          if dc_step
          else 0.0
      )

      # Raw Discharge Capacity in Ah
      if cap_col and cap_col in cycle_data.columns:
        dc_cap_ah = (
            cycle_data[cycle_data[step_col] == dc_step][cap_col].iloc[-1]
            if dc_step
            else 0.0
        )
      elif active_mass_g and active_mass_g > 0:
        dc_cap_ah = (dc_cap * active_mass_g) / 1000.0
      else:
        dc_cap_ah = dc_cap
    else:
      cc_cap = cycle_data[target_cap_col].max()
      cv_cap = 0.0
      dc_cap = 0.0
      if cap_col and cap_col in cycle_data.columns:
        dc_cap_ah = cycle_data[cap_col].max()
      else:
        dc_cap_ah = 0.0

    total_charge_cap = cc_cap + cv_cap
    coulombic_eff = (
        (dc_cap / total_charge_cap * 100) if total_charge_cap > 0 else 0.0
    )

    results.append({
        "Cycle": cycle,
        f"CC Charge ({unit_label})": round(cc_cap, 3),
        f"CV Charge ({unit_label})": round(cv_cap, 3),
        f"Total Charge ({unit_label})": round(total_charge_cap, 3),
        f"Discharge ({unit_label})": round(dc_cap, 3),
        "Discharge Capacity (Ah)": round(dc_cap_ah, 6),
        "Coulombic Efficiency (%)": round(coulombic_eff, 2),
    })

  return pd.DataFrame(results), unit_label


# Sidebar Options
st.sidebar.header("⚙️ Active Mass Settings")
custom_mass = st.sidebar.number_input(
    "Active Material Mass (g) [Optional Override]",
    min_value=0.0,
    value=0.0,
    step=0.001,
    format="%.4f",
    help="Used to convert Capacity (Ah) into Specific Capacity (mAh/g) if the file lacks an S.Capacity column.",
)

# File Uploader
allowed_exts = ["txt", "csv"] + [f"{i:03d}" for i in range(1000)]

uploaded_files = st.file_uploader(
    "Drag & drop Maccor files here (.000–.xxx, .txt, .csv)",
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

    parsed_mass = custom_mass if custom_mass > 0 else None

    if is_binary:
      st.info(f"⚡ Decoding binary data (`.{ext}`)...")
      raw_df = parse_maccor_binary_bytes(file_bytes, mass_g=parsed_mass)
    else:
      content = file_bytes.decode("latin-1")
      lines = content.splitlines()

      # Parse header metadata for mass if not supplied via sidebar
      if not parsed_mass:
        parsed_mass = parse_header_metadata(lines[:20])
        if parsed_mass:
          st.caption(f"ℹ️ Detected Active Mass from Header: `{parsed_mass} g`")

      # Dynamic header row locator
      header_line = 0
      for i, line in enumerate(lines):
        if any(
            k in line
            for k in [
                "Rec",
                "Cycle C",
                "Cycle",
                "Test Time",
                "Step",
                "Capacity",
                "S.Capacity",
                "MD",
            ]
        ):
          header_line = i
          break

      buffer = io.StringIO(content)
      try:
        raw_df = pd.read_csv(buffer, skiprows=header_line, sep="\t")
        if raw_df.shape[1] <= 1:
          buffer.seek(0)
          raw_df = pd.read_csv(buffer, skiprows=header_line, sep=",")
      except Exception as e:
        st.error(f"Error reading file: {e}")
        raw_df = None

    if raw_df is not None:
      processed = process_dataframe(
          raw_df, filename=uploaded_file.name, active_mass_g=parsed_mass
      )

      if processed:
        results_df, unit_lbl = processed

        if not results_df.empty:
          # Displays summary table including Discharge Capacity (Ah)
          st.dataframe(results_df, use_container_width=True)

          # Charts plot specific capacities and Coulombic Efficiency only
          col1, col2 = st.columns(2)
          with col1:
            st.caption(f"Specific Capacities ({unit_lbl}) vs Cycle")
            st.line_chart(
                results_df.set_index("Cycle")[
                    [
                        f"CC Charge ({unit_lbl})",
                        f"CV Charge ({unit_lbl})",
                        f"Total Charge ({unit_lbl})",
                        f"Discharge ({unit_lbl})",
                    ]
                ]
            )
          with col2:
            st.caption("Coulombic Efficiency (%) vs Cycle")
            st.line_chart(
                results_df.set_index("Cycle")["Coulombic Efficiency (%)"]
            )

          st.download_button(
              label="📥 Download CSV Summary",
              data=results_df.to_csv(index=False).encode("utf-8"),
              file_name=f"processed_{uploaded_file.name}.csv",
              mime="text/csv",
          )
