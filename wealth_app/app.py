"""
💼 Personal Wealth Dashboard — Cloud Edition
Reads live from Google Sheets via Service Account (no login needed).
Deployed on Render — works on any device including iPhone.
"""

import os, json, traceback
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import dash
from dash import dcc, html, Input, Output

# ── Config ────────────────────────────────────────────────────────────────────
SHEET_ID  = "1h5GD2Sn-5Jo96IR3_lIHYGIhWTUk564RDSU13wSSC48"
RAW_SHEET = "Raw Data (GBP & AED)"
FX_SHEET  = "FX & Assumptions"
AED_USD_FIXED = 1 / 3.6725
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# ── Service Account Auth ──────────────────────────────────────────────────────
def get_sheets_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    # Render stores the JSON as an env var to avoid committing secrets to git
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        raise EnvironmentError(
            "GOOGLE_SERVICE_ACCOUNT_JSON environment variable not set.\n"
            "Add it in Render → Environment → Add Environment Variable."
        )
    info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds).spreadsheets()


def fetch_sheet_values(service, sheet_name):
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


# ── Column mapping ────────────────────────────────────────────────────────────
# A=Date, B=CurrAcct, C=Savings, D=LS100, E=ESG, F=SP500, G=Crypto,
# H=DAZN, I=Tesco, J=Kindred, K=LISA,
# L=UK House (GBP), M=Dubai (AED), N=Coventry (AED), O=Gratuity (AED), P=Car (AED)
RAW_COL_MAP = {
    "date": 0, "current_acct_gbp": 1, "savings_gbp": 2,
    "van_ls100": 3, "van_esg": 4, "van_sp500": 5, "crypto": 6,
    "ret_dazn": 7, "ret_tesco": 8, "ret_kindred": 9, "ret_lisa": 10,
    "uk_house": 11, "dubai_prop_aed": 12, "coventry_aed": 13,
    "gratuity_aed": 14, "car_aed": 15,
}

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


def parse_raw_data(rows):
    seen, order = {}, []
    for row in rows[3:]:
        if not row or not row[0] or not str(row[0]).strip():
            continue
        date = str(row[0]).strip()
        if not date[:4].isdigit():
            continue
        def g(idx, r=row):
            return safe_float(r[idx]) if idx < len(r) else 0.0
        rec = {k: (date if k == "date" else g(v)) for k, v in RAW_COL_MAP.items()}
        if date not in seen:
            order.append(date)
        seen[date] = rec
    return [seen[d] for d in order]


def parse_fx_rates(rows):
    fx, aed_usd = {}, AED_USD_FIXED
    if len(rows) >= 3 and len(rows[2]) >= 2:
        v = safe_float(rows[2][1], 0)
        if v > 0:
            aed_usd = v
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
    return fx, aed_usd


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
        rows_out.append({**r, "rate": rate, "Liquid": liquid, "Investments": invest,
                         "Retirement": retire, "Property": prop, "Other": other, "Total": total})
    df = pd.DataFrame(rows_out)
    if df.empty:
        return df
    df["MoM_Change"] = df["Total"].diff()
    df["MoM_Pct"]    = df["Total"].pct_change() * 100
    df["Cum_6M"]     = df["Total"].pct_change(6) * 100
    df["Cum_12M"]    = df["Total"].pct_change(12) * 100
    return df


# ── Data loader ───────────────────────────────────────────────────────────────
_last_load_time = None
_cached_df      = None
_load_error     = None

def load_data():
    global _last_load_time, _cached_df, _load_error
    try:
        service  = get_sheets_service()
        raw_rows = fetch_sheet_values(service, RAW_SHEET)
        fx_rows  = fetch_sheet_values(service, FX_SHEET)
        raw_recs = parse_raw_data(raw_rows)
        fx_rates, aed_usd = parse_fx_rates(fx_rows)
        _cached_df = build_df(raw_recs, fx_rates, aed_usd)
        _load_error = None
        _last_load_time = datetime.now().strftime("%d %b %Y %H:%M UTC")
        print(f"✅ Loaded {len(_cached_df)} months at {_last_load_time}")
    except Exception as e:
        _load_error = str(e)
        print(f"❌ Error: {e}")
        traceback.print_exc()

