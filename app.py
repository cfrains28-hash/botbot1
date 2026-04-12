import streamlit as st
import pandas as pd
import requests
import time
from datetime import timedelta
from binance.client import Client
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from streamlit_gsheets import GSheetsConnection # 🚨 새로 추가된 클라우드 DB 라이브러리

# 1. 웹 페이지 기본 설정
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
        
    if len(df) < 20: return df

    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    df['rsi'] = 100 - (100 / (1 + (gain / loss))) 
    
    df['ma5'] = df['close'].rolling(window=5, min_periods=1).mean()
    df['ma20'] = df['close'].rolling(window=20, min_periods=1).mean()
    df['ma60'] = df['close'].rolling(window=60, min_periods=1).mean()
    
    return df

st.sidebar.markdown("### ⚙️ 메인 설정")
coin_list = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE']
selected_coin = st.sidebar.selectbox("🪙 코인 선택", coin_list, index=0)
symbol = f"{selected_coin}USDT"

if st.session_state.last_coin != selected_coin:
    st.session_state.alert_sent = False
    st.session_state.last_coin = selected_coin

st.title(f"📈 {selected_coin} 세력 감시 센터 V2.9 (클라우드 DB)")

score_container = st.container()   
control_container = st.container() 
chart_container = st.container()   

with control_container:
    col_interval, col_toggle = st.columns([4, 1])
    with col_interval:
        interval_dict = {
            "1분봉": Client.KLINE_INTERVAL_1MINUTE, 
            "5분봉": Client.KLINE_INTERVAL_5MINUTE, 
            "15분봉": Client.KLINE_INTERVAL_15MINUTE, 
            "1시간봉": Client.KLINE_INTERVAL_1HOUR, 
            "4시간봉": Client.KLINE_INTERVAL_4HOUR, 
            "일봉": Client.KLINE_INTERVAL_1DAY, 
            "주봉": Client.KLINE_INTERVAL_1WEEK,
            "월봉": Client.KLINE_INTERVAL_1MONTH
        }
        selected_interval_name = st.radio("⏰ 차트 간격", list(interval_dict.keys()), horizontal=True, index=3)
        selected_interval = interval_dict[selected_interval_name]
    
    with col_toggle:
        st.markdown("<br>", unsafe_allow_html=True) 
        show_vp = st.toggle("📊 매물대 차트 켜기", value=False) 

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

support_zones = vp[vp['price_mid'] < current_price]
if not support_zones.empty:
    vol_threshold = vp['volume'].quantile(0.75) 
    strong_supports = support_zones[support_zones['volume'] >= vol_threshold]
    
    if not strong_supports.empty:
        closest_idx = strong_supports['price_mid'].idxmax()
        poc_price = strong_supports.loc[closest_idx, 'price_mid']
        poc_bottom = float(strong_supports.loc[closest_idx, 'price_zone'].left)
        stop_loss_price = poc_bottom * 0.998
    else:
        poc_idx = support_zones['volume'].idxmax()
        poc_price = support_zones.loc[poc_idx, 'price_mid']
        poc_bottom = float(support_zones.loc[poc_idx, 'price_zone'].left)
        stop_loss_price = poc_bottom * 0.998
else:
    poc_price = current_price
    stop_loss_price = current_price * 0.97 

ma_score = 40 if (current_price > df['ma60'].iloc[-1] and df['ma5'].iloc[-1] > df['ma20'].iloc[-1] > df['ma60'].iloc[-1]) else (20 if current_price > df['ma60'].iloc[-1] else 0)
vol_score = 30 if vol_ratio >= 3.0 else (15 if vol_ratio >= 1.5 else 0)
pos_score = 30 if current_price >= poc_price * 0.99 else 0
total_prob = ma_score + vol_score + pos_score

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

st.sidebar.markdown("---")
auto_refresh = st.sidebar.checkbox("🔄 실시간 새로고침 (ON/OFF)", value=True)
alert_price = st.sidebar.number_input(f"🚨 {selected_coin} 알림 가격(USDT):", value=0.0, format="%.4f")
st.sidebar.markdown("---")
st.sidebar.metric(label=f"현재 {selected_coin} 가격", value=f"{current_price:,.4f}")
rsi_status = "🔴 과매수" if current_rsi >= 70 else "🟢 과매도" if current_rsi <= 30 else "⚪ 중립"
st.sidebar.markdown(f"**RSI (14):** `{current_rsi:.1f}` ({rsi_status})")
vol_info = ("🐋 고래!", "orange") if vol_ratio >= 3.0 else ("🐬 돌고래", "blue") if vol_ratio >= 1.5 else ("🐟 멸치", "gray")
st.sidebar.markdown(f"**거래량 강도:** :{vol_info[1]}[{vol_info[0]} ({vol_ratio:.1f}x)]")

