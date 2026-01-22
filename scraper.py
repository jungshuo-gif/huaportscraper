import streamlit as st
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType
from selenium.webdriver.support.ui import Select
import time
import re
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone, time as dt_time

# --- 1. 基礎設定 ---
st.set_page_config(page_title="花蓮港船舶即時查詢", layout="wide")

def get_taiwan_time():
    """取得當前台灣時間 (UTC+8)"""
    tz_taiwan = timezone(timedelta(hours=8))
    return datetime.now(timezone.utc).astimezone(tz_taiwan).replace(tzinfo=None, second=0, microsecond=0)

def split_date_range(start, end):
    """將長區間拆分為多個 7 天內的區段"""
    segments = []
    curr_start = start
    while curr_start < end:
        curr_end = min(curr_start + timedelta(days=7), end)
        segments.append((curr_start, curr_end))
        curr_start = curr_end + timedelta(seconds=1)
    return segments

# --- 2. 初始化 Session State ---
if 'trigger_search' not in st.session_state:
    st.session_state.trigger_search = False 
if 'expander_state' not in st.session_state:
    st.session_state.expander_state = False 

# --- 3. UI 連動回調 ---
def on_ui_change():
    now = get_taiwan_time()
    opt = st.session_state.ui_option
    
    sd, st_val = now.date(), now.time()
    ed, et_val = now.date(), now.time()

    if opt == "未來 24H":
        f = now + timedelta(hours=24); ed, et_val = f.date(), f.time()
        st.session_state.expander_state = False
    elif opt == "未來 3 日":
        f = now + timedelta(hours=72); ed, et_val = f.date(), f.time()
        st.session_state.expander_state = False
    elif opt == "前 7 日":
        p = now - timedelta(days=7); sd, st_val = p.date(), dt_time(0, 0)
        st.session_state.expander_state = False
    elif opt == "本月整月":
        first_day = now.replace(day=1, hour=0, minute=0)
        sd, st_val = first_day.date(), first_day.time()
        st.session_state.expander_state = False

    st.session_state.sd_key = sd
    st.session_state.st_key = st_val
    st.session_state.ed_key = ed
    st.session_state.et_key = et_val
    st.session_state.trigger_search = True

# --- 4. 核心爬蟲函數 ---
def run_scraper_segment(start_time, end_time, step_text=""):
    download_dir = os.path.join(os.getcwd(), "temp_downloads")
    if not os.path.exists(download_dir): os.makedirs(download_dir)
    for f in os.listdir(download_dir):
        try: os.remove(os.path.join(download_dir, f))
        except: pass

    driver = None
    with st.status(f"🚢 查詢中，請等候約10秒 {step_text}...", expanded=True) as status:
        try:
            options = webdriver.ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_experimental_option("prefs", {"download.default_directory": download_dir})
            service = Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())
            driver = webdriver.Chrome(service=service, options=options)
            driver.execute_cdp_cmd('Page.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': download_dir})
            driver.get("https://tpnet.twport.com.tw/IFAWeb/Function?_RedirUrl=/IFAWeb/Reports/HistoryPortShipList")
            wait = WebDriverWait(driver, 20)
            if driver.find_elements(By.TAG_NAME, "iframe"): driver.switch_to.frame(0)
            
            h_tab = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(),'花蓮港')]")))
            driver.execute_script("arguments[0].click();", h_tab)

            v_s, v_e = start_time.strftime("%Y/%m/%d %H:%M"), end_time.strftime("%Y/%m/%d %H:%M")
            status.write(f"📝 填寫區間: {v_s} ~ {v_e}")
            inps = driver.find_elements(By.TAG_NAME, "input")
            d_inps = [i for i in inps if i.get_attribute("value") and i.get_attribute("value").startswith("20")]
            if len(d_inps) >= 2:
                driver.execute_script(f"arguments[0].value = '{v_s}'; arguments[0].dispatchEvent(new Event('change'));", d_inps[0])
                driver.execute_script(f"arguments[0].value = '{v_e}'; arguments[0].dispatchEvent(new Event('change'));", d_inps[1])
            
            checked_boxes = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']:checked")
            for cb in checked_boxes: driver.execute_script("arguments[0].click();", cb)
            
            try:
                sort_sel = driver.find_element(By.XPATH, "//*[contains(text(),'Ordering by')]/following::select[1]")
                Select(sort_sel).select_by_index(1)
            except: pass

            btn = driver.find_element(By.XPATH, "//*[contains(@value,'Query') or contains(@value,'查詢')]")
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(4)
            
            xml_btns = driver.find_elements(By.XPATH, "//*[contains(text(), 'XML') or contains(@value, 'XML')]")
            if xml_btns: driver.execute_script("arguments[0].click();", xml_btns[0])
            
            downloaded_file = None
            for _ in range(15):
                time.sleep(1)
                xml_fs = [os.path.join(download_dir, f) for f in os.listdir(download_dir) if f.lower().endswith('.xml')]
                if xml_fs:
                    downloaded_file = max(xml_fs, key=os.path.getmtime)
                    break
            
            if not downloaded_file: return pd.DataFrame()

            with open(downloaded_file, 'r', encoding='big5', errors='replace') as f:
                content = f.read().replace('encoding="BIG5"', '').replace('encoding="big5"', '')
            
            root = ET.fromstring(content)
            parsed = []
            for ship in root.findall('SHIP'):
                gt_n = ship.find('GROSS_TOA')
                gt = int(round(float(gt_n.text))) if gt_n is not None and gt_n.text else 0
                if gt < 500 : continue

                w_n = ship.find('WHARF_CODE')
                raw_w = w_n.text if w_n is not None else ""
                match = re.search(r'(\d+)', raw_w)
                w_label = "{:02d}號".format(int(match.group(1))) if match else raw_w

                t_n = ship.find('PILOT_EXP_TM')
                raw_t = t_n.text if t_n is not None else ""
                d_s, t_s = "未排定", "未排定"
                if len(raw_t) >= 12: d_s, t_s = f"{raw_t[4:6]}/{raw_t[6:8]}", f"{raw_t[8:10]}:{raw_t[10:12]}"

                parsed.append({
                    "日期": d_s, "時間": t_s, "狀態": ship.find('SP_STS').text if ship.find('SP_STS') is not None else "",
                    "碼頭": w_label, "中文船名": ship.find('VESSEL_CNAME').text or "",
                    "長度(m)": int(round(float(ship.find('LOA').text))) if ship.find('LOA') is not None else 0,
                    "英文船名": ship.find('VESSEL_ENAME').text if ship.find('VESSEL_ENAME') is not None else "",
                    "總噸位": gt, "前一港": ship.find('BEFORE_PORT').text if ship.find('BEFORE_PORT') is not None else "",
                    "下一港": ship.find('NEXT_PORT').text if ship.find('NEXT_PORT') is not None else "",
                    "代理行": (ship.find('PBG_NAME').text or "")[:2]
                })
            status.update(label="✅ 查詢完成", state="complete", expanded=False)
            return pd.DataFrame(parsed)
        except Exception as e:
            st.error(f"❌ 錯誤: {e}")
            return pd.DataFrame()
        finally:
            if driver: driver.quit()