load_data()

# ── Theme ─────────────────────────────────────────────────────────────────────
CATS    = ["Liquid", "Investments", "Retirement", "Property", "Other"]
PALETTE = {"Liquid": "#2196F3", "Investments": "#4CAF50",
           "Retirement": "#FF9800", "Property": "#9C27B0",
           "Other": "#607D8B"}
DARK_BG  = "#0F172A"
CARD_BG  = "#1E293B"
TEXT_COL = "#E2E8F0"
ACCENT   = "#3B82F6"

PLOT_LAYOUT = dict(
    paper_bgcolor=CARD_BG, plot_bgcolor=DARK_BG,
    font=dict(color=TEXT_COL, family="Inter, Arial, sans-serif", size=12),
    margin=dict(l=40, r=16, t=48, b=40),
    legend=dict(bgcolor=CARD_BG, bordercolor="#334155", borderwidth=1),
    xaxis=dict(gridcolor="#1E3A5F", zerolinecolor="#334155"),
    yaxis=dict(gridcolor="#1E3A5F", zerolinecolor="#334155"),
)

def hex_to_rgba(hex_color, alpha=0.8):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
    return f"rgba({r},{g},{b},{alpha})"

# ── App ───────────────────────────────────────────────────────────────────────
app = dash.Dash(__name__, title="💼 Wealth Dashboard", suppress_callback_exceptions=True,
                meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}])
server = app.server  # expose Flask server for Render

def kpi_card(label, value, sub="", color=ACCENT):
    return html.Div([
        html.P(label, style={"margin": 0, "fontSize": "10px", "color": "#94A3B8",
                             "textTransform": "uppercase", "letterSpacing": "1px"}),
        html.H3(value, style={"margin": "3px 0", "fontSize": "20px",
                              "color": color, "fontWeight": "700"}),
        html.P(sub, style={"margin": 0, "fontSize": "10px", "color": "#64748B"}),
    ], style={"background": CARD_BG, "borderRadius": "10px", "padding": "14px 16px",
              "flex": "1", "minWidth": "130px", "borderLeft": f"4px solid {color}"})

def build_kpis(df):
    if df is None or df.empty:
        return [kpi_card("No data", "—", "Check error", "#EF4444")]
    first_t = df["Total"].iloc[0]
    last_t  = df["Total"].iloc[-1]
    peak_t  = df["Total"].max()
    peak_m  = df.loc[df["Total"].idxmax(), "date"]
    growth  = (last_t - first_t) / first_t * 100 if first_t else 0
    mom_vals = df["MoM_Change"].dropna()
    best_mom = mom_vals.max() if not mom_vals.empty else 0
    best_m   = df.loc[df["MoM_Change"].idxmax(), "date"] if not mom_vals.empty else "—"
    return [
        kpi_card("Net Worth",     f"${last_t:,.0f}",    df["date"].iloc[-1],  ACCENT),
        kpi_card("Total Growth",  f"+{growth:.1f}%",    f"Since {df['date'].iloc[0]}", "#22C55E"),
        kpi_card("Peak",          f"${peak_t:,.0f}",    peak_m,               "#A78BFA"),
        kpi_card("Best Month",    f"+${best_mom:,.0f}", best_m,               "#F59E0B"),
    ]

