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
from datetime import datetime, timedelta, time as dt_time

# --- 1. 基礎設定 ---
st.set_page_config(page_title="花蓮港船舶即時查詢", layout="wide")

def get_taiwan_time():
    """取得當前台灣時間 (抹除秒數)"""
    return (datetime.utcnow() + timedelta(hours=8)).replace(second=0, microsecond=0)

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
                w_label = f"{int(re.search(r'(\d+)', raw_w).group(1)):02d}號" if raw_w and re.search(r'(\d+)', raw_w) else raw_w

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

# --- 4.5 跨 Session 全域共享快取 ---
# 修正點：加入 show_spinner=False 以隱藏非預期的 "Running..." 系統文字
@st.cache_data(ttl=1200, show_spinner=False)
def get_shared_24h_data():
    now_tw = get_taiwan_time()
    f24 = now_tw + timedelta(hours=24)
    df = run_scraper_segment(now_tw, f24, "(全域自動同步)")
    if not df.empty:
        cols = ["日期", "時間", "狀態", "碼頭", "中文船名", "長度(m)", "英文船名", "總噸位", "前一港", "下一港", "代理行"]
        return df[cols].drop_duplicates().sort_values(by=["日期", "時間"]), get_taiwan_time()
    return None, None

# --- 5. UI 介面 ---
st.markdown(
    """<h3 style='text-align: left; font-size: 24px; margin-bottom: 20px;'>🚢 花蓮港船舶動態查詢</h3>""", 
    unsafe_allow_html=True
)

now_init = get_taiwan_time()
f24_init = now_init + timedelta(hours=24)

st.radio(
    "⏱️ **預設顯示未來24H動態(每20分鐘自動更新)。點選按鈕可即時重新查詢。**",
    ["未來 24H", "未來 3 日", "前 7 日", "本月整月"],
    key="ui_option",
    on_change=on_ui_change,
    horizontal=True
)

# 摺疊選單
with st.expander("更改查詢時段", expanded=st.session_state.expander_state):
    c1, c2 = st.columns(2)
    with c1:
        sd_in = st.date_input("開始日期", key="sd_key", value=now_init.date())
        st_in = st.time_input("開始時間", key="st_key", value=now_init.time(), label_visibility="collapsed")
    with c2:
        ed_in = st.date_input("結束日期", key="ed_key", value=f24_init.date())
        et_in = st.time_input("結束時間", key="et_key", value=f24_init.time(), label_visibility="collapsed")

start_dt = datetime.combine(sd_in, st_in)
end_dt = datetime.combine(ed_in, et_in)

# 按鈕區域 (固定在時段選單下方)
st.write("") 
if st.button("🚀 開始查詢", type="primary", use_container_width=True):
    st.session_state.trigger_search = True
    # 如果是查詢「未來24H」，不清除快取，而是讓手動查詢的結果更新快取
    if st.session_state.ui_option != "未來 24H":
        st.cache_data.clear()

# --- 6. 執行邏輯 ---

# 🔄 自動更新機制：每20分鐘強制重新整理頁面
if 'last_auto_refresh' not in st.session_state:
    st.session_state.last_auto_refresh = time.time()

if time.time() - st.session_state.last_auto_refresh > 1200:  # 1200秒 = 20分鐘
    st.session_state.last_auto_refresh = time.time()
    st.cache_data.clear()  # 清除快取確保獲取最新資料
    st.rerun()

# 情況 A：讀取全域快取模式 (自動同步)
if st.session_state.ui_option == "未來 24H" and not st.session_state.trigger_search:
    placeholder_status = st.empty()
    
    with placeholder_status.container():
        shared_df, update_time = get_shared_24h_data()
    
    # 資料取得後，清空「查詢中」的提示，只保留結果
    placeholder_status.empty()
    
    if shared_df is not None:
        st.success(f"⚡ 顯示全域同步資料 (更新時間: {update_time.strftime('%H:%M')})")
        st.dataframe(shared_df, use_container_width=True, hide_index=True)
        csv_shared = shared_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 下載完整報表", csv_shared, "Report_Shared.csv", use_container_width=True, key="dl_shared")
        st.stop() 

# 情況 C：執行手動爬蟲邏輯 (手動模式則保留進度條讓使用者確認完成)
if st.session_state.trigger_search:
    st.session_state.trigger_search = False
    
    # 🔥 特殊處理：如果是查詢「未來24H」，直接更新全域快取
    if st.session_state.ui_option == "未來 24H":
        # 清除舊快取
        st.cache_data.clear()
        
        # 🎯 直接呼叫快取函數（只會執行一次爬蟲）
        shared_df, update_time = get_shared_24h_data()
        
        if shared_df is not None:
            st.success(f"🎊 查詢完成！已更新全域快取，共 {len(shared_df)} 筆資料。")
            st.dataframe(shared_df, use_container_width=True, hide_index=True)
            csv_manual = shared_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 下載完整報表", csv_manual, "Report_Shared.csv", use_container_width=True, key="dl_manual_24h")
        else:
            st.warning("⚠️ 該區間查無符合條件的船舶資料。")
        
        st.stop()  # 完成後停止，避免執行後續邏輯
    
    # 一般情況：處理其他時段的查詢
    date_segments = split_date_range(start_dt, end_dt)
    all_dfs = []
    
    for i, (seg_s, seg_e) in enumerate(date_segments):
        df_seg = run_scraper_segment(seg_s, seg_e, f"({i+1}/{len(date_segments)})")
        if not df_seg.empty:
            all_dfs.append(df_seg)
    
    if all_dfs:
        final_df = pd.concat(all_dfs).drop_duplicates().sort_values(by=["日期", "時間"])
        cols = ["日期", "時間", "狀態", "碼頭", "中文船名", "長度(m)", "英文船名", "總噸位", "前一港", "下一港", "代理行"]
        final_df = final_df[cols]
        st.success(f"🎊 查詢完成！共獲取 {len(final_df)} 筆資料。")
        st.dataframe(final_df, use_container_width=True, hide_index=True)
        csv_manual = final_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 下載完整報表", csv_manual, f"Report_{start_dt.strftime('%m%d')}.csv", use_container_width=True, key="dl_manual")
    else:
        st.warning("⚠️ 該區間查無符合條件的船舶資料。")
