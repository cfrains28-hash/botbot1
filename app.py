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
    @media (max-width: 600px) {
        .stPlotlyChart { height: 500px !important; }
        .main-title { font-size: 1.5rem !important; }
    }
    .score-card {
        background-color: rgba(255, 255, 255, 0.05);
        padding: 15px;
        border-radius: 10px;
        text-align: center;
        border: 1px solid rgba(255, 255, 255, 0.1);
        margin-bottom: 20px;
    }
    div.row-widget.stRadio > div { flex-direction: row; align-items: center; }
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

if 'last_coin' not in st.session_state: st.session_state.last_coin = 'BTC'
if 'whale_alerts' not in st.session_state: st.session_state.whale_alerts = {}

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    params = {"chat_id": CHAT_ID, "text": message}
    try: requests.get(url, params=params)
    except: pass

# ==========================================
# 3. 데이터 로드 및 점수 계산 함수 (우회 접속 패치 완료)
# ==========================================
@st.cache_data(ttl=10)
def load_data(interval, symbol):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    # 🚨 바이낸스 서버가 막힐 때를 대비한 5개의 예비 우회로 
    endpoints = [
        "https://data-api.binance.vision/api/v3/klines", 
        "https://api.binance.com/api/v3/klines", 
        "https://api1.binance.com/api/v3/klines",
        "https://api2.binance.com/api/v3/klines",
        "https://api3.binance.com/api/v3/klines"
    ]
    
    data = None
    for url in endpoints:
        try:
            params = {"symbol": symbol, "interval": interval, "limit": 300}
            response = requests.get(url, params=params, headers=headers, timeout=5)
            if response.status_code == 200:
                data = response.json()
                break # 성공하면 바로 탈출!
        except: continue # 실패하면 다음 주소로 재시도
            
    if data is None or not isinstance(data, list): return pd.DataFrame()

    df = pd.DataFrame(data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'tbb', 'tbq', 'ignore'])
    df['time'] = pd.to_datetime(df['time'], unit='ms')
    for col in ['open', 'high', 'low', 'close', 'volume']: df[col] = pd.to_numeric(df[col])
        
    if len(df) < 60: return pd.DataFrame()

    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    df['rsi'] = 100 - (100 / (1 + (gain / loss))) 
    
    df['ma5'] = df['close'].rolling(window=5, min_periods=1).mean()
    df['ma20'] = df['close'].rolling(window=20, min_periods=1).mean()
    df['ma60'] = df['close'].rolling(window=60, min_periods=1).mean()
    return df

def calculate_score_and_vp(df):
    current_price = df['close'].iloc[-1]
    last_volume = df['volume'].iloc[-1]
    avg_volume = df['volume'].iloc[-21:-1].mean()
    vol_ratio = last_volume / avg_volume if avg_volume > 0 else 0

    # 매물대 계산
    bins = 50
    df['price_zone'] = pd.cut(df['close'], bins=bins)
    vp = df.groupby('price_zone', observed=False)['volume'].sum().reset_index()
    vp['price_mid'] = vp['price_zone'].apply(lambda x: x.mid).astype(float)
    
    support_zones = vp[vp['price_mid'] < current_price]
    if not support_zones.empty:
        vol_threshold = vp['volume'].quantile(0.75) 
        strong_supports = support_zones[support_zones['volume'] >= vol_threshold]
        if not strong_supports.empty:
            closest_idx = strong_supports['price_mid'].idxmax()
            poc_price = strong_supports.loc[closest_idx, 'price_mid']
            stop_loss_price = float(strong_supports.loc[closest_idx, 'price_zone'].left) * 0.998
        else:
            poc_idx = support_zones['volume'].idxmax()
            poc_price = support_zones.loc[poc_idx, 'price_mid']
            stop_loss_price = float(support_zones.loc[poc_idx, 'price_zone'].left) * 0.998
    else:
        poc_price = current_price
        stop_loss_price = current_price * 0.97 

    ma_score = 40 if (current_price > df['ma60'].iloc[-1] and df['ma5'].iloc[-1] > df['ma20'].iloc[-1] > df['ma60'].iloc[-1]) else (20 if current_price > df['ma60'].iloc[-1] else 0)
    vol_score = 30 if vol_ratio >= 3.0 else (15 if vol_ratio >= 1.5 else 0)
    pos_score = 30 if current_price >= poc_price * 0.99 else 0
    total_prob = ma_score + vol_score + pos_score
    
    return total_prob, vol_ratio, stop_loss_price, vp