app.layout = html.Div([
    # Header
    html.Div([
        html.Div([
            html.H1("💼 Wealth Dashboard",
                    style={"margin": 0, "fontSize": "20px", "fontWeight": "700"}),
            html.P("Live · Google Sheets · USD",
                   style={"margin": "2px 0 0", "color": "#94A3B8", "fontSize": "11px"}),
        ]),
        html.Div([
            html.Button("🔄", id="refresh-btn", title="Refresh data",
                        style={"background": ACCENT, "color": "white", "border": "none",
                               "borderRadius": "8px", "padding": "8px 14px",
                               "cursor": "pointer", "fontWeight": "700", "fontSize": "16px"}),
            html.Div(id="last-updated",
                     style={"fontSize": "10px", "color": "#64748B", "marginTop": "3px", "textAlign": "right"}),
        ]),
    ], style={"padding": "16px 16px 12px", "borderBottom": "1px solid #1E3A5F",
              "display": "flex", "justifyContent": "space-between", "alignItems": "center"}),

    # Error banner
    html.Div(id="error-banner", style={"display": "none"}),

    # KPIs
    html.Div(id="kpi-row", children=build_kpis(_cached_df),
             style={"display": "flex", "gap": "10px", "padding": "12px 16px", "flexWrap": "wrap"}),

    # Tabs — mobile friendly labels
    dcc.Tabs(id="tabs", value="overview", children=[
        dcc.Tab(label="📈 Net Worth",  value="overview"),
        dcc.Tab(label="🥧 Mix",        value="breakdown"),
        dcc.Tab(label="📊 MoM",        value="mom"),
        dcc.Tab(label="📉 Growth",     value="growth"),
        dcc.Tab(label="🔍 Detail",     value="detail"),
    ], style={"padding": "0 16px"},
    colors={"border": "#1E3A5F", "primary": ACCENT, "background": DARK_BG}),

    html.Div(id="tab-content", style={"padding": "0 16px 24px"}),
    dcc.Store(id="data-store", data=0),

], style={"background": DARK_BG, "color": TEXT_COL, "minHeight": "100vh",
          "fontFamily": "Inter, Arial, sans-serif", "maxWidth": "100vw", "overflowX": "hidden"})


@app.callback(
    Output("data-store",   "data"),
    Output("kpi-row",      "children"),
    Output("last-updated", "children"),
    Output("error-banner", "children"),
    Output("error-banner", "style"),
    Input("refresh-btn",   "n_clicks"),
    prevent_initial_call=True,
)
def refresh(n):
    load_data()
    ts = f"Updated {_last_load_time}" if _last_load_time else ""
    if _load_error:
        err_style = {"display": "block", "background": "#7F1D1D", "color": "#FCA5A5",
                     "padding": "10px 16px", "fontSize": "12px", "fontFamily": "monospace"}
        return n, build_kpis(_cached_df), ts, f"Error: {_load_error}", err_style
    return n, build_kpis(_cached_df), ts, "", {"display": "none"}


