import streamlit as st
import pandas as pd
import requests
import time
from binance.client import Client
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 1. 웹 페이지 기본 설정 및 보안 스타일
st.set_page_config(layout="wide")

# 사이드바 너비 축소 CSS
st.markdown(
    """
    <style>
    [data-testid="stSidebar"] {
        min-width: 250px !important;
        max-width: 250px !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# [보안 설정] Streamlit Secrets에서 토큰 불러오기
try:
    TELEGRAM_TOKEN = st.secrets["TELEGRAM_TOKEN"]
    CHAT_ID = st.secrets["CHAT_ID"]
except:
    # 로컬 테스트용 (서버 배포 전 내 PC에서 돌릴 때만 직접 입력하세요)
    TELEGRAM_TOKEN = "YOUR_TOKEN_HERE"
    CHAT_ID = "YOUR_CHAT_ID_HERE"

# 세션 상태 초기화
if 'alert_sent' not in st.session_state:
    st.session_state.alert_sent = False
if 'last_coin' not in st.session_state:
    st.session_state.last_coin = 'BTC'

# 2. 텔레그램 메시지 전송 함수
def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    params = {"chat_id": CHAT_ID, "text": message}
    try:
        requests.get(url, params=params)
    except Exception as e:
        print(f"텔레그램 발송 실패: {e}")

# 3. 데이터 불러오기 함수
@st.cache_data(ttl=10)
def load_data(interval, symbol):
    client = Client()
    if interval == Client.KLINE_INTERVAL_15MINUTE: start_str = "3 days ago UTC"  
    elif interval in [Client.KLINE_INTERVAL_1HOUR, Client.KLINE_INTERVAL_4HOUR]: start_str = "30 days ago UTC" 
    elif interval == Client.KLINE_INTERVAL_1WEEK: start_str = "2 years ago UTC" 
    elif interval == Client.KLINE_INTERVAL_1MONTH: start_str = "5 years ago UTC" 
    else: start_str = "300 days ago UTC" 
        
    klines = client.get_historical_klines(symbol, interval, start_str)
    df = pd.DataFrame(klines, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'tbb', 'tbq', 'ignore'])
    df['time'] = pd.to_datetime(df['time'], unit='ms')
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col])
        
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    df['rsi'] = 100 - (100 / (1 + (gain / loss))) 
    
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma20'] = df['close'].rolling(window=20).mean()
    df['ma60'] = df['close'].rolling(window=60).mean()
    return df

# 4. 데이터 로딩 및 사이드바 브리핑
coin_list = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE']
selected_coin = st.sidebar.selectbox("🪙 코인 선택:", coin_list, index=0)
symbol = f"{selected_coin}USDT"

if st.session_state.last_coin != selected_coin:
    st.session_state.alert_sent = False
    st.session_state.last_coin = selected_coin

st.title(f"📈 {selected_coin} 세력 감시 센터")

interval_dict = {
    "15분봉": Client.KLINE_INTERVAL_15MINUTE, "1시간봉": Client.KLINE_INTERVAL_1HOUR,
    "4시간봉": Client.KLINE_INTERVAL_4HOUR, "일봉": Client.KLINE_INTERVAL_1DAY,
    "주봉": Client.KLINE_INTERVAL_1WEEK, "월봉": Client.KLINE_INTERVAL_1MONTH
}
selected_interval_name = st.sidebar.selectbox("⏰ 차트 간격 선택:", list(interval_dict.keys()), index=3)
selected_interval = interval_dict[selected_interval_name]

# 데이터 계산
df = load_data(selected_interval, symbol)
current_price = df['close'].iloc[-1]
current_rsi = df['rsi'].iloc[-1]
last_volume = df['volume'].iloc[-1]
avg_volume = df['volume'].iloc[-21:-1].mean()

# 사이드바 UI 구성
auto_refresh = st.sidebar.checkbox("🔄 실시간 새로고침 (ON/OFF)", value=True)
bins = st.sidebar.slider("매물대 분할 개수:", 10, 100, 50)
alert_price = st.sidebar.number_input(f"🚨 {selected_coin} 알림 가격(USDT):", value=0.0, format="%.4f")

st.sidebar.markdown("---")
st.sidebar.metric(label=f"현재 {selected_coin} 가격 (USDT)", value=f"{current_price:,.4f}")

# RSI 및 거래량 상태 판독
rsi_status = "🔴 과매수" if current_rsi >= 70 else "🟢 과매도" if current_rsi <= 30 else "⚪ 중립"
st.sidebar.markdown(f"**RSI (14):** `{current_rsi:.1f}` ({rsi_status})")

vol_ratio = last_volume / avg_volume
vol_info = ("🐋 고래!", "orange") if vol_ratio >= 3.0 else ("🐬 돌고래", "skyblue") if vol_ratio >= 1.5 else ("🐟 멸치", "gray")
st.sidebar.markdown(f"**거래량 강도:** :{vol_info[1]}[{vol_info[0]} ({vol_ratio:.1f}x)]")
st.sidebar.markdown("---")

# 5. 알림 로직
if alert_price > 0 and current_price <= alert_price:
    if not st.session_state.alert_sent: 
        send_telegram_msg(f"⚠️ [{selected_coin} 도달]\n현재가: {current_price:,.4f}\n설정하신 가격 아래로 하락!")
        st.session_state.alert_sent = True 
elif current_price > alert_price:
    st.session_state.alert_sent = False

if vol_ratio >= 3.0:
    if 'last_whale_time' not in st.session_state or st.session_state.last_whale_time != df['time'].iloc[-1]:
        send_telegram_msg(f"🐋 [{selected_coin} 고래 출현!]\n평균 대비 {vol_ratio:.1f}배 거래 발생!\n현재가: {current_price:,.4f}")
        st.session_state.last_whale_time = df['time'].iloc[-1]

# 6. 매물대 계산
df['price_zone'] = pd.cut(df['close'], bins=bins)
vp = df.groupby('price_zone', observed=False)['volume'].sum().reset_index()
vp['price_mid'] = vp['price_zone'].apply(lambda x: x.mid).astype(float)

# 7. 차트 그리기
fig = make_subplots(
    rows=2, cols=2, shared_xaxes=True, shared_yaxes=True, 
    column_widths=[0.3, 0.7], row_heights=[0.82, 0.18], 
    specs=[[{}, {}], [None, {}]], horizontal_spacing=0.015, vertical_spacing=0.07,
    subplot_titles=("매물대 분석", f"{selected_coin} 실시간 캔들", "RSI (14)")
)

# 매물대 막대
fig.add_trace(go.Bar(x=vp['volume'], y=vp['price_mid'], orientation='h', marker=dict(color=vp['volume'], colorscale='Viridis', showscale=False), name='매물대'), row=1, col=1)

# 캔들 및 이평선
fig.add_trace(go.Candlestick(x=df['time'], open=df['open'], high=df['high'], low=df['low'], close=df['close'], name='가격'), row=1, col=2)
for ma, color in zip(['ma5', 'ma20', 'ma60'], ['white', 'orange', 'deepskyblue']):
    fig.add_trace(go.Scatter(x=df['time'], y=df[ma], line=dict(color=color, width=1.5), name=ma.upper()), row=1, col=2)

# 고래 포착 아이콘
whale_spots = df[df['volume'] > (df['volume'].rolling(window=20).mean() * 3.0)]
for _, row in whale_spots.iterrows():
    fig.add_annotation(x=row['time'], y=row['high'], text="🐋", showarrow=True, arrowhead=1, ax=0, ay=-35, row=1, col=2)

# RSI 및 기준선
fig.add_trace(go.Scatter(x=df['time'], y=df['rsi'], line=dict(color='yellow', width=1.5), name='RSI'), row=2, col=2)
fig.add_hline(y=70, line_dash="dot", line_color="red", annotation_text="🔥 과매수", row=2, col=2)
fig.add_hline(y=30, line_dash="dot", line_color="green", annotation_text="💧 과매도", row=2, col=2)

# 8. 레이아웃 & 툴바 설정
fig.update_xaxes(showticklabels=False, rangeslider_visible=False, rangeselector=dict(buttons=list([dict(count=1, label="1D", step="day", stepmode="backward"), dict(count=7, label="1W", step="day", stepmode="backward"), dict(step="all", label="ALL")]), y=1.05), row=1, col=2)
fig.update_xaxes(tickformat='%m-%d %H:%M', rangeslider_visible=False, row=2, col=2)
fig.update_yaxes(side="right", tickformat=',.4f', row=1, col=2)
fig.update_yaxes(range=[0, 100], side="right", tickvals=[30, 50, 70], row=2, col=2)

fig.update_layout(
    height=1000, template="plotly_dark", dragmode="pan", uirevision="constant",
    margin=dict(l=10, r=60, t=80, b=50),
    newshape=dict(line_color="cyan", line_width=2, line_dash="dot"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)

st.plotly_chart(fig, use_container_width=True, config={'modeBarButtonsToAdd': ['drawline', 'eraseshape'], 'scrollZoom': True})

# 9. 자동 새로고침
if auto_refresh:
    time.sleep(10)
    st.rerun()