# ==========================================
# 4. 메인 UI 및 사이드바 설정
# ==========================================
st.sidebar.markdown("### ⚙️ 메인 설정")
coin_list = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE']
selected_coin = st.sidebar.selectbox("🪙 코인 선택", coin_list, index=0)
symbol = f"{selected_coin}USDT"

if st.session_state.last_coin != selected_coin:
    st.session_state.last_coin = selected_coin
    st.session_state.whale_alerts = {} # 코인 바뀌면 알림 기록 초기화

st.title(f"📈 {selected_coin} 전지적 세력 시점 V3.0")

# ==========================================
# 5. [핵심] 백그라운드 전 구간 스캐너 & 구글 시트
# ==========================================
# 구글 시트 연결
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
    log_df = conn.read(worksheet=selected_coin, ttl=0)
    if log_df.empty or "상태" not in log_df.columns:
        log_df = pd.DataFrame(columns=["진입시간", "차트간격", "진입가", "승률점수", "상태", "청산시간", "청산가", "수익률(%)"])
except:
    log_df = pd.DataFrame(columns=["진입시간", "차트간격", "진입가", "승률점수", "상태", "청산시간", "청산가", "수익률(%)"])

needs_update = False
st.sidebar.markdown("---")
st.sidebar.markdown("### 📡 백그라운드 스캔 상태")

# 대장님이 요청하신 보수적 감시 봉 리스트
SCAN_INTERVALS = {"15분봉": "15m", "1시간봉": "1h", "4시간봉": "4h", "일봉": "1d"}

for name, inv in SCAN_INTERVALS.items():
    scan_df = load_data(inv, symbol)
    if scan_df.empty: continue
    
    curr_price = scan_df['close'].iloc[-1]
    curr_time_dt = scan_df['time'].iloc[-1]
    curr_time_str = str(curr_time_dt)
    
    score, v_ratio, _, _ = calculate_score_and_vp(scan_df)
    
    # [정산 로직] 해당 봉의 시간이 다음 캔들로 넘어갔을 때만 청산
    if not log_df.empty:
        pending_idx = log_df[(log_df["상태"] == "⏳ 대기중") & (log_df["차트간격"] == name)].index
        for idx in pending_idx:
            entry_time = pd.to_datetime(log_df.loc[idx, "진입시간"])
            if curr_time_dt > entry_time: 
                entry_price = float(log_df.loc[idx, "진입가"])
                pnl_pct = ((curr_price - entry_price) / entry_price) * 100
                
                log_df.loc[idx, "청산시간"] = curr_time_str
                log_df.loc[idx, "청산가"] = curr_price
                log_df.loc[idx, "수익률(%)"] = round(pnl_pct, 2)
                log_df.loc[idx, "상태"] = "🟢 승리" if pnl_pct > 0 else "🔴 패배"
                needs_update = True

    # [진입 로직] 70점 이상 강력 매수 시 기록
    if score >= 70:
        if log_df.empty or not ((log_df["진입시간"] == curr_time_str) & (log_df["차트간격"] == name)).any():
            new_trade = pd.DataFrame([{"진입시간": curr_time_str, "차트간격": name, "진입가": curr_price, "승률점수": score, "상태": "⏳ 대기중", "청산시간": "-", "청산가": 0.0, "수익률(%)": 0.0}])
            log_df = pd.concat([log_df, new_trade], ignore_index=True)
            needs_update = True

    # [고래 알림] 거래량 3배 이상 + 50만불 이상일 때 (텔레그램 전송)
    usdt_volume = scan_df['volume'].iloc[-1] * curr_price
    if v_ratio >= 3.0 and usdt_volume >= 500000:
        alert_key = f"{name}_{curr_time_str}"
        if alert_key not in st.session_state.whale_alerts:
            is_buy_whale = curr_price > scan_df['open'].iloc[-1]
            emoji = "🐳 [매수 찐고래]" if is_buy_whale else "🦈 [매도 상어]"
            msg = f"{emoji} ({name})\n코인: {selected_coin}\n현재가: {curr_price:,.2f}\n거래대금: ${usdt_volume:,.0f}\n🎯 알고리즘 점수: {score}점"
            send_telegram_msg(msg)
            st.session_state.whale_alerts[alert_key] = True