@app.callback(
    Output("tab-content", "children"),
    Input("tabs",         "value"),
    Input("data-store",   "data"),
)
def render_tab(tab, _):
    df = _cached_df
    if df is None or df.empty:
        return html.P("No data. Tap 🔄 to refresh.",
                      style={"color": "#EF4444", "padding": "40px", "textAlign": "center"})

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
            mode="lines+markers", line=dict(color="#FFFFFF", width=2, dash="dot"),
            marker=dict(size=4, color="#FFFFFF"),
            hovertemplate="<b>Total</b><br>%{x}<br>$%{y:,.0f}<extra></extra>"
        ))
        fig.update_layout(**PLOT_LAYOUT, title="Net Worth Over Time", height=380)
        return dcc.Graph(id="overview-graph", figure=fig, style={"marginTop": "16px"},
                         config={"displayModeBar": False})

    elif tab == "breakdown":
        latest = df.iloc[-1]
        labels = [c for c in CATS if latest[c] > 0]
        values = [latest[c] for c in labels]
        fig = go.Figure(go.Pie(
            labels=labels, values=values,
            marker_colors=[PALETTE[l] for l in labels], hole=0.5,
            textinfo="label+percent",
            hovertemplate="<b>%{label}</b><br>$%{value:,.0f}<br>%{percent}<extra></extra>"
        ))
        fig.update_layout(**PLOT_LAYOUT, title=f"Allocation — {latest['date']}", height=360)
        return dcc.Graph(id="breakdown-graph", figure=fig, style={"marginTop": "16px"},
                         config={"displayModeBar": False})

    elif tab == "mom":
        mom_vals = df["MoM_Change"].fillna(0)
        colors = ["#22C55E" if v >= 0 else "#EF4444" for v in mom_vals]
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
            subplot_titles=["Change ($)", "Change (%)"], vertical_spacing=0.14)
        fig.add_trace(go.Bar(x=df["date"], y=df["MoM_Change"].fillna(0).round(0),
            marker_color=colors, hovertemplate="%{x}<br>$%{y:,.0f}<extra></extra>"), row=1, col=1)
        fig.add_trace(go.Bar(x=df["date"], y=df["MoM_Pct"].fillna(0).round(2),
            marker_color=colors, hovertemplate="%{x}<br>%{y:.2f}%<extra></extra>"), row=2, col=1)
        fig.update_layout(**PLOT_LAYOUT, showlegend=False, height=440,
                          title="Month-on-Month Changes",
                          shapes=[
                              dict(type="line", xref="paper", x0=0, x1=1,
                                   yref="y1", y0=0, y1=0, line=dict(color="#475569", width=1)),
                              dict(type="line", xref="paper", x0=0, x1=1,
                                   yref="y2", y0=0, y1=0, line=dict(color="#475569", width=1)),
                          ])
        return dcc.Graph(id="mom-graph", figure=fig, style={"marginTop": "16px"},
                         config={"displayModeBar": False})

    elif tab == "growth":
        df6  = df.dropna(subset=["Cum_6M"])
        df12 = df.dropna(subset=["Cum_12M"])
        fig = go.Figure()
        if not df6.empty:
            fig.add_trace(go.Scatter(x=df6["date"], y=df6["Cum_6M"].round(2),
                name="6M Rolling %", mode="lines+markers",
                line=dict(color="#38BDF8", width=2),
                hovertemplate="%{x}<br>6M: %{y:.1f}%<extra></extra>"))
        if not df12.empty:
            fig.add_trace(go.Scatter(x=df12["date"], y=df12["Cum_12M"].round(2),
                name="12M Rolling %", mode="lines+markers",
                line=dict(color="#F472B6", width=2),
                hovertemplate="%{x}<br>12M: %{y:.1f}%<extra></extra>"))
        fig.update_layout(**PLOT_LAYOUT, height=380, title="Rolling Growth Rates (%)",
                          yaxis_title="Growth %",
                          shapes=[dict(type="line", xref="paper", x0=0, x1=1,
                                       yref="y", y0=0, y1=0,
                                       line=dict(color="#475569", width=1))])
        return dcc.Graph(id="growth-graph", figure=fig, style={"marginTop": "16px"},
                         config={"displayModeBar": False})

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
            html.P("Select assets:", style={"marginTop": "16px", "color": "#94A3B8", "fontSize": "13px"}),
            dcc.Dropdown(id="asset-picker", options=options, value=default,
                         multi=True, style={"color": "#000"}),
            dcc.Graph(id="detail-graph", style={"marginTop": "12px"},
                      config={"displayModeBar": False}),
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
        "van_ls100": "Van LS100 (GBP)", "van_esg": "Van ESG (GBP)",
        "van_sp500": "Van S&P500 (GBP)", "crypto": "Crypto (GBP)",
        "ret_dazn": "DAZN Pension (GBP)", "ret_tesco": "Tesco Pension (GBP)",
        "ret_kindred": "Kindred Pension (GBP)", "ret_lisa": "LISA (GBP)",
        "uk_house": "UK House Equity (GBP)", "dubai_prop_aed": "Dubai Property (AED)",
        "coventry_aed": "Coventry (AED)", "gratuity_aed": "Gratuity (AED)",
        "car_aed": "Car — Camaro (AED)",
    }
    colors = px.colors.qualitative.Plotly
    for i, col in enumerate(selected):
        if col not in df.columns:
            continue
        fig.add_trace(go.Scatter(
            x=df["date"], y=df[col].round(2),
            name=col_labels.get(col, col), mode="lines+markers",
            line=dict(width=2, color=colors[i % len(colors)]),
            hovertemplate=f"<b>{col_labels.get(col,col)}</b><br>%{{x}}<br>%{{y:,.2f}}<extra></extra>"
        ))
    fig.update_layout(**PLOT_LAYOUT, height=400,
                      title="Asset Trends (native currency)", yaxis_title="Value")
    return fig


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8050))
    app.run(debug=False, host="0.0.0.0", port=port)
