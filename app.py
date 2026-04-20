import streamlit as st
import pandas as pd
import requests
import time
from datetime import timedelta
from binance.client import Client
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from streamlit_gsheets import GSheetsConnection

# ==========================================
# 1. 웹 페이지 기본 설정 및 CSS
# ==========================================
st.set_page_config(layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
    .block-container { padding: 3rem 1rem 1rem 1rem !important; }
    [data-testid="stSidebar"] { min-width: 250px !important; max-width: 250px !important; }
    .score-card {
        background-color: rgba(255, 255, 255, 0.05); padding: 15px; border-radius: 10px;
        text-align: center; border: 1px solid rgba(255, 255, 255, 0.1); margin-bottom: 20px;
    }
    div.row-widget.stRadio > div { flex-direction: row; align-items: center; justify-content: center; }
    </style>
    """, unsafe_allow_html=True
)

# ==========================================
# 2. 시스템 초기화 및 텔레그램 설정
# ==========================================
try:
    TELEGRAM_TOKEN = st.secrets["TELEGRAM_TOKEN"]
    CHAT_ID = st.secrets["CHAT_ID"]
except:
    TELEGRAM_TOKEN = "YOUR_TOKEN_HERE"
    CHAT_ID = "YOUR_CHAT_ID_HERE"

if 'last_coin' not in st.session_state: st.session_state.last_coin = 'BTC'

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: requests.get(url, params={"chat_id": CHAT_ID, "text": message})
    except: pass

# ==========================================
# 3. 데이터 로드 및 점수 계산
# ==========================================
@st.cache_data(ttl=10)
def load_data(interval, symbol):
    headers = {'User-Agent': 'Mozilla/5.0'}
    endpoints = ["https://data-api.binance.vision/api/v3/klines", "https://api.binance.com/api/v3/klines"]
    data = None
    for url in endpoints:
        try:
            response = requests.get(url, params={"symbol": symbol, "interval": interval, "limit": 300}, headers=headers, timeout=5)
            if response.status_code == 200:
                data = response.json()
                break
        except: continue
            
    if not data: return pd.DataFrame()

    df = pd.DataFrame(data, columns=['time','open','high','low','close','volume','ct','qav','nt','tbb','tbq','i'])
    df['time'] = pd.to_datetime(df['time'], unit='ms') + pd.Timedelta(hours=9)
    for col in ['open','high','low','close','volume']: df[col] = pd.to_numeric(df[col])
        
    if len(df) < 60: return df

    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    df['rsi'] = 100 - (100 / (1 + (gain / loss))) 
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma20'] = df['close'].rolling(20).mean()
    df['ma60'] = df['close'].rolling(60).mean()
    return df

def calculate_logic(df):
    if df.empty: return 0, 0, 0, pd.DataFrame()
    curr_p = df['close'].iloc[-1]
    last_v = df['volume'].iloc[-1]
    avg_v = df['volume'].iloc[-21:-1].mean()
    v_ratio = last_v / avg_v if avg_v > 0 else 0

    bins = 50
    df['zone'] = pd.cut(df['close'], bins=bins)
    vp = df.groupby('zone', observed=False)['volume'].sum().reset_index()
    vp['mid'] = vp['zone'].apply(lambda x: x.mid).astype(float)
    support = vp[vp['mid'] < curr_p]
    poc_p = support.loc[support['volume'].idxmax(), 'mid'] if not support.empty else curr_p

    ma_s = 40 if (curr_p > df['ma60'].iloc[-1] and df['ma5'].iloc[-1] > df['ma20'].iloc[-1] > df['ma60'].iloc[-1]) else (20 if curr_p > df['ma60'].iloc[-1] else 0)
    vol_s = 30 if v_ratio >= 3.0 else (15 if v_ratio >= 1.5 else 0)
    pos_s = 30 if curr_p >= poc_p * 0.99 else 0
    return ma_s + vol_s + pos_s, v_ratio, poc_p, vp

# ==========================================
# 4. 사이드바 (드롭다운 설정 추가)
# ==========================================
st.sidebar.markdown("### ⚙️ 메인 설정")
selected_coin = st.sidebar.selectbox("🪙 코인 선택", ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE'], index=0)
symbol = f"{selected_coin}USDT"

if st.session_state.last_coin != selected_coin:
    st.session_state.last_coin = selected_coin

st.sidebar.markdown("---")
st.sidebar.markdown("### 🔫 스나이퍼 매매 설정")

# 🚨 [수정됨] 드롭다운 형식의 멀티 셀렉트 추가
SCAN_OPTIONS = {"5분봉": "5m", "15분봉": "15m", "1시간봉": "1h", "4시간봉": "4h", "일봉": "1d"}
active_names = st.sidebar.multiselect("🔍 실시간 스캔 간격 선택", options=list(SCAN_OPTIONS.keys()), default=["5분봉", "15분봉", "1시간봉"])

min_score = st.sidebar.slider("🔥 최소 진입 점수", min_value=70, max_value=100, value=90, step=5)
use_mtf = st.sidebar.toggle("🌐 다중 시간대(4H) 필터", value=True)
max_hold_candles = st.sidebar.number_input("⏳ 최대 보유 캔들 수", min_value=1, max_value=50, value=3)
leverage = st.sidebar.slider("⚡ 사용 배율 (Leverage)", min_value=1, max_value=20, value=5)
tp_roe = st.sidebar.number_input("🎯 목표 익절 (ROE %)", min_value=1.0, value=3.0, step=0.5)
use_sl = st.sidebar.toggle("🛑 마젠타 지지선 자동손절", value=True)
fee_rate = 0.05 

auto_refresh = st.sidebar.checkbox("🔄 실시간 자동 새로고침", value=True)

# ==========================================
# 5. 백그라운드 스캔 (익절/손절 및 알림)
# ==========================================
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
    log_df = conn.read(worksheet=selected_coin, ttl=0)
    if "순수익(ROE%)" not in log_df.columns:
        log_df = pd.DataFrame(columns=["진입시간", "차트간격", "진입가", "목표가", "손절가", "승률점수", "상태", "청산시간", "청산가", "순수익(ROE%)"])
except:
    log_df = pd.DataFrame(columns=["진입시간", "차트간격", "진입가", "목표가", "손절가", "승률점수", "상태", "청산시간", "청산가", "순수익(ROE%)"])

needs_update = False
INTERVAL_MINS = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}

# 🚨 [수정됨] 사용자가 드롭다운에서 선택한 간격만 반복문을 돕니다.
for name in active_names:
    inv = SCAN_OPTIONS[name]
    s_df = load_data(inv, symbol)
    if s_df.empty: continue
    
    s_curr_p = s_df['close'].iloc[-1]
    s_curr_t = s_df['time'].iloc[-1]
    s_score, s_v_ratio, s_poc, _ = calculate_logic(s_df)
    
    # [청산 로직]
    if not log_df.empty:
        p_idx = log_df[(log_df["상태"] == "⏳ 대기중") & (log_df["차트간격"] == name)].index
        for idx in p_idx:
            ent_p = float(log_df.loc[idx, "진입가"])
            tar_p = float(log_df.loc[idx, "목표가"])
            sl_p = float(log_df.loc[idx, "손절가"])
            
            gross_roe = ((s_curr_p - ent_p) / ent_p) * 100 * leverage
            net_roe = gross_roe - (fee_rate * 2 * leverage)
            
            entry_time = pd.to_datetime(log_df.loc[idx, "진입시간"])
            hold_time_limit = pd.Timedelta(minutes=INTERVAL_MINS[inv] * max_hold_candles)
            
            is_tp = s_curr_p >= tar_p
            is_sl = use_sl and (s_curr_p <= sl_p)
            is_time_over = s_curr_t >= (entry_time + hold_time_limit)
            
            if is_tp or is_sl or is_time_over:
                if is_tp: status = "🎯 익절완료"
                elif is_sl: status = "🛑 자동손절"
                else: status = "🟢 시간종료(승)" if net_roe > 0 else "🔴 시간종료(패)"
                
                log_df.loc[idx, ["청산시간", "청산가", "순수익(ROE%)", "상태"]] = [str(s_curr_t), s_curr_p, round(net_roe, 2), status]
                needs_update = True
                send_telegram_msg(f"[{status}] {selected_coin} {name}\n청산가: {s_curr_p:,.2f}\n수익률: {net_roe:.2f}%")

    # [진입 로직]
    mtf_pass = True
    if use_mtf and inv in ["5m", "15m", "1h"]:
        df_4h = load_data("4h", symbol)
        if not df_4h.empty and df_4h['close'].iloc[-1] < df_4h['ma60'].iloc[-1]:
            mtf_pass = False

    if s_score >= min_score and mtf_pass:
        is_p = not log_df[(log_df["상태"] == "⏳ 대기중") & (log_df["차트간격"] == name)].empty if not log_df.empty else False
        is_s = (pd.to_datetime(log_df[log_df["차트간격"]==name]["진입시간"], errors='coerce') == s_curr_t).any() if not log_df.empty else False
        
        if not is_p and not is_s:
            target_price = s_curr_p * (1 + (tp_roe / 100 / leverage))
            stop_price = s_poc * 0.990
            
            new = pd.DataFrame([{
                "진입시간": str(s_curr_t), "차트간격": name, "진입가": s_curr_p, 
                "목표가": round(target_price, 2), "손절가": round(stop_price, 2), 
                "승률점수": s_score, "상태": "⏳ 대기중", "청산시간": "-", "청산가": 0.0, "순수익(ROE%)": 0.0
            }])
            log_df = pd.concat([log_df, new], ignore_index=True)
            needs_update = True
            send_telegram_msg(f"🚨 [신규진입] {selected_coin} {name}\n진입가: {s_curr_p:,.2f}\n목표가: {target_price:,.2f}\n점수: {s_score}점")

if needs_update:
    try: conn.update(worksheet=selected_coin, data=log_df)
    except: pass

# ==========================================
# 6. 메인 화면 UI 및 차트
# ==========================================
st.title(f"📈 {selected_coin} 전지적 세력 시점 V3.4 (드롭다운 필터)")

interval_ui = {"1분봉": "1m", "5분봉": "5m", "15분봉": "15m", "1시간봉": "1h", "4시간봉": "4h", "일봉": "1d"}
sel_name = st.radio("⏰ 차트 화면 간격", list(interval_ui.keys()), horizontal=True, index=2)
df_ui = load_data(interval_ui[sel_name], symbol)

if not df_ui.empty:
    u_score, u_v_ratio, u_poc, u_vp = calculate_logic(df_ui)
    u_price = df_ui['close'].iloc[-1]
    u_rsi = df_ui['rsi'].iloc[-1]
    
    st.sidebar.markdown("---")
    st.sidebar.metric(label=f"현재 {selected_coin} 가격", value=f"{u_price:,.2f}")
    st.sidebar.markdown(f"**RSI (14):** `{u_rsi:.1f}`")
    v_stat = ("🐳 고래!", "orange") if u_v_ratio >= 3.0 else ("🐬 돌고래", "blue") if u_v_ratio >= 1.5 else ("🐟 멸치", "gray")
    st.sidebar.markdown(f"**거래량 강도:** :{v_stat[1]}[{v_stat[0]} ({u_v_ratio:.1f}x)]")
    
    c1, c2, c3 = st.columns(3)
    with c1: st.markdown(f"<div class='score-card'><h4>🎯 화면 승률 ({sel_name})</h4><h2 style='color:#00FF00;'>{u_score}%</h2></div>", unsafe_allow_html=True)
    with c2: st.markdown(f"<div class='score-card'><h4>현재 상태</h4><h2>{'🔥 강력 매수' if u_score >= min_score else '👀 관망'}</h2></div>", unsafe_allow_html=True)
    with c3: st.markdown(f"<div class='score-card'><h4>🛑 화면상 지지선 (참고용)</h4><h2 style='color:#FF4444;'>{u_poc*0.998:,.2f}</h2></div>", unsafe_allow_html=True)

    show_vp = st.toggle("📊 매물대 차트 켜기", value=False)
    fig = make_subplots(rows=2, cols=2, shared_xaxes=True, shared_yaxes=True, column_widths=[0.2, 0.8], row_heights=[0.8, 0.2], specs=[[{"secondary_y": False}, {"secondary_y": True}], [{"colspan": 2}, None]], horizontal_spacing=0.01, vertical_spacing=0.05) if show_vp else make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.8, 0.2], vertical_spacing=0.05)
    
    c_col = 2 if show_vp else 1
    if show_vp: fig.add_trace(go.Bar(x=u_vp['volume'], y=u_vp['mid'], orientation='h', marker=dict(color='rgba(100,100,255,0.3)'), name='매물대'), row=1, col=1)
    
    fig.add_trace(go.Candlestick(x=df_ui['time'], open=df_ui['open'], high=df_ui['high'], low=df_ui['low'], close=df_ui['close'], name='Price'), row=1, col=c_col)
    for m, c in zip(['ma5','ma20','ma60'], ['white','orange','deepskyblue']):
        fig.add_trace(go.Scatter(x=df_ui['time'], y=df_ui[m], line=dict(color=c, width=1), name=m.upper()), row=1, col=c_col)
    
    fig.add_hline(y=u_poc*0.998, line_dash="dash", line_color="magenta", annotation_text="🛑 손절선", row=1, col=c_col)
    fig.add_trace(go.Scatter(x=df_ui['time'], y=df_ui['rsi'], line=dict(color='yellow', width=1.5), name='RSI'), row=2, col=1 if not show_vp else 1)
    fig.update_layout(height=800, template="plotly_dark", margin=dict(l=0, r=10, t=30, b=0), showlegend=False)
    fig.update_xaxes(rangeslider_visible=False) 
    
    st.plotly_chart(fig, use_container_width=True)

# ==========================================
# 7. 구글 시트 장부
# ==========================================
st.markdown("---")
with st.expander("📊 실전 시뮬레이션 장부", expanded=True):
    if not log_df.empty:
        st.dataframe(log_df.sort_values("진입시간", ascending=False), use_container_width=True)
    else:
        st.info(f"아직 기록된 타점이 없습니다. ({min_score}점 이상 대기 중)")

if auto_refresh:
    time.sleep(15)
    st.rerun()