st.sidebar.success("✅ 4개 다중 프레임 감시 중")
if needs_update:
    try: conn.update(worksheet=selected_coin, data=log_df)
    except: pass

# ==========================================
# 6. 눈에 보이는 화면 (Foreground 차트 및 컨트롤)
# ==========================================
col_interval, col_toggle = st.columns([4, 1])
with col_interval:
    interval_dict = {"1분봉": "1m", "5분봉": "5m", "15분봉": "15m", "1시간봉": "1h", "4시간봉": "4h", "일봉": "1d"}
    selected_interval_name = st.radio("⏰ 차트 화면 간격 (화면용, 스캔은 별도 진행)", list(interval_dict.keys()), horizontal=True, index=3)
    selected_interval = interval_dict[selected_interval_name]
with col_toggle:
    st.markdown("<br>", unsafe_allow_html=True) 
    show_vp = st.toggle("📊 매물대 켜기", value=False) 

# 화면용 데이터 로드
df_ui = load_data(selected_interval, symbol)
if df_ui.empty:
    st.error("⚠️ 서버 연결 지연. 잠시 후 재시도합니다.")
    time.sleep(5)
    st.rerun()

ui_score, ui_v_ratio, ui_stop_loss, ui_vp = calculate_score_and_vp(df_ui)
ui_price = df_ui['close'].iloc[-1]

# 메인 점수판
col1, col2, col3 = st.columns(3)
with col1:
    color = "#00FF00" if ui_score >= 70 else "#FFA500" if ui_score >= 40 else "#AAAAAA"
    st.markdown(f"<div class='score-card'><h4>🎯 현재 차트 승률 ({selected_interval_name})</h4><h2 style='color:{color};'>{ui_score}%</h2></div>", unsafe_allow_html=True)
with col2:
    status = "🔥 강력 매수" if ui_score >= 70 else "👀 관망/준비" if ui_score >= 40 else "❄️ 진입 금지"
    st.markdown(f"<div class='score-card'><h4>현재 상태</h4><h2>{status}</h2></div>", unsafe_allow_html=True)
with col3:
    st.markdown(f"<div class='score-card'><h4>🛑 기계적 손절가</h4><h2 style='color:#FF4444;'>{ui_stop_loss:,.2f}</h2></div>", unsafe_allow_html=True)

# 시뮬레이션 장부 표
st.markdown("---")
with st.expander("📊 구글 시트 실전 시뮬레이션 장부 (15m, 1h, 4h, 1d 통합 감시)", expanded=True):
    if not log_df.empty:
        completed_trades = log_df[log_df["상태"] != "⏳ 대기중"]
        if not completed_trades.empty:
            win_count = len(completed_trades[completed_trades["상태"] == "🟢 승리"])
            total_count = len(completed_trades)
            col_stat1, col_stat2 = st.columns(2)
            col_stat1.metric("통합 봇 실제 승률", f"{(win_count/total_count)*100:.1f}% ({win_count}승 / {total_count-win_count}패)")
            col_stat2.metric("누적 모의 수익률", f"{completed_trades['수익률(%)'].sum():.2f}%")
        st.dataframe(log_df.sort_values(by="진입시간", ascending=False), use_container_width=True)
    else:
        st.info("아직 70점 이상의 타점이 발생하지 않았습니다.")

