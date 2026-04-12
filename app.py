import streamlit as st
import pandas as pd
import requests
import time
from binance.client import Client
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 1. 웹 페이지 기본 설정
st.set_page_config(layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
    /* 🚨 [수정] 상단 여백(top)을 1rem -> 3rem으로 늘려서 타이틀 짤림 방지! */
    .block-container { padding: 7rem 1rem 1rem 1rem !important; } /* 시계방항 위->오->아->왼*/
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
    /* 라디오 버튼(분봉 선택) 텍스트 정렬 보정 */
    div.row-widget.stRadio > div { flex-direction: row; align-items: center; }
    </style>
    """,
    unsafe_allow_html=True
)

try:
    TELEGRAM_TOKEN = st.secrets["TELEGRAM_TOKEN"]
    CHAT_ID = st.secrets["CHAT_ID"]
except:
    TELEGRAM_TOKEN = "YOUR_TOKEN_HERE"
    CHAT_ID = "YOUR_CHAT_ID_HERE"

if 'alert_sent' not in st.session_state: st.session_state.alert_sent = False
if 'last_coin' not in st.session_state: st.session_state.last_coin = 'BTC'

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    params = {"chat_id": CHAT_ID, "text": message}
    try: requests.get(url, params=params)
    except: pass

@st.cache_data(ttl=10)
def load_data(interval, symbol):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
    endpoints = ["https://data-api.binance.vision/api/v3/klines", "https://api.binance.com/api/v3/klines", "https://api1.binance.com/api/v3/klines"]
    
    data = None
    for url in endpoints:
        try:
            params = {"symbol": symbol, "interval": interval, "limit": 500}
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

    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    df['rsi'] = 100 - (100 / (1 + (gain / loss))) 
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma20'] = df['close'].rolling(window=20).mean()
    df['ma60'] = df['close'].rolling(window=60).mean()
    
    return df

# --- 🚨 [수정] 사이드바 최상단으로 '코인 선택' 이동 ---
st.sidebar.markdown("### ⚙️ 메인 설정")
coin_list = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE']
selected_coin = st.sidebar.selectbox("🪙 코인 선택", coin_list, index=0)
symbol = f"{selected_coin}USDT"

if st.session_state.last_coin != selected_coin:
    st.session_state.alert_sent = False
    st.session_state.last_coin = selected_coin

# 타이틀 출력
st.title(f"📈 {selected_coin} 세력 감시 센터 V2.4")

score_container = st.container()   
control_container = st.container() 
chart_container = st.container()   

# --- 🚨 [수정] 메인 컨트롤 패널에는 '차트 간격(분봉)'만 깔끔하게 남김 ---
with control_container:
    interval_dict = {
        "1분봉": Client.KLINE_INTERVAL_1MINUTE, 
        "3분봉": Client.KLINE_INTERVAL_3MINUTE, 
        "5분봉": Client.KLINE_INTERVAL_5MINUTE, 
        "15분봉": Client.KLINE_INTERVAL_15MINUTE, 
        "1시간봉": Client.KLINE_INTERVAL_1HOUR, 
        "4시간봉": Client.KLINE_INTERVAL_4HOUR, 
        "일봉": Client.KLINE_INTERVAL_1DAY, 
        "주봉": Client.KLINE_INTERVAL_1WEEK
    }
    selected_interval_name = st.radio("⏰ 차트 간격", list(interval_dict.keys()), horizontal=True, index=3)
    selected_interval = interval_dict[selected_interval_name]

# 데이터 로딩 및 계산
df = load_data(selected_interval, symbol)
if df.empty:
    st.error("⚠️ 서버 연결 지연. 잠시 후 재시도합니다.")
    time.sleep(5)
    st.rerun()
    st.stop()

current_price = df['close'].iloc[-1]
current_rsi = df['rsi'].iloc[-1]
last_volume = df['volume'].iloc[-1]
avg_volume = df['volume'].iloc[-21:-1].mean()
vol_ratio = last_volume / avg_volume

bins = st.sidebar.slider("매물대 분할 개수:", 10, 100, 50)
df['price_zone'] = pd.cut(df['close'], bins=bins)
vp = df.groupby('price_zone', observed=False)['volume'].sum().reset_index()
vp['price_mid'] = vp['price_zone'].apply(lambda x: x.mid).astype(float)

# ==========================================
# 🛠️ [신규 코드] 스마트 지지선 추적 로직 적용
# ==========================================
# 1. 현재 가격보다 '낮은' 매물대만 필터링합니다.
support_zones = vp[vp['price_mid'] < current_price]

if not support_zones.empty:
    # 2. 발밑에 있는 매물대 중에서 가장 튼튼한 바닥(최대 거래량)을 찾습니다.
    poc_idx = support_zones['volume'].idxmax()
    poc_price = support_zones.loc[poc_idx, 'price_mid']
    poc_bottom = float(support_zones.loc[poc_idx, 'price_zone'].left)
    stop_loss_price = poc_bottom * 0.998  # 그 바닥이 깨지면 손절 (-0.2% 버퍼)
else:
    # 3. [안전장치] 만약 밑에 매물대가 아예 없는 '완전 지하실(신저가)'이라면?
    # 현재가 기준 -3%를 기계적 방어선으로 설정합니다.
    poc_price = current_price
    stop_loss_price = current_price * 0.97

ma_score = 40 if (current_price > df['ma60'].iloc[-1] and df['ma5'].iloc[-1] > df['ma20'].iloc[-1] > df['ma60'].iloc[-1]) else (20 if current_price > df['ma60'].iloc[-1] else 0)
vol_score = 30 if vol_ratio >= 3.0 else (15 if vol_ratio >= 1.5 else 0)
pos_score = 30 if current_price >= poc_price * 0.99 else 0
total_prob = ma_score + vol_score + pos_score

# 점수판 출력
with score_container:
    col1, col2, col3 = st.columns(3)
    with col1:
        color = "#00FF00" if total_prob >= 70 else "#FFA500" if total_prob >= 40 else "#AAAAAA"
        st.markdown(f"<div class='score-card'><h4>🎯 진입 승률 ({selected_interval_name})</h4><h2 style='color:{color};'>{total_prob}%</h2></div>", unsafe_allow_html=True)
    with col2:
        status = "🔥 강력 매수" if total_prob >= 70 else "👀 관망/준비" if total_prob >= 40 else "❄️ 진입 금지"
        st.markdown(f"<div class='score-card'><h4>현재 상태</h4><h2>{status}</h2></div>", unsafe_allow_html=True)
    with col3:
        st.markdown(f"<div class='score-card'><h4>🛑 기계적 손절가</h4><h2 style='color:#FF4444;'>{stop_loss_price:,.2f}</h2></div>", unsafe_allow_html=True)

# 사이드바 하단 정보 추가
st.sidebar.markdown("---")
auto_refresh = st.sidebar.checkbox("🔄 실시간 새로고침 (ON/OFF)", value=True)
alert_price = st.sidebar.number_input(f"🚨 {selected_coin} 알림 가격(USDT):", value=0.0, format="%.4f")
st.sidebar.markdown("---")
st.sidebar.metric(label=f"현재 {selected_coin} 가격", value=f"{current_price:,.4f}")
rsi_status = "🔴 과매수" if current_rsi >= 70 else "🟢 과매도" if current_rsi <= 30 else "⚪ 중립"
st.sidebar.markdown(f"**RSI (14):** `{current_rsi:.1f}` ({rsi_status})")
vol_info = ("🐋 고래!", "orange") if vol_ratio >= 3.0 else ("🐬 돌고래", "skyblue") if vol_ratio >= 1.5 else ("🐟 멸치", "gray")
st.sidebar.markdown(f"**거래량 강도:** :{vol_info[1]}[{vol_info[0]} ({vol_ratio:.1f}x)]")

if alert_price > 0 and current_price <= alert_price:
    if not st.session_state.alert_sent: 
        send_telegram_msg(f"⚠️ [{selected_coin} 도달]\n현재가: {current_price:,.4f}")
        st.session_state.alert_sent = True 
elif current_price > alert_price: st.session_state.alert_sent = False

# ==========================================
# 🚨 [수정] 텔레그램 알림: 찐 고래 & 고승률 필터링
# ==========================================
# 1. 현재 캔들에서 터진 '진짜 돈(USDT)' 계산
usdt_volume = last_volume * current_price 
min_usdt_threshold = 500000 # 최소 기준: 50만 달러 (약 6.5억 원) - 입맛에 맞게 조절하세요!

# 2. 호들갑 방지 3중 필터 적용
# (거래량 3배 이상) AND (50만 달러 이상 썼을 것) AND (승률이 40% 이상인 볼만한 자리일 것)
if vol_ratio >= 3.0 and usdt_volume >= min_usdt_threshold and total_prob >= 40:
    if 'last_whale_time' not in st.session_state or st.session_state.last_whale_time != df['time'].iloc[-1]:
        
        current_open = df['open'].iloc[-1]
        is_buy_whale = current_price > current_open
        
        if is_buy_whale:
            whale_title = "🐳 [매수 찐고래 출현]"
            whale_desc = "세력이 뭉칫돈으로 위로 긁었습니다!"
        else:
            whale_title = "🦈 [매도 큰상어 투하]"
            whale_desc = "세력이 뭉칫돈을 시장가로 던졌습니다!"

        msg = f"{whale_title} ({selected_interval_name})\n"
        msg += f"코인: {selected_coin}\n"
        msg += f"현재가: {current_price:,.4f}\n"
        msg += f"터진금액: ${usdt_volume:,.0f}\n" # 얼마 썼는지도 알려줍니다!
        msg += f"상황: {whale_desc}\n"
        msg += f"🎯 진입승률: {total_prob}%"
        
        send_telegram_msg(msg)
        st.session_state.last_whale_time = df['time'].iloc[-1]

# 차트 그리기
with chart_container:
    fig = make_subplots(
        rows=2, cols=2, shared_xaxes=True, shared_yaxes=True, 
        column_widths=[0.3, 0.7], row_heights=[0.82, 0.18], 
        specs=[[{}, {}], [None, {}]], horizontal_spacing=0.015, vertical_spacing=0.07,
        subplot_titles=("매물대 분석", f"{selected_coin} 실시간 캔들", "RSI (14)")
    )

    fig.add_trace(go.Bar(x=vp['volume'], y=vp['price_mid'], orientation='h', marker=dict(color=vp['volume'], colorscale='Viridis', showscale=False), name='매물대'), row=1, col=1)
    fig.add_trace(go.Candlestick(x=df['time'], open=df['open'], high=df['high'], low=df['low'], close=df['close'], name='가격'), row=1, col=2)
    for ma, color in zip(['ma5', 'ma20', 'ma60'], ['lightgreen', 'orange', 'deepskyblue']):
        fig.add_trace(go.Scatter(x=df['time'], y=df[ma], line=dict(color=color, width=1.5), name=ma.upper()), row=1, col=2)

    # ==========================================
    # 🚨 [수정 2] 차트 아이콘: 매수(🐳)/매도(🦈) 구분해서 찍기
    # ==========================================
    whale_spots = df[df['volume'] > (df['volume'].rolling(window=20).mean() * 3.0)]
    for _, row in whale_spots.iterrows():
        is_buy = row['close'] > row['open']
        
        # 매수는 캔들 아래에 띄우고, 매도는 캔들 위에 띄움
        emoji = "🐳매수" if is_buy else "🦈매도"
        color = "lime" if is_buy else "red"
        y_position = row['low'] if is_buy else row['high']
        ay_value = 35 if is_buy else -35
        
        fig.add_annotation(
            x=row['time'], y=y_position, 
            text=f"<span style='color:{color}; font-weight:bold;'>{emoji}</span>", 
            showarrow=True, arrowhead=1, arrowcolor=color, 
            ax=0, ay=ay_value, 
            row=1, col=2
        )

    fig.add_hline(y=stop_loss_price, line_dash="dash", line_color="magenta", annotation_text=f"🛑 자동 손절선", annotation_position="bottom right", row=1, col=2)
    fig.add_trace(go.Scatter(x=df['time'], y=df['rsi'], line=dict(color='yellow', width=1.5), name='RSI'), row=2, col=2)
    fig.add_hline(y=70, line_dash="dot", line_color="red", row=2, col=2)
    fig.add_hline(y=30, line_dash="dot", line_color="green", row=2, col=2)

    fig.update_xaxes(showticklabels=False, rangeslider_visible=False, row=1, col=2)
    fig.update_xaxes(tickformat='%m-%d %H:%M', rangeslider_visible=False, row=2, col=2)

    fig.update_layout(
        xaxis_rangeslider_visible=False,
        height=900, template="plotly_dark", dragmode="pan", uirevision="constant", margin=dict(l=0, r=40, t=50, b=0), font=dict(size=12)
    )
    fig.update_yaxes(side="right", tickfont=dict(size=12), row=1, col=2) 

    st.plotly_chart(fig, use_container_width=True, config={'modeBarButtonsToAdd': ['drawline', 'eraseshape'], 'scrollZoom': True})

if auto_refresh:
    time.sleep(10)
    st.rerun()