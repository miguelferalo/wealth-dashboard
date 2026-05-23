"""
╔══════════════════════════════════════════════════════════════╗
║           Personal Wealth Dashboard — Google Sheets Live     ║
╠══════════════════════════════════════════════════════════════╣
║  FIRST-TIME SETUP (do this once):                            ║
║                                                              ║
║  1. Go to https://console.cloud.google.com                   ║
║     → Create a project (any name)                            ║
║     → APIs & Services → Enable APIs                          ║
║     → Search "Google Sheets API" → Enable                    ║
║     → Search "Google Drive API"  → Enable                    ║
║                                                              ║
║  2. APIs & Services → Credentials                            ║
║     → Create Credentials → OAuth client ID                   ║
║     → Application type: Desktop app → Create                 ║
║     → Download JSON → save as credentials.json               ║
║     → Put credentials.json in the same folder as this file   ║
║                                                              ║
║  3. Install dependencies:                                     ║
║     pip install dash plotly pandas                           ║
║         google-auth google-auth-oauthlib                     ║
║         google-auth-httplib2 google-api-python-client        ║
║                                                              ║
║  4. Run:  python wealth_dashboard.py                         ║
║     → Browser opens once for Google login → Done             ║
║     → Token saved to token.json (never asked again)          ║
║                                                              ║
║  Open: http://127.0.0.1:8050                                 ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, sys, json, traceback
from pathlib import Path
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import dash
from dash import dcc, html, Input, Output, State

# ── Config ────────────────────────────────────────────────────────────────────
SHEET_ID       = "1h5GD2Sn-5Jo96IR3_lIHYGIhWTUk564RDSU13wSSC48"
RAW_SHEET      = "Raw Data (GBP & AED)"   # sheet tab with inputs
FX_SHEET       = "FX & Assumptions"       # sheet tab with FX rates
CREDENTIALS    = Path(__file__).parent / "credentials.json"
TOKEN_FILE     = Path(__file__).parent / "token.json"
AED_USD_FIXED  = 1 / 3.6725              # fallback if FX sheet unavailable

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# ── Google Sheets auth ────────────────────────────────────────────────────────
def get_sheets_service():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS.exists():
                raise FileNotFoundError(
                    f"\n\n❌  credentials.json not found at: {CREDENTIALS}\n"
                    "    Follow the FIRST-TIME SETUP instructions at the top of this file.\n"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        print("✅  Token saved — you won't need to log in again.")

    return build("sheets", "v4", credentials=creds).spreadsheets()


def fetch_sheet_values(service, sheet_name):
    """Return list-of-lists for a named sheet (skips empty rows)."""
    result = service.values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{sheet_name}'"
    ).execute()
    return result.get("values", [])


def safe_float(val, default=0.0):
    try:
        return float(str(val).replace(",", "").replace("£", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return default


# ── Parse Raw Data (GBP & AED) sheet ─────────────────────────────────────────
# Rows 1-3 are headers. Data from row 4. Dates already in YYYY-MM format.
# A=Date, B=Current Acct, C=Savings, D=Van LS100, E=Van ESG, F=Van SP500, G=Crypto,
# H=DAZN, I=Tesco, J=Kindred, K=LISA,
# L=UK House Equity (GBP),
# M=Dubai Prop (AED), N=Coventry (AED),
# O=Gratuity (AED), P=Car (AED)

RAW_COL_MAP = {
    "date":             0,   # A
    "current_acct_gbp": 1,   # B
    "savings_gbp":      2,   # C
    "van_ls100":        3,   # D
    "van_esg":          4,   # E
    "van_sp500":        5,   # F
    "crypto":           6,   # G
    "ret_dazn":         7,   # H
    "ret_tesco":        8,   # I
    "ret_kindred":      9,   # J
    "ret_lisa":         10,  # K
    "uk_house":         11,  # L
    "dubai_prop_aed":   12,  # M
    "coventry_aed":     13,  # N
    "gratuity_aed":     14,  # O
    "car_aed":          15,  # P
}


def parse_raw_data(rows):
    """New sheet: dates are already YYYY-MM. Rows 1-3 are headers, data from row 4."""
    seen = {}
    order = []
    for row in rows[3:]:   # skip 3 header rows
        if not row or not row[0] or not str(row[0]).strip():
            continue
        date = str(row[0]).strip()
        if not date[:4].isdigit():   # must start with year e.g. 2024
            continue
        def g(idx, r=row):
            return safe_float(r[idx]) if idx < len(r) else 0.0
        rec = {k: (date if k == "date" else g(v)) for k, v in RAW_COL_MAP.items()}
        if date not in seen:
            order.append(date)
        seen[date] = rec   # last row per month wins (handles any duplicates)

    records = [seen[d] for d in order]
    print(f"  📅 Parsed {len(records)} months: {records[0]['date'] if records else '?'} → {records[-1]['date'] if records else '?'}")
    return records


def parse_fx_rates(rows):
    """FX & Assumptions sheet: AED/USD in row 3 col B. GBP/USD table starts row 7 (index 6).
    Dates already in YYYY-MM format."""
    fx = {}
    aed_usd = AED_USD_FIXED

    # AED/USD peg is in row 3 (index 2), col B (index 1)
    if len(rows) >= 3 and len(rows[2]) >= 2:
        v = safe_float(rows[2][1], 0)
        if v > 0:
            aed_usd = v

    # GBP/USD rates start at row 7 (index 6), col A=date, col B=rate
    for row in rows[6:]:
        if not row or not row[0]:
            continue
        date = str(row[0]).strip()
        if not date[:4].isdigit():
            continue
        if len(row) > 1:
            rate = safe_float(row[1], 0)
            if rate > 0:
                fx[date] = rate

    print(f"  💱 Loaded {len(fx)} FX rates, AED/USD={aed_usd:.6f}")
    return fx, aed_usd


# Fallback GBP/USD rates for months not covered by the Currency exchange rate sheet
GBP_USD_FALLBACK = {
    "2024-04": 1.251419, "2024-05": 1.262745, "2024-06": 1.270938,
    "2024-07": 1.286336, "2024-08": 1.293435, "2024-09": 1.321129,
    "2024-10": 1.305953, "2024-11": 1.275405, "2024-12": 1.264064,
    "2025-01": 1.239700, "2025-02": 1.253800, "2025-03": 1.291900,
    "2025-04": 1.321100, "2025-05": 1.333200, "2025-06": 1.357100,
    "2025-07": 1.340300, "2025-08": 1.313400, "2025-09": 1.327800,
    "2025-10": 1.298700, "2025-11": 1.267900, "2025-12": 1.258200,
    "2026-01": 1.270954, "2026-02": 1.263235, "2026-03": 1.270671,
    "2026-04": 1.251419, "2026-05": 1.262745,
}


# ── Build DataFrame ───────────────────────────────────────────────────────────
def build_df(raw_records, fx_rates, aed_usd):
    rows_out = []
    for r in raw_records:
        date = r["date"]
        rate = fx_rates.get(date) or GBP_USD_FALLBACK.get(date, 1.27)

        liquid  = (r["current_acct_gbp"] + r["savings_gbp"]) * rate
        invest  = (r["van_ls100"] + r["van_esg"] + r["van_sp500"] + r["crypto"]) * rate
        retire  = (r["ret_dazn"] + r["ret_tesco"] + r["ret_kindred"] + r["ret_lisa"]) * rate
        prop    = r["uk_house"] * rate + (r["dubai_prop_aed"] + r["coventry_aed"]) * aed_usd
        other   = (r["gratuity_aed"] + r["car_aed"]) * aed_usd
        total   = liquid + invest + retire + prop + other

        rows_out.append({**r, "rate": rate,
                         "Liquid": liquid, "Investments": invest,
                         "Retirement": retire, "Property": prop,
                         "Other": other, "Total": total})

    df = pd.DataFrame(rows_out)
    if df.empty:
        return df
    df["MoM_Change"] = df["Total"].diff()
    df["MoM_Pct"]    = df["Total"].pct_change() * 100
    df["Cum_6M"]     = df["Total"].pct_change(6) * 100
    df["Cum_12M"]    = df["Total"].pct_change(12) * 100
    return df


# ── Load data (called on startup + refresh) ───────────────────────────────────
_last_load_time = None
_cached_df      = None
_load_error     = None

def load_data():
    global _last_load_time, _cached_df, _load_error
    try:
        service    = get_sheets_service()
        raw_rows   = fetch_sheet_values(service, RAW_SHEET)
        fx_rows    = fetch_sheet_values(service, FX_SHEET)
        raw_recs   = parse_raw_data(raw_rows)
        fx_rates, aed_usd = parse_fx_rates(fx_rows)
        _cached_df = build_df(raw_recs, fx_rates, aed_usd)
        _load_error = None
        _last_load_time = datetime.now().strftime("%H:%M:%S")
        print(f"✅  Loaded {len(_cached_df)} months from Google Sheets at {_last_load_time}")
    except Exception as e:
        _load_error = str(e)
        print(f"❌  Error loading data: {e}")
        traceback.print_exc()


# Initial load
load_data()

# ── Theme ─────────────────────────────────────────────────────────────────────
CATS    = ["Liquid", "Investments", "Retirement", "Property", "Other"]
PALETTE = {"Liquid": "#2196F3", "Investments": "#4CAF50",
           "Retirement": "#FF9800", "Property": "#9C27B0",
           "Other": "#607D8B", "Total": "#1F3864"}
DARK_BG  = "#0F172A"
CARD_BG  = "#1E293B"
TEXT_COL = "#E2E8F0"
ACCENT   = "#3B82F6"

PLOT_LAYOUT = dict(
    paper_bgcolor=CARD_BG, plot_bgcolor=DARK_BG,
    font=dict(color=TEXT_COL, family="Inter, Arial, sans-serif", size=12),
    margin=dict(l=50, r=20, t=50, b=50),
    legend=dict(bgcolor=CARD_BG, bordercolor="#334155", borderwidth=1),
    xaxis=dict(gridcolor="#1E3A5F", zerolinecolor="#334155"),
    yaxis=dict(gridcolor="#1E3A5F", zerolinecolor="#334155"),
)

# ── Dash App ──────────────────────────────────────────────────────────────────
app = dash.Dash(__name__, title="Wealth Dashboard", suppress_callback_exceptions=True)

def kpi_card(label, value, sub="", color=ACCENT):
    return html.Div([
        html.P(label, style={"margin": 0, "fontSize": "11px", "color": "#94A3B8",
                             "textTransform": "uppercase", "letterSpacing": "1px"}),
        html.H3(value, style={"margin": "4px 0", "fontSize": "24px",
                              "color": color, "fontWeight": "700"}),
        html.P(sub, style={"margin": 0, "fontSize": "11px", "color": "#64748B"}),
    ], style={"background": CARD_BG, "borderRadius": "12px", "padding": "18px 20px",
              "flex": "1", "minWidth": "150px", "borderLeft": f"4px solid {color}"})


def build_kpis(df):
    if df is None or df.empty:
        return [kpi_card("No data", "—", "Check error above", "#EF4444")]
    first_t  = df["Total"].iloc[0]
    last_t   = df["Total"].iloc[-1]
    peak_t   = df["Total"].max()
    peak_m   = df.loc[df["Total"].idxmax(), "date"]
    growth   = (last_t - first_t) / first_t * 100 if first_t else 0
    mom_vals = df["MoM_Change"].dropna()
    best_mom = mom_vals.max() if not mom_vals.empty else 0
    best_m   = df.loc[df["MoM_Change"].idxmax(), "date"] if not mom_vals.empty else "—"
    return [
        kpi_card("Current Net Worth",  f"${last_t:,.0f}",  df["date"].iloc[-1],   ACCENT),
        kpi_card("Total Growth",       f"+{growth:.1f}%",  f"Since {df['date'].iloc[0]}", "#22C55E"),
        kpi_card("Peak Net Worth",     f"${peak_t:,.0f}",  f"Reached {peak_m}",   "#A78BFA"),
        kpi_card("Best Month",         f"+${best_mom:,.0f}", best_m,              "#F59E0B"),
    ]


app.layout = html.Div([
    # Header
    html.Div([
        html.Div([
            html.H1("💼 Personal Wealth Dashboard",
                    style={"margin": 0, "fontSize": "26px", "fontWeight": "700"}),
            html.P("Live data from Google Sheets · All values in USD",
                   style={"margin": "4px 0 0", "color": "#94A3B8", "fontSize": "13px"}),
        ]),
        html.Div([
            html.Button("🔄 Refresh Data", id="refresh-btn",
                        style={"background": ACCENT, "color": "white", "border": "none",
                               "borderRadius": "8px", "padding": "10px 20px",
                               "cursor": "pointer", "fontWeight": "600", "fontSize": "13px"}),
            html.Div(id="last-updated", style={"fontSize": "11px", "color": "#64748B",
                                                "marginTop": "4px", "textAlign": "right"}),
        ], style={"textAlign": "right"}),
    ], style={"padding": "20px 32px 16px", "borderBottom": "1px solid #1E3A5F",
              "display": "flex", "justifyContent": "space-between", "alignItems": "center"}),

    # Error banner (hidden when no error)
    html.Div(id="error-banner",
             style={"display": "none", "background": "#7F1D1D", "color": "#FCA5A5",
                    "padding": "12px 32px", "fontSize": "13px", "fontFamily": "monospace"}),

    # KPI row
    html.Div(id="kpi-row",
             children=build_kpis(_cached_df),
             style={"display": "flex", "gap": "14px", "padding": "18px 32px", "flexWrap": "wrap"}),

    # Tabs
    dcc.Tabs(id="tabs", value="overview", children=[
        dcc.Tab(label="📈 Net Worth",    value="overview"),
        dcc.Tab(label="🥧 Category Mix", value="breakdown"),
        dcc.Tab(label="📊 MoM Changes",  value="mom"),
        dcc.Tab(label="📉 Growth Rates", value="growth"),
        dcc.Tab(label="🔍 Asset Detail", value="detail"),
    ], style={"padding": "0 32px"},
    colors={"border": "#1E3A5F", "primary": ACCENT, "background": DARK_BG}),

    html.Div(id="tab-content", style={"padding": "0 32px 32px"}),

    # Hidden store to trigger re-render after refresh
    dcc.Store(id="data-store", data=0),

], style={"background": DARK_BG, "color": TEXT_COL, "minHeight": "100vh",
          "fontFamily": "Inter, Arial, sans-serif"})


# ── Refresh callback ──────────────────────────────────────────────────────────
@app.callback(
    Output("data-store",    "data"),
    Output("kpi-row",       "children"),
    Output("last-updated",  "children"),
    Output("error-banner",  "children"),
    Output("error-banner",  "style"),
    Input("refresh-btn",    "n_clicks"),
    prevent_initial_call=True,
)
def refresh(n):
    load_data()
    df = _cached_df
    ts = f"Last refreshed: {_last_load_time}" if _last_load_time else ""
    if _load_error:
        err_style = {"display": "block", "background": "#7F1D1D", "color": "#FCA5A5",
                     "padding": "12px 32px", "fontSize": "13px", "fontFamily": "monospace"}
        return n, build_kpis(df), ts, f"Error: {_load_error}", err_style
    return n, build_kpis(df), ts, "", {"display": "none"}


# ── Tab content ───────────────────────────────────────────────────────────────
@app.callback(
    Output("tab-content", "children"),
    Input("tabs",         "value"),
    Input("data-store",   "data"),
)
def render_tab(tab, _store):
    df = _cached_df
    if df is None or df.empty:
        return html.P("No data loaded. Click Refresh Data.",
                      style={"color": "#EF4444", "padding": "40px", "textAlign": "center"})

    def hex_to_rgba(hex_color, alpha=0.8):
        """Convert #RRGGBB to rgba(r,g,b,alpha) for plotly compatibility."""
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"

    if tab == "overview":
        fig = go.Figure()
        for cat in CATS:
            fig.add_trace(go.Scatter(
                x=df["date"], y=df[cat].round(0), name=cat,
                stackgroup="one", fill="tonexty", mode="lines",
                line=dict(width=0.5, color=PALETTE[cat]),
                fillcolor=hex_to_rgba(PALETTE[cat], 0.8),
                hovertemplate=f"<b>{cat}</b><br>%{{x}}<br>$%{{y:,.0f}}<extra></extra>"
            ))
        fig.add_trace(go.Scatter(
            x=df["date"], y=df["Total"].round(0), name="Total",
            mode="lines+markers",
            line=dict(color="#FFFFFF", width=2, dash="dot"),
            marker=dict(size=5, color="#FFFFFF"),
            hovertemplate="<b>Total</b><br>%{x}<br>$%{y:,.0f}<extra></extra>"
        ))
        fig.update_layout(**PLOT_LAYOUT, title="Net Worth Over Time (Stacked by Category)", height=460)
        return dcc.Graph(id="overview-graph", figure=fig, style={"marginTop": "20px"})

    elif tab == "breakdown":
        latest = df.iloc[-1]
        labels = [c for c in CATS if latest[c] > 0]
        values = [latest[c] for c in labels]
        fig = make_subplots(rows=1, cols=2,
            specs=[[{"type": "pie"}, {"type": "bar"}]],
            subplot_titles=[f"Allocation — {latest['date']}", "Category Growth Over Time"])
        fig.add_trace(go.Pie(
            labels=labels, values=values,
            marker_colors=[PALETTE[l] for l in labels], hole=0.45,
            textinfo="label+percent",
            hovertemplate="<b>%{label}</b><br>$%{value:,.0f}<br>%{percent}<extra></extra>"
        ), row=1, col=1)
        for cat in CATS:
            fig.add_trace(go.Bar(
                x=df["date"], y=df[cat].round(0), name=cat,
                marker_color=PALETTE[cat],
                hovertemplate=f"<b>{cat}</b><br>%{{x}}<br>$%{{y:,.0f}}<extra></extra>"
            ), row=1, col=2)
        fig.update_layout(**PLOT_LAYOUT, barmode="stack", height=460,
                          title="Category Breakdown — Pie & Stacked Bar")
        return dcc.Graph(id="breakdown-graph", figure=fig, style={"marginTop": "20px"})

    elif tab == "mom":
        mom_vals = df["MoM_Change"].fillna(0)
        colors = ["#22C55E" if v >= 0 else "#EF4444" for v in mom_vals]
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
            subplot_titles=["Month-on-Month Change ($)", "Month-on-Month Change (%)"],
            vertical_spacing=0.14)
        fig.add_trace(go.Bar(x=df["date"], y=df["MoM_Change"].fillna(0).round(0),
            marker_color=colors, name="MoM $",
            hovertemplate="%{x}<br>$%{y:,.0f}<extra></extra>"), row=1, col=1)
        fig.add_trace(go.Bar(x=df["date"], y=df["MoM_Pct"].fillna(0).round(2),
            marker_color=colors, name="MoM %",
            hovertemplate="%{x}<br>%{y:.2f}%<extra></extra>"), row=2, col=1)
        # zero reference lines via shapes (add_hline is unreliable on subplots)
        fig.update_layout(**PLOT_LAYOUT, showlegend=False, height=520,
                          title="Month-on-Month Changes",
                          shapes=[
                              dict(type="line", xref="paper", x0=0, x1=1,
                                   yref="y1", y0=0, y1=0,
                                   line=dict(color="#475569", width=1)),
                              dict(type="line", xref="paper", x0=0, x1=1,
                                   yref="y2", y0=0, y1=0,
                                   line=dict(color="#475569", width=1)),
                          ])
        return dcc.Graph(id="mom-graph", figure=fig, style={"marginTop": "20px"})

    elif tab == "growth":
        df6  = df.dropna(subset=["Cum_6M"])
        df12 = df.dropna(subset=["Cum_12M"])
        fig = go.Figure()
        if not df6.empty:
            fig.add_trace(go.Scatter(x=df6["date"], y=df6["Cum_6M"].round(2),
                name="6-Month Rolling %", mode="lines+markers",
                line=dict(color="#38BDF8", width=2),
                hovertemplate="%{x}<br>6M: %{y:.1f}%<extra></extra>"))
        if not df12.empty:
            fig.add_trace(go.Scatter(x=df12["date"], y=df12["Cum_12M"].round(2),
                name="12-Month Rolling %", mode="lines+markers",
                line=dict(color="#F472B6", width=2),
                hovertemplate="%{x}<br>12M: %{y:.1f}%<extra></extra>"))
        fig.update_layout(**PLOT_LAYOUT, height=460,
                          title="Cumulative Rolling Growth Rates (%)", yaxis_title="Growth %",
                          shapes=[dict(type="line", xref="paper", x0=0, x1=1,
                                       yref="y", y0=0, y1=0,
                                       line=dict(color="#475569", width=1))])
        return dcc.Graph(id="growth-graph", figure=fig, style={"marginTop": "20px"})

    elif tab == "detail":
        detail_cols = {
            "Current Account (GBP)": "current_acct_gbp",
            "Savings (GBP)":         "savings_gbp",
            "Van LS100 (GBP)":       "van_ls100",
            "Van ESG (GBP)":         "van_esg",
            "Van S&P500 (GBP)":      "van_sp500",
            "Crypto (GBP)":          "crypto",
            "DAZN Pension (GBP)":    "ret_dazn",
            "Tesco Pension (GBP)":   "ret_tesco",
            "Kindred Pension (GBP)": "ret_kindred",
            "LISA (GBP)":            "ret_lisa",
            "UK House Equity (GBP)": "uk_house",
            "Dubai Property (AED)":  "dubai_prop_aed",
            "Coventry (AED)":        "coventry_aed",
            "Gratuity (AED)":        "gratuity_aed",
            "Car — Camaro (AED)":    "car_aed",
        }
        options = [{"label": k, "value": v} for k, v in detail_cols.items()]
        default = ["current_acct_gbp", "savings_gbp", "van_sp500", "uk_house", "ret_tesco"]
        return html.Div([
            html.P("Select assets to compare:",
                   style={"marginTop": "20px", "color": "#94A3B8"}),
            dcc.Dropdown(id="asset-picker", options=options, value=default,
                         multi=True,
                         style={"background": CARD_BG, "color": "#000"}),
            dcc.Graph(id="detail-graph", style={"marginTop": "16px"}),
        ])

    return html.Div("Select a tab")