# 차트 그리기
if show_vp:
    fig = make_subplots(rows=2, cols=2, shared_xaxes=True, shared_yaxes=True, column_widths=[0.25, 0.75], row_heights=[0.82, 0.18], specs=[[{}, {}], [{}, {}]], horizontal_spacing=0.015, vertical_spacing=0.07, subplot_titles=("📊 매물대", f"🔥 {selected_coin} 캔들", "", "RSI (14)"))
    candle_col = 2
    fig.add_trace(go.Bar(x=ui_vp['volume'], y=ui_vp['price_mid'], orientation='h', marker=dict(color=ui_vp['volume'], colorscale='Viridis', showscale=False), name='매물대'), row=1, col=1)
    fig.update_xaxes(visible=False, row=2, col=1); fig.update_yaxes(visible=False, row=2, col=1)
else:
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.82, 0.18], vertical_spacing=0.07, subplot_titles=(f"🔥 {selected_coin} 캔들", "RSI (14)"))
    candle_col = 1

fig.add_trace(go.Candlestick(x=df_ui['time'], open=df_ui['open'], high=df_ui['high'], low=df_ui['low'], close=df_ui['close'], name='가격'), row=1, col=candle_col)
for ma, color in zip(['ma5', 'ma20', 'ma60'], ['white', 'orange', 'deepskyblue']):
    fig.add_trace(go.Scatter(x=df_ui['time'], y=df_ui[ma], line=dict(color=color, width=1.5), name=ma.upper()), row=1, col=candle_col)

# 화면 차트상의 고래 표시
whale_spots = df_ui[df_ui['volume'] > (df_ui['volume'].rolling(20).mean() * 3.0)]
for _, row in whale_spots.iterrows():
    is_buy = row['close'] > row['open']
    color = "lime" if is_buy else "red"
    fig.add_annotation(x=row['time'], y=row['low'] if is_buy else row['high'], text=f"<span style='color:{color}; font-weight:bold;'>{'🐳' if is_buy else '🦈'}</span>", showarrow=True, arrowhead=1, arrowcolor=color, ax=0, ay=35 if is_buy else -35, row=1, col=candle_col)

fig.add_hline(y=ui_stop_loss, line_dash="dash", line_color="magenta", annotation_text=f"🛑 손절가: {ui_stop_loss:,.2f}", annotation_position="bottom right", row=1, col=candle_col)
fig.add_trace(go.Scatter(x=df_ui['time'], y=df_ui['rsi'], line=dict(color='yellow', width=1.5), name='RSI'), row=2, col=candle_col)
fig.add_hline(y=70, line_dash="dot", line_color="red", row=2, col=candle_col)
fig.add_hline(y=30, line_dash="dot", line_color="green", row=2, col=candle_col)

x_format = '%m-%d %H:%M' if "분봉" in selected_interval_name or "시간봉" in selected_interval_name else '%Y-%m-%d'
fig.update_xaxes(showticklabels=False, rangeslider_visible=False, row=1, col=candle_col)
fig.update_xaxes(tickformat=x_format, rangeslider_visible=False, row=2, col=candle_col)
fig.update_layout(xaxis_rangeslider_visible=False, height=900, template="plotly_dark", dragmode="pan", margin=dict(l=0, r=40, t=50, b=0))
fig.update_yaxes(side="right", row=1, col=candle_col) 
st.plotly_chart(fig, use_container_width=True)

# 자동 새로고침 설정
auto_refresh = st.sidebar.checkbox("🔄 실시간 자동 새로고침", value=True)
if auto_refresh:
    time.sleep(15) # 15초마다 4개 차트 스캔 후 새로고침 (API 보호)
    st.rerun()