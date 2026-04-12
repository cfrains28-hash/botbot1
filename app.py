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
        background-color: rgba(255, 255, 255, 0.05);
        padding: 15px;
        border-radius: 10px;
        text-align: center;
        border: 1px solid rgba(255, 255, 255, 0.1);
        margin-bottom: 20px;
    }
    div.row-widget.stRadio > div { flex-direction: row; align-items: center; justify-content: center; }
    </style>
    """,
    unsafe_allow_html=True
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

if 'alert_sent' not in st.session_state: st.session_state.alert_sent = False
if 'last_coin' not in st.session_state: st.session_state.last_coin = 'BTC'
if 'whale_alerts' not in st.session_state: st.session_state.whale_alerts = {}

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    params = {"chat_id": CHAT_ID, "text": message}
    try: requests.get(url, params=params)
    except: pass

# ==========================================
# 3. 데이터 로드 (멀티 서버 우회 접속 패치)
# ==========================================
@st.cache_data(ttl=10)
def load_data(interval, symbol):
    headers = {'User-Agent': 'Mozilla/5.0'}
    endpoints = [
        "https://data-api.binance.vision/api/v3/klines", 
        "https://api.binance.com/api/v3/klines", 
        "https://api1.binance.com/api/v3/klines",
        "https://api2.binance.com/api/v3/klines"
    ]
    
    data = None
    for url in endpoints:
        try:
            params = {"symbol": symbol, "interval": interval, "limit": 300}
            response = requests.get(url, params=params, headers=headers, timeout=5)
            if response.status_code == 200:
                data = response.json()
                break
        except: continue
            
    if data is None or not isinstance(data, list): return pd.DataFrame()

    df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'tbb', 'tbq', 'ignore'])
    df['time'] = pd.to_datetime(df['time'], unit='ms')
    for col in ['open', 'high', 'low', 'close', 'volume']: df[col] = pd.to_numeric(df[col])
        
    if len(df) < 60: return df

    # 지표 계산
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

    # 매물대 지지선
    bins = 50
    df['zone'] = pd.cut(df['close'], bins=bins)
    vp = df.groupby('zone', observed=False)['volume'].sum().reset_index()
    vp['mid'] = vp['zone'].apply(lambda x: x.mid).astype(float)
    support = vp[vp['mid'] < curr_p]
    poc_p = support.loc[support['volume'].idxmax(), 'mid'] if not support.empty else curr_p

    # 보수적 70점 채점
    ma_s = 40 if (curr_p > df['ma60'].iloc[-1] and df['ma5'].iloc[-1] > df['ma20'].iloc[-1] > df['ma60'].iloc[-1]) else (20 if curr_p > df['ma60'].iloc[-1] else 0)
    vol_s = 30 if v_ratio >= 3.0 else (15 if v_ratio >= 1.5 else 0)
    pos_s = 30 if curr_p >= poc_p * 0.99 else 0
    
    return ma_s + vol_s + pos_s, v_ratio, poc_p, vp

# ==========================================
# 4. 사이드바 메인 설정 및 지표
# ==========================================
st.sidebar.markdown("### ⚙️ 메인 설정")
coin_list = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE']
selected_coin = st.sidebar.selectbox("🪙 코인 선택", coin_list, index=0)
symbol = f"{selected_coin}USDT"

st.sidebar.markdown("---")
alert_price = st.sidebar.number_input(f"🚨 {selected_coin} 알림 가격(USDT):", value=0.0, format="%.2f")
auto_refresh = st.sidebar.checkbox("🔄 실시간 자동 새로고침", value=True)

# ------------------------------------------
# 5. [핵심] 백그라운드 4종 스캔 (15m, 1h, 4h, 1d)
# ------------------------------------------
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
    log_df = conn.read(worksheet=selected_coin, ttl=0)
except:
    log_df = pd.DataFrame(columns=["진입시간", "차트간격", "진입가", "승률점수", "상태", "청산시간", "청산가", "수익률(%)"])

needs_update = False
SCAN_LIST = {"15분봉": "15m", "1시간봉": "1h", "4시간봉": "4h", "일봉": "1d"}

for name, inv in SCAN_LIST.items():
    s_df = load_data(inv, symbol)
    if s_df.empty: continue
    
    s_curr_p = s_df['close'].iloc[-1]
    s_curr_t = s_df['time'].iloc[-1]
    s_score, s_v_ratio, _, _ = calculate_logic(s_df)
    
    # 정산 로직
    if not log_df.empty:
        p_idx = log_df[(log_df["상태"] == "⏳ 대기중") & (log_df["차트간격"] == name)].index
        for idx in p_idx:
            if s_curr_t > pd.to_datetime(log_df.loc[idx, "진입시간"]):
                ent_p = float(log_df.loc[idx, "진입가"])
                pnl = round(((s_curr_p - ent_p) / ent_p) * 100, 2)
                log_df.loc[idx, ["청산시간","청산가","수익률(%)","상태"]] = [str(s_curr_t), s_curr_p, pnl, "🟢 승리" if pnl > 0 else "🔴 패배"]
                needs_update = True

    # 진입 로직 (보수적 70점 + 중복 방지)
    if s_score >= 70:
        is_p = not log_df[(log_df["상태"] == "⏳ 대기중") & (log_df["차트간격"] == name)].empty if not log_df.empty else False
        is_s = (pd.to_datetime(log_df[log_df["차트간격"]==name]["진입시간"], errors='coerce') == s_curr_t).any() if not log_df.empty else False
        
        if not is_p and not is_s:
            new = pd.DataFrame([{"진입시간": str(s_curr_t), "차트간격": name, "진입가": s_curr_p, "승률점수": s_score, "상태": "⏳ 대기중", "청산시간": "-", "청산가": 0.0, "수익률(%)": 0.0}])
            log_df = pd.concat([log_df, new], ignore_index=True)
            needs_update = True

    # 고래 알림 (텔레그램)
    if s_v_ratio >= 3.0 and (s_curr_p * s_df['volume'].iloc[-1]) >= 500000:
        a_key = f"{name}_{str(s_curr_t)}"
        if a_key not in st.session_state.whale_alerts:
            is_buy = s_curr_p > s_df['open'].iloc[-1]
            send_telegram_msg(f"{'🐳매수' if is_buy else '🦈매도'} ({name})\n가: {s_curr_p:,.2f}\n점수: {s_score}")
            st.session_state.whale_alerts[a_key] = True

if needs_update:
    try: conn.update(worksheet=selected_coin, data=log_df)
    except: pass

# ------------------------------------------
# 6. 메인 화면 출력 (UI)
# ------------------------------------------
st.title(f"📈 {selected_coin} 전지적 세력 시점 V3.1")

# 화면용 차트 간격 (주봉, 월봉 추가!)
interval_ui = {
    "1분봉": "1m", "5분봉": "5m", "15분봉": "15m", "1시간봉": "1h", 
    "4시간봉": "4h", "일봉": "1d", "주봉": "1w", "월봉": "1M"
}
sel_name = st.radio("⏰ 차트 화면 간격", list(interval_ui.keys()), horizontal=True, index=5)
df_ui = load_data(interval_ui[sel_name], symbol)

if not df_ui.empty:
    u_score, u_v_ratio, u_poc, u_vp = calculate_logic(df_ui)
    u_price = df_ui['close'].iloc[-1]
    u_rsi = df_ui['rsi'].iloc[-1]
    
    # [사이드바 실시간 지표 복구]
    st.sidebar.markdown("---")
    st.sidebar.metric(label=f"현재 {selected_coin} 가격", value=f"{u_price:,.2f}")
    r_stat = "🔴 과매수" if u_rsi >= 70 else "🟢 과매도" if u_rsi <= 30 else "⚪ 중립"
    st.sidebar.markdown(f"**RSI (14):** `{u_rsi:.1f}` ({r_stat})")
    v_stat = ("🐳 고래!", "orange") if u_v_ratio >= 3.0 else ("🐬 돌고래", "blue") if u_v_ratio >= 1.5 else ("🐟 멸치", "gray")
    st.sidebar.markdown(f"**거래량 강도:** :{v_stat[1]}[{v_stat[0]} ({u_v_ratio:.1f}x)]")
    
    # [상단 점수판]
    c1, c2, c3 = st.columns(3)
    with c1: st.markdown(f"<div class='score-card'><h4>🎯 화면 승률 ({sel_name})</h4><h2 style='color:#00FF00;'>{u_score}%</h2></div>", unsafe_allow_html=True)
    with c2: st.markdown(f"<div class='score-card'><h4>현재 상태</h4><h2>{'🔥 강력 매수' if u_score >= 70 else '👀 관망'}</h2></div>", unsafe_allow_html=True)
    with c3: st.markdown(f"<div class='score-card'><h4>🛑 손절가</h4><h2 style='color:#FF4444;'>{u_poc*0.998:,.2f}</h2></div>", unsafe_allow_html=True)

# [중앙 메인 차트] (RSI 하단 차트 제거 및 매물대 색상 변경 버전)
    show_vp = st.toggle("📊 매물대 차트 켜기", value=False)
    if show_vp:
        # 🚨 [변경 1] 1행 2열 구조로 변경 (행 높이는 1.0으로 100%)
        fig = make_subplots(rows=1, cols=2, column_widths=[0.25, 0.75], row_heights=[1.0], shared_yaxes=True, horizontal_spacing=0.01)
        
        # 🚨 [변경 2] 매물대 차트 색상 변경: gray -> Viridis 컬러 스케일 적용 (showscale=False로 컬러바 숨김)
        fig.add_trace(go.Bar(x=u_vp['volume'], y=u_vp['mid'], orientation='h', marker=dict(color=u_vp['volume'], colorscale='Viridis', showscale=False), name='매물대'), row=1, col=1)
        
        c_col = 2
    else:
        # 매물대 차트를 끄면 1행 1열
        fig = make_subplots(rows=1, cols=1)
        c_col = 1
    
    # 캔들 차트 및 이평선 추가 (기존 코드 유지)
    fig.add_trace(go.Candlestick(x=df_ui['time'], open=df_ui['open'], high=df_ui['high'], low=df_ui['low'], close=df_ui['close'], name='Price'), row=1, col=c_col)
    for m, c in zip(['ma5','ma20','ma60'], ['white','orange','deepskyblue']):
        fig.add_trace(go.Scatter(x=df_ui['time'], y=df_ui[m], line=dict(color=c, width=1), name=m.upper()), row=1, col=c_col)

    # 🚨 [변경 3] RSI 그래프 추가 코드 블록 전체 삭제됨!

    # 차트 레이아웃 설정 (Rangeslider 비활성화 유지)
    fig.update_layout(height=800, template="plotly_dark", xaxis_rangeslider_visible=False, margin=dict(l=0, r=10, t=30, b=0))
    
    # X축 형식 설정 (rangeslider_visible=False 필수)
    x_format = '%m-%d %H:%M' if "분봉" in sel_name or "시간봉" in sel_name else '%Y-%m-%d'
    fig.update_xaxes(tickformat=x_format, rangeslider_visible=False, row=1, col=c_col)
    
    st.plotly_chart(fig, use_container_width=True)

# ------------------------------------------
# 7. [하단] 구글 시트 장부 (대장님 지시대로 맨 밑으로!)
# ------------------------------------------
st.markdown("---")
with st.expander("📊 구글 시트 실전 시뮬레이션 장부 (15m, 1h, 4h, 1d 통합 감시)", expanded=True):
    if not log_df.empty:
        st.dataframe(log_df.sort_values("진입시간", ascending=False), use_container_width=True)
    else:
        st.info("아직 기록된 타점이 없습니다.")

# 가격 알림 로직
if alert_price > 0 and u_price <= alert_price:
    if not st.session_state.alert_sent:
        send_telegram_msg(f"⚠️ {selected_coin} 설정가 도달! ({u_price:,.2f})")
        st.session_state.alert_sent = True
elif u_price > alert_price: st.session_state.alert_sent = False

if auto_refresh:
    time.sleep(15)
    st.rerun()