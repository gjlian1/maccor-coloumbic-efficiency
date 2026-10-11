import io
import os
import re
import struct
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(
    page_title="Battery Data Analyzer", page_icon="🔋", layout="wide"
)

st.title("🔋 Battery Data & Specific Capacity Analyzer")
st.markdown(
    "Drag & drop Maccor files (`.000`–`.xxx`, `.txt`, `.csv`) to extract step capacities and plot performance curves."
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
  """Parses raw Maccor binary .000-.xxx files directly in memory preserving full float precision."""
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
            "Test Time [s]": test_time,
            "Current [A]": current,
            "Voltage [V]": voltage,
            "Capacity": capacity,
            "S.Capacity (mAh/g)": spec_cap,
            "Energy": energy,
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

    if not cycle_col and "cycle" in c_lower and "p" not in c_lower:
      cycle_col = c_clean

    if (
        not spec_cap_col
        and ("s.capacity" in c_lower or "mah/g" in c_lower or "spec" in c_lower)
    ):
      spec_cap_col = c_clean

    if not cap_col and ("capacity" in c_lower or "cap" in c_lower):
      if c_clean != spec_cap_col:
        cap_col = c_clean

    if not md_col and c_lower in ["md", "mode", "md."]:
      md_col = c_clean

    if not step_col and "step" in c_lower and "time" not in c_lower:
      step_col = c_clean

  return cycle_col, spec_cap_col, cap_col, md_col, step_col


def process_dataframe(df, filename="", active_mass_g=None):
  """Calculates capacities and Coulombic Efficiency using full, unrounded floats."""
  df.columns = df.columns.str.strip()

  cycle_col, spec_cap_col, cap_col, md_col, step_col = auto_detect_columns(df)

  if spec_cap_col and spec_cap_col in df.columns:
    target_cap_col = spec_cap_col
    unit_label = "mAh/g"
  elif cap_col and cap_col in df.columns:
    target_cap_col = cap_col
    unit_label = "mAh/g" if active_mass_g else "Ah"
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
        f"CC Charge ({unit_label})": float(cc_cap),
        f"CV Charge ({unit_label})": float(cv_cap),
        f"Total Charge ({unit_label})": float(total_charge_cap),
        f"Discharge ({unit_label})": float(dc_cap),
        "Discharge Capacity (Ah)": float(dc_cap_ah),
        "Coulombic Efficiency (%)": float(coulombic_eff),
    })

  return pd.DataFrame(results), unit_label


def render_click_to_copy_table(df, height=350):
  """Renders an interactive HTML table where clicking any column header copies all its exact row values directly to the clipboard."""
  headers_html = "".join(
      f"<th onclick=\"copyColumn({i}, '{col}')\" title=\"Click to copy all"
      f' {col} values">{col} 📋</th>'
      for i, col in enumerate(df.columns)
  )

  rows_html = ""
  for _, row in df.iterrows():
    cells = "".join(f"<td>{val}</td>" for val in row)
    rows_html += f"<tr>{cells}</tr>"

  html_code = f"""
    <style>
        .table-container {{
            max-height: {height}px;
            overflow-y: auto;
            border: 1px solid #374151;
            border-radius: 8px;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin-bottom: 15px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            color: #e5e7eb;
            background-color: #0e1117;
        }}
        th {{
            position: sticky;
            top: 0;
            background-color: #1f2937;
            color: #60a5fa;
            padding: 10px 12px;
            text-align: left;
            cursor: pointer;
            border-bottom: 2px solid #4b5563;
            user-select: none;
            white-space: nowrap;
        }}
        th:hover {{
            background-color: #374151;
            color: #93c5fd;
        }}
        td {{
            padding: 8px 12px;
            border-bottom: 1px solid #1f2937;
            white-space: nowrap;
        }}
        tr:hover {{
            background-color: #111827;
        }}
        #toast {{
            visibility: hidden;
            min-width: 200px;
            background-color: #10b981;
            color: #ffffff;
            font-weight: 600;
            text-align: center;
            border-radius: 6px;
            padding: 8px 16px;
            position: fixed;
            z-index: 1000;
            top: 10px;
            right: 20px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
            font-size: 13px;
        }}
        #toast.show {{
            visibility: visible;
        }}
    </style>

    <div id="toast">Copied to Clipboard!</div>
    <div class="table-container">
        <table id="maccorTable">
            <thead><tr>{headers_html}</tr></thead>
            <tbody>{rows_html}</tbody>
        </table>
    </div>

    <script>
        function copyColumn(colIdx, colName) {{
            const table = document.getElementById("maccorTable");
            const rows = table.rows;
            let colValues = [];
            
            for (let i = 1; i < rows.length; i++) {{
                colValues.push(rows[i].cells[colIdx].innerText);
            }}
            
            const textToCopy = colValues.join("\\n");
            
            navigator.clipboard.writeText(textToCopy).then(() => {{
                const toast = document.getElementById("toast");
                toast.innerText = "Copied " + colValues.length + " values from '" + colName + "'";
                toast.className = "show";
                setTimeout(() => {{ toast.className = toast.className.replace("show", ""); }}, 2200);
            }}).catch(err => {{
                console.error('Failed to copy: ', err);
            }});
        }}
    </script>
    """
  components.html(html_code, height=height + 30, scrolling=True)


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

      if not parsed_mass:
        parsed_mass = parse_header_metadata(lines[:20])
        if parsed_mass:
          st.caption(f"ℹ️ Detected Active Mass from Header: `{parsed_mass} g`")

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
          st.caption(
              "💡 **Click any column header below to copy all its row values to your clipboard.**"
          )
          render_click_to_copy_table(results_df, height=320)

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