# --- 5. 緩存讀取邏輯 ---
@st.cache_data(ttl=1200, show_spinner=False)
def get_cached_data():
    cache_file = "port_data_cache.csv"
    # 如果 GitHub Actions 產出的 csv 存在且夠新
    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file)
        mtime = datetime.fromtimestamp(os.path.getmtime(cache_file))
        return df, mtime
    
    # 若無緩存，則執行一次即時爬蟲(未來24H)
    now_tw = get_taiwan_time()
    f24 = now_tw + timedelta(hours=24)
    df = run_scraper_segment(now_tw, f24, "(啟動即時同步)")
    return df, get_taiwan_time()

# --- 6. UI 介面 ---
st.markdown(
    """<h3 style='text-align: left; font-size: 24px; margin-bottom: 20px;'>🚢 花蓮港船舶動態查詢</h3>""", 
    unsafe_allow_html=True
)

now_init = get_taiwan_time()
f24_init = now_init + timedelta(hours=24)

st.radio(
    "⏱️ **預設顯示未來24H動態。點選按鈕可即時重新查詢。**",
    ["未來 24H", "未來 3 日", "前 7 日", "本月整月"],
    key="ui_option",
    on_change=on_ui_change,
    horizontal=True
)

with st.expander("更改查詢時段", expanded=st.session_state.expander_state):
    c1, c2 = st.columns(2)
    with c1:
        sd_in = st.date_input("開始日期", key="sd_key", value=now_init.date())
        st_in = st.time_input("開始時間", key="st_key", value=now_init.time(), label_visibility="collapsed")
    with c2:
        ed_in = st.date_input("結束日期", key="ed_key", value=f24_init.date())
        et_in = st.time_input("結束時間", key="et_key", value=f24_init.time(), label_visibility="collapsed")

if st.button("🚀 開始查詢", type="primary", use_container_width=True):
    st.session_state.trigger_search = True
    if st.session_state.ui_option != "未來 24H":
        st.cache_data.clear()

# --- 7. 執行邏輯 ---

# 模式 A: 未來 24H 模式 (優先讀取 CSV 緩存)
if st.session_state.ui_option == "未來 24H" and not st.session_state.trigger_search:
    df, update_time = get_cached_data()
    if df is not None and not df.empty:
        st.success(f"⚡ 顯示同步資料 (更新時間: {update_time.strftime('%m/%d %H:%M')})")
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button("📥 下載報表", df.to_csv(index=False).encode('utf-8-sig'), "Report.csv", use_container_width=True)
        st.stop()

# 模式 B: 手動查詢/其他時段
if st.session_state.trigger_search:
    st.session_state.trigger_search = False
    
    start_dt = datetime.combine(sd_in, st_in)
    end_dt = datetime.combine(ed_in, et_in)
    
    date_segments = split_date_range(start_dt, end_dt)
    all_dfs = []
    
    for i, (seg_s, seg_e) in enumerate(date_segments):
        df_seg = run_scraper_segment(seg_s, seg_e, f"({i+1}/{len(date_segments)})")
        if not df_seg.empty:
            all_dfs.append(df_seg)
    
    if all_dfs:
        final_df = pd.concat(all_dfs).drop_duplicates().sort_values(by=["日期", "時間"])
        st.success(f"🎊 查詢完成！共 {len(final_df)} 筆資料。")
        st.dataframe(final_df, use_container_width=True, hide_index=True)
        st.download_button("📥 下載報表", final_df.to_csv(index=False).encode('utf-8-sig'), "Report_Custom.csv", use_container_width=True)
    else:
        st.warning("⚠️ 該區間查無資料。")
