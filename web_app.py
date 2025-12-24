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
    return (datetime.utcnow() + timedelta(hours=8)).replace(second=0, microsecond=0)

def split_date_range(start, end):
    segments = []
    curr_start = start
    while curr_start < end:
        curr_end = min(curr_start + timedelta(days=7), end)
        segments.append((curr_start, curr_end))
        curr_start = curr_end + timedelta(seconds=1)
    return segments

# --- 2. 初始化 Session State (新增緩存機制) ---
if 'trigger_search' not in st.session_state:
    st.session_state.trigger_search = True 
if 'expander_state' not in st.session_state:
    st.session_state.expander_state = False 
if 'last_option' not in st.session_state:
    st.session_state.last_option = "未來 24H"

# 緩存專用變數
if 'cache_24h_df' not in st.session_state:
    st.session_state.cache_24h_df = None
if 'cache_24h_time' not in st.session_state:
    st.session_state.cache_24h_time = None

# --- 3. UI 連動回調 ---
def on_ui_change():
    now = get_taiwan_time()
    opt = st.session_state.ui_option
    st.session_state.last_option = opt
    
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
    elif opt == "手動調整":
        st.session_state.expander_state = True 

    st.session_state.sd_key = sd
    st.session_state.st_key = st_val
    st.session_state.ed_key = ed
    st.session_state.et_key = et_val
    
    # 判斷是否需要自動觸發：檢查 20 分鐘緩存
    if opt == "未來 24H" and st.session_state.cache_24h_df is not None:
        time_diff = datetime.now() - st.session_state.cache_24h_time
        if time_diff < timedelta(minutes=20):
            st.session_state.trigger_search = False # 有效緩存，不自動爬取
            return

    if opt != "手動調整":
        st.session_state.trigger_search = True

# --- 4. 核心爬蟲函數 (保持 11 欄位與過濾邏輯) ---
def run_scraper_segment(start_time, end_time, step_text=""):
    download_dir = os.path.join(os.getcwd(), "temp_downloads")
    if not os.path.exists(download_dir): os.makedirs(download_dir)
    for f in os.listdir(download_dir):
        try: os.remove(os.path.join(download_dir, f))
        except: pass

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
            status.write(f"📝 填寫時間: {v_s} ~ {v_e}")
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
                cname = ship.find('VESSEL_CNAME').text or ""
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
                    "碼頭": w_label, "中文船名": cname, "長度(m)": int(round(float(ship.find('LOA').text))) if ship.find('LOA') is not None else 0,
                    "英文船名": ship.find('VESSEL_ENAME').text if ship.find('VESSEL_ENAME') is not None else "",
                    "總噸位": gt, "前一港": ship.find('BEFORE_PORT').text if ship.find('BEFORE_PORT') is not None else "",
                    "下一港": ship.find('NEXT_PORT').text if ship.find('NEXT_PORT') is not None else "",
                    "代理行": (ship.find('PBG_NAME').text or "")[:2]
                })
            driver.quit()
            status.update(label="✅ 區段查詢完成", state="complete", expanded=False)
            return pd.DataFrame(parsed)
        except Exception as e:
            if 'driver' in locals(): driver.quit()
            st.error(f"❌ 錯誤: {e}")
            return pd.DataFrame()

# --- 5. UI 介面 ---
# 修改後：改用 markdown 語法並強制設定字體大小 (例如 24px)，確保手機不換行
st.markdown(
    """
    <h3 style='text-align: left; font-size: 30px; margin-bottom: 20px;'>
    🚢 花蓮港船舶即時查詢
    </h3>
    """, 
    unsafe_allow_html=True
)
now_init = get_taiwan_time()
f24 = now_init + timedelta(hours=24)

st.radio(
    "⏱️ **1,預設自動顯示未來24H動態，請向下滑。2,亦可點選按鈕，等待查詢約10秒。**",
    ["未來 24H", "未來 3 日", "前 7 日", "本月整月"], # 修改點：已移除「手動輸入」選項
    key="ui_option",
    on_change=on_ui_change,
    horizontal=True
)

# 修改點：標題改為「手動輸入」，並保留原本的摺疊狀態邏輯 (預設為 False)
with st.expander("手動輸入", expanded=st.session_state.expander_state):
    c1, c2 = st.columns(2)
    with c1:
        sd_in = st.date_input("開始日期", key="sd_key", value=now_init.date())
        st_in = st.time_input("開始時間", key="st_key", value=now_init.time(), label_visibility="collapsed")
    with c2:
        ed_in = st.date_input("結束日期", key="ed_key", value=f24.date())
        et_in = st.time_input("結束時間", key="et_key", value=f24.time(), label_visibility="collapsed")

start_dt = datetime.combine(sd_in, st_in)
end_dt = datetime.combine(ed_in, et_in)

# --- 6. 執行邏輯 (緩存優先) ---
if st.button("🚀 開始查詢", type="primary", use_container_width=True):
    st.session_state.trigger_search = True

# 判斷是否直接顯示緩存 (適用於非手動觸發的 未來 24H)
if st.session_state.ui_option == "未來 24H" and not st.session_state.trigger_search:
    if st.session_state.cache_24h_df is not None:
        time_diff = datetime.now() - st.session_state.cache_24h_time
        if time_diff < timedelta(minutes=20):
            st.success(f"⚡ 顯示近20分鐘內資料 (更新時間: {st.session_state.cache_24h_time.strftime('%H:%M')})")
            st.dataframe(st.session_state.cache_24h_df, use_container_width=True, hide_index=True)
            st.stop() # 停止執行後續爬蟲邏輯

if st.session_state.trigger_search:
    st.session_state.trigger_search = False
    date_segments = split_date_range(start_dt, end_dt)
    all_dfs = []
    
    if st.session_state.ui_option != "手動調整":
        time.sleep(1.5)

    for i, (seg_s, seg_e) in enumerate(date_segments):
        df_seg = run_scraper_segment(seg_s, seg_e, f"({i+1}/{len(date_segments)})")
        if not df_seg.empty: all_dfs.append(df_seg)
    
    if all_dfs:
        final_df = pd.concat(all_dfs).drop_duplicates().sort_values(by=["日期", "時間"])
        cols = ["日期", "時間", "狀態", "碼頭", "中文船名", "長度(m)", "英文船名", "總噸位", "前一港", "下一港", "代理行"]
        final_df = final_df[cols]
        
        # 更新緩存 (僅針對 24H 查詢)
        if st.session_state.ui_option == "未來 24H":
            st.session_state.cache_24h_df = final_df
            st.session_state.cache_24h_time = datetime.now()
        
        st.success(f"🎊 查詢完成！共獲取 {len(final_df)} 筆資料。")
        st.dataframe(final_df, use_container_width=True, hide_index=True)
        csv = final_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 下載完整報表", csv, f"Report_{start_dt.strftime('%m%d')}.csv", use_container_width=True)
    else:
        st.warning("⚠️ 該區間查無符合條件的船舶資料。")




