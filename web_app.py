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
    """取得當前台灣時間 (抹除秒數與微秒，確保符合網站 7 天限制)"""
    return (datetime.utcnow() + timedelta(hours=8)).replace(second=0, microsecond=0)

def split_date_range(start, end):
    segments = []
    curr_start = start
    while curr_start < end:
        curr_end = min(curr_start + timedelta(days=7), end)
        segments.append((curr_start, curr_end))
        curr_start = curr_end + timedelta(minutes=1) # 避開重疊
    return segments

# --- 2. 初始化 Session State ---
if 'first_load' not in st.session_state:
    st.session_state.first_load = True
    st.session_state.trigger_search = True

if 'last_option' not in st.session_state:
    st.session_state.last_option = "未來 24H"

# --- 3. UI 連動回調 ---
def on_ui_change():
    now = get_taiwan_time()
    opt = st.session_state.ui_option
    st.session_state.last_option = opt
    
    sd, st_val = now.date(), now.time()
    ed, et_val = now.date(), now.time()

    if opt == "未來 24H":
        f = now + timedelta(hours=24); ed, et_val = f.date(), f.time()
    elif opt == "未來 3 日":
        f = now + timedelta(hours=72); ed, et_val = f.date(), f.time()
    elif opt == "前 7 日":
        p = now - timedelta(days=7)
        sd, st_val = p.date(), dt_time(0, 0)
        # 結束時間設為現在
        ed, et_val = now.date(), now.time()
    elif opt == "本月整月":
        first_day = now.replace(day=1, hour=0, minute=0)
        sd, st_val = first_day.date(), first_day.time()

    st.session_state.sd_key = sd
    st.session_state.st_key = st_val
    st.session_state.ed_key = ed
    st.session_state.et_key = et_val
    
    if opt != "手動調整":
        st.session_state.trigger_search = True

# --- 4. 核心爬蟲函數 (修正縮排與解析邏輯) ---
def run_scraper_segment(start_time, end_time, step_text=""):
    download_dir = os.path.join(os.getcwd(), "temp_downloads")
    if not os.path.exists(download_dir): os.makedirs(download_dir)
    for f in os.listdir(download_dir):
        try: os.remove(os.path.join(download_dir, f))
        except: pass

    # 確保所有邏輯都在 st.status 裡面，這樣才不會只剩轉圈圈
    with st.status(f"🚢 正在連線查詢 {step_text}...", expanded=True) as status:
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
            status.write(f"📝 填寫區段: {v_s} ~ {v_e}")
            
            inps = driver.find_elements(By.TAG_NAME, "input")
            d_inps = [i for i in inps if i.get_attribute("value") and i.get_attribute("value").startswith("20")]
            if len(d_inps) >= 2:
                driver.execute_script(f"arguments[0].value = '{v_s}'; arguments[0].dispatchEvent(new Event('change'));", d_inps[0])
                driver.execute_script(f"arguments[0].value = '{v_e}'; arguments[0].dispatchEvent(new Event('change'));", d_inps[1])
            
            btn = driver.find_element(By.XPATH, "//*[contains(@value,'Query') or contains(@value,'查詢')]")
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(1)
            
            # 檢查 Alert
            try:
                alert = driver.switch_to.alert
                msg = alert.text
                alert.accept()
                raise Exception(f"網站限制：{msg}")
            except: pass

            time.sleep(3)
            xml_btns = driver.find_elements(By.XPATH, "//*[contains(text(), 'XML') or contains(@value, 'XML')]")
            if xml_btns: driver.execute_script("arguments[0].click();", xml_btns[0])
            
            # --- 等待檔案下載 ---
            downloaded_file = None
            for _ in range(15):
                time.sleep(1)
                files = [f for f in os.listdir(download_dir) if f.endswith('.xml')]
                if files:
                    downloaded_file = os.path.join(download_dir, files[0])
                    break
            
            if not downloaded_file:
                raise Exception("未偵測到下載檔案 (可能該區段無資料)")
                
            status.write("⚙️ 解析 XML 資料...")
            with open(downloaded_file, 'r', encoding='big5', errors='replace') as f:
                content = f.read().replace('encoding="BIG5"', '').replace('encoding="big5"', '')
            
            root = ET.fromstring(content)
            parsed_data = []
            
            for ship in root.findall('SHIP'):
                try:
                    cname = ship.find('VESSEL_CNAME').text if ship.find('VESSEL_CNAME') is not None else ""
                    gt_node = ship.find('GROSS_TOA')
                    gt = int(round(float(gt_node.text))) if gt_node is not None and gt_node.text else 0
                    
                    if gt <= 500 and "東湧8號" not in cname: continue
                    
                    # 時間解析
                    raw_tm = ship.find('PILOT_EXP_TM').text if ship.find('PILOT_EXP_TM') is not None else ""
                    d_disp, t_disp = "未排定", "未排定"
                    if len(raw_tm) >= 12:
                        d_disp = f"{raw_tm[4:6]}/{raw_tm[6:8]}"
                        t_disp = f"{raw_tm[8:10]}:{raw_tm[10:12]}"
                    
                    # 碼頭安全檢查
                    raw_wharf = ship.find('WHARF_CODE').text if ship.find('WHARF_CODE') is not None else ""
                    wharf_label = raw_wharf
                    if raw_wharf:
                        match = re.search(r'(\d+)', raw_wharf)
                        if match:
                            wharf_label = f"{int(match.group(1)):02d}號碼頭"

                    parsed_data.append({
                        "日期": d_disp, "時間": t_disp,
                        "狀態": ship.find('SP_STS').text if ship.find('SP_STS') is not None else "",
                        "碼頭": wharf_label, "中文船名": cname,
                        "長度(m)": int(round(float(ship.find('LOA').text))) if ship.find('LOA') is not None else 0,
                        "代理行": (ship.find('PBG_NAME').text or "")[:2],
                        "總噸位": gt
                    })
                except: continue
            
            driver.quit()
            status.update(label=f"✅ 區段查詢完成", state="complete", expanded=False)
            return pd.DataFrame(parsed_data)

        except Exception as e:
            if 'driver' in locals(): driver.quit()
            status.update(label=f"❌ 錯誤: {str(e)}", state="error")
            return pd.DataFrame()