if alert_price > 0 and current_price <= alert_price:
    if not st.session_state.alert_sent: 
        send_telegram_msg(f"⚠️ [{selected_coin} 도달]\n현재가: {current_price:,.4f}")
        st.session_state.alert_sent = True 
elif current_price > alert_price: st.session_state.alert_sent = False

# ==========================================
# 🚨 [패치 핵심] V2.9 구글 시트 영구 저장 로직
# ==========================================
log_df = pd.DataFrame()
try:
    # 스트림릿 시크릿에 설정된 인증키를 통해 구글 시트와 통신
    conn = st.connection("gsheets", type=GSheetsConnection)
    # 현재 선택된 코인 이름(BTC, ETH 등)을 탭(worksheet) 이름으로 사용
    log_df = conn.read(worksheet=selected_coin, ttl=0)
    
    # 시트가 비어있으면 기본 열 생성
    if log_df.empty or "상태" not in log_df.columns:
        log_df = pd.DataFrame(columns=["진입시간", "차트간격", "진입가", "승률점수", "상태", "청산시간", "청산가", "수익률(%)"])
except Exception as e:
    st.error("⚠️ 구글 시트 연결 실패: .streamlit/secrets.toml 설정을 확인하세요.")
    log_df = pd.DataFrame(columns=["진입시간", "차트간격", "진입가", "승률점수", "상태", "청산시간", "청산가", "수익률(%)"])

needs_update = False # 구글 API 호출을 최소화하기 위한 스위치

if not log_df.empty:
    pending_idx = log_df[log_df["상태"] == "⏳ 대기중"].index
    current_time_dt = df['time'].iloc[-1]
    
    for idx in pending_idx:
        entry_time = pd.to_datetime(log_df.loc[idx, "진입시간"])
        if current_time_dt > entry_time: 
            entry_price = float(log_df.loc[idx, "진입가"])
            exit_price = current_price
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100
            
            log_df.loc[idx, "청산시간"] = str(current_time_dt)
            log_df.loc[idx, "청산가"] = exit_price
            log_df.loc[idx, "수익률(%)"] = round(pnl_pct, 2)
            log_df.loc[idx, "상태"] = "🟢 승리" if pnl_pct > 0 else "🔴 패배"
            needs_update = True

if total_prob >= 70:
    current_time_str = str(df['time'].iloc[-1])
    if log_df.empty or current_time_str not in log_df["진입시간"].values:
        new_trade = pd.DataFrame([{
            "진입시간": current_time_str,
            "차트간격": selected_interval_name,
            "진입가": current_price,
            "승률점수": total_prob,
            "상태": "⏳ 대기중",
            "청산시간": "-",
            "청산가": 0.0,
            "수익률(%)": 0.0
        }])
        log_df = pd.concat([log_df, new_trade], ignore_index=True)
        needs_update = True

# 채점이나 새로운 진입이 생겼을 때만 구글 시트에 데이터 전송 (업로드)
if needs_update:
    try:
        conn.update(worksheet=selected_coin, data=log_df)
    except: pass

st.markdown("---")
with st.expander("📊 구글 시트 실전 시뮬레이션 장부 (Cloud Linked)", expanded=True):
    if not log_df.empty:
        completed_trades = log_df[log_df["상태"] != "⏳ 대기중"]
        if not completed_trades.empty:
            win_count = len(completed_trades[completed_trades["상태"] == "🟢 승리"])
            total_count = len(completed_trades)
            actual_win_rate = (win_count / total_count) * 100
            total_pnl = completed_trades["수익률(%)"].sum()
            
            col_stat1, col_stat2 = st.columns(2)
            col_stat1.metric("이 알고리즘의 실제 승률", f"{actual_win_rate:.1f}% ({win_count}승 / {total_count-win_count}패)")
            col_stat2.metric("누적 모의 수익률", f"{total_pnl:.2f}%")
        
        st.dataframe(log_df.sort_values(by="진입시간", ascending=False), use_container_width=True)
        st.caption("💡 위 데이터는 구글 클라우드 스프레드시트와 실시간으로 동기화됩니다.")
    else:
        st.info("아직 70점 이상의 강력 매수 타점이 발생하지 않았거나, 구글 시트와 연결되지 않았습니다.")

# ==========================================