@app.callback(Output("detail-graph", "figure"), Input("asset-picker", "value"))
def update_detail(selected):
    df = _cached_df
    fig = go.Figure()
    if df is None or df.empty or not selected:
        return fig
    col_labels = {
        "current_acct_gbp": "Current Account (GBP)", "savings_gbp": "Savings (GBP)",
        "van_ls100": "Van LS100 (GBP)",  "van_esg": "Van ESG (GBP)",
        "van_sp500": "Van S&P500 (GBP)", "crypto": "Crypto (GBP)",
        "ret_dazn": "DAZN Pension (GBP)", "ret_tesco": "Tesco Pension (GBP)",
        "ret_kindred": "Kindred Pension (GBP)", "ret_lisa": "LISA (GBP)",
        "uk_house": "UK House Equity (GBP)",
        "dubai_prop_aed": "Dubai Property (AED)",
        "coventry_aed": "Coventry (AED)",
        "gratuity_aed": "Gratuity (AED)",
        "car_aed": "Car — Camaro (AED)",
    }
    colors = px.colors.qualitative.Plotly
    for i, col in enumerate(selected):
        if col not in df.columns:
            continue
        fig.add_trace(go.Scatter(
            x=df["date"], y=df[col].round(2),
            name=col_labels.get(col, col),
            mode="lines+markers",
            line=dict(width=2, color=colors[i % len(colors)]),
            hovertemplate=f"<b>{col_labels.get(col,col)}</b><br>%{{x}}<br>%{{y:,.2f}}<extra></extra>"
        ))
    fig.update_layout(**PLOT_LAYOUT, height=480,
                      title="Individual Asset Trends (native currency)",
                      yaxis_title="Value")
    return fig


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "="*60)
    print("  💼  Personal Wealth Dashboard — Google Sheets Live")
    print("="*60)
    if not CREDENTIALS.exists():
        print(f"\n  ⚠️  credentials.json not found.")
        print(f"  Follow the FIRST-TIME SETUP at the top of this file.\n")
    else:
        print(f"  ✅  credentials.json found")
        if TOKEN_FILE.exists():
            print(f"  ✅  token.json found — no login needed")
        else:
            print(f"  ℹ️  First run: browser will open for Google login")
    print(f"\n  Sheet ID: {SHEET_ID}")
    print(f"  Open:     http://127.0.0.1:8050")
    print("="*60 + "\n")
    app.run(debug=False, host="127.0.0.1", port=8050)