# --- 5. UI 介面 ---
st.title("🚢 花蓮港船舶動態查詢")

now_init = get_taiwan_time()
f24 = now_init + timedelta(hours=24)

st.radio(
    "⏱️ **快捷查詢區間 (點選後自動執行)**",
    ["未來 24H", "未來 3 日", "前 7 日", "本月整月", "手動調整"],
    key="ui_option",
    on_change=on_ui_change,
    horizontal=True
)

with st.expander("📆 詳細時間確認", expanded=True):
    c1, c2 = st.columns(2)
    with c1:
        sd_in = st.date_input("開始日期", key="sd_key", value=now_init.date())
        st_in = st.time_input("開始時間", key="st_key", value=now_init.time(), label_visibility="collapsed")
    with c2:
        ed_in = st.date_input("結束日期", key="ed_key", value=f24.date())
        et_in = st.time_input("結束時間", key="et_key", value=f24.time(), label_visibility="collapsed")

start_dt = datetime.combine(sd_in, st_in)
end_dt = datetime.combine(ed_in, et_in)

if st.button("🚀 開始查詢", type="primary", use_container_width=True):
    st.session_state.trigger_search = True

if st.session_state.trigger_search:
    st.session_state.trigger_search = False
    
    # 延遲機制
    if not st.session_state.first_load and st.session_state.ui_option != "手動調整":
        with st.info("⏳ 準備查詢中，請稍候 2 秒..."):
            time.sleep(2)
    st.session_state.first_load = False

    segments = split_date_range(start_dt, end_dt)
    all_dfs = []
    for i, (s, e) in enumerate(segments):
        df_seg = run_scraper_segment(s, e, f"({i+1}/{len(segments)})")
        if not df_seg.empty:
            all_dfs.append(df_seg)
    
    if all_dfs:
        final_df = pd.concat(all_dfs).drop_duplicates().sort_values(by=["日期", "時間"])
        st.success(f"🎊 查詢完成！共獲取 {len(final_df)} 筆資料。")
        st.dataframe(final_df, use_container_width=True, hide_index=True)
        csv = final_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 下載完整報表", csv, f"Report_{start_dt.strftime('%m%d')}.csv", use_container_width=True)
    else:
        st.warning("⚠️ 該區間查無資料。")