usdt_volume = last_volume * current_price 
min_usdt_threshold = 500000 

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

        msg = f"{whale_title} ({selected_interval_name})\n코인: {selected_coin}\n현재가: {current_price:,.4f}\n터진금액: ${usdt_volume:,.0f}\n상황: {whale_desc}\n🎯 진입승률: {total_prob}%"
        send_telegram_msg(msg)
        st.session_state.last_whale_time = df['time'].iloc[-1]

with chart_container:
    if show_vp:
        fig = make_subplots(
            rows=2, cols=2, shared_xaxes=True, shared_yaxes=True, 
            column_widths=[0.25, 0.75], row_heights=[0.82, 0.18], 
            specs=[[{}, {}], [{}, {}]], horizontal_spacing=0.015, vertical_spacing=0.07,
            subplot_titles=("📊 매물대 구간 확인", f"🔥 {selected_coin} 실시간 캔들", "", "RSI (14)")
        )
        candle_col = 2
        
        fig.add_trace(go.Bar(
            x=vp['volume'], y=vp['price_mid'], orientation='h', 
            marker=dict(color=vp['volume'], colorscale='Viridis', showscale=False), 
            name='매물대',
            hovertemplate="<b>가격:</b> %{y:,.2f} USDT<br><b>거래량:</b> %{x:,.0f}<extra></extra>"
        ), row=1, col=1)
        
        fig.update_xaxes(visible=False, row=2, col=1)
        fig.update_yaxes(visible=False, row=2, col=1)
        
    else:
        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True, 
            row_heights=[0.82, 0.18], vertical_spacing=0.07,
            subplot_titles=(f"🔥 {selected_coin} 실시간 캔들", "RSI (14)")
        )
        candle_col = 1

    fig.add_trace(go.Candlestick(x=df['time'], open=df['open'], high=df['high'], low=df['low'], close=df['close'], name='가격'), row=1, col=candle_col)
    for ma, color in zip(['ma5', 'ma20', 'ma60'], ['white', 'orange', 'deepskyblue']):
        fig.add_trace(go.Scatter(x=df['time'], y=df[ma], line=dict(color=color, width=1.5), name=ma.upper()), row=1, col=candle_col)

    whale_spots = df[df['volume'] > (df['volume'].rolling(window=20).mean() * 3.0)]
    for _, row in whale_spots.iterrows():
        is_buy = row['close'] > row['open']
        emoji = "🐳매수" if is_buy else "🦈매도"
        color = "lime" if is_buy else "red"
        y_position = row['low'] if is_buy else row['high']
        ay_value = 35 if is_buy else -35
        
        fig.add_annotation(
            x=row['time'], y=y_position, 
            text=f"<span style='color:{color}; font-weight:bold;'>{emoji}</span>", 
            showarrow=True, arrowhead=1, arrowcolor=color, ax=0, ay=ay_value, row=1, col=candle_col
        )

    fig.add_hline(y=stop_loss_price, line_dash="dash", line_color="magenta", annotation_text=f"🛑 기계적 손절가: {stop_loss_price:,.2f}", annotation_position="bottom right", row=1, col=candle_col)
    fig.add_trace(go.Scatter(x=df['time'], y=df['rsi'], line=dict(color='yellow', width=1.5), name='RSI'), row=2, col=candle_col)
    fig.add_hline(y=70, line_dash="dot", line_color="red", row=2, col=candle_col)
    fig.add_hline(y=30, line_dash="dot", line_color="green", row=2, col=candle_col)

    if "분봉" in selected_interval_name or "시간봉" in selected_interval_name:
        x_format = '%m-%d %H:%M'  
    elif "일봉" in selected_interval_name or "주봉" in selected_interval_name:
        x_format = '%Y-%m-%d'     
    else: 
        x_format = '%Y-%m'        

    fig.update_xaxes(showticklabels=False, rangeslider_visible=False, row=1, col=candle_col)
    fig.update_xaxes(tickformat=x_format, rangeslider_visible=False, row=2, col=candle_col)

    fig.update_layout(
        xaxis_rangeslider_visible=False,
        height=900, template="plotly_dark", dragmode="pan", 
        uirevision=f"{selected_coin}_{selected_interval_name}", 
        margin=dict(l=0, r=40, t=50, b=0), font=dict(size=12)
    )
    fig.update_yaxes(side="right", tickfont=dict(size=12), row=1, col=candle_col) 

    st.plotly_chart(fig, use_container_width=True, config={'modeBarButtonsToAdd': ['drawline', 'eraseshape'], 'scrollZoom': True})

if auto_refresh:
    time.sleep(10)
    st.rerun()