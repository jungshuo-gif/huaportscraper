import streamlit as st
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType
import time
import re
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, time as dt_time

# --- 1. 基礎設定與時間函式 ---
st.set_page_config(page_title="花蓮港船舶即時查詢", layout="wide")

def get_taiwan_time():
    """取得當前台灣時間"""
    return (datetime.utcnow() + timedelta(hours=8)).replace(second=0, microsecond=0)

def split_date_range(start, end):
    """將長區間拆分為多個 7 天內的區段"""
    segments = []
    current_start = start
    while current_start < end:
        # 結束點為開始點 + 7天，但不超過最終結束時間
        current_end = min(current_start + timedelta(days=7), end)
        segments.append((current_start, current_end))
        # 下一段從結束點後 1 分鐘開始，避免資料重疊
        current_start = current_end + timedelta(minutes=1)
    return segments

# --- 2. 初始化與 UI 連動邏輯 ---
if 'trigger_search' not in st.session_state:
    st.session_state.trigger_search = False

def update_time_fields():
    """單選鈕改變時，即時更新輸入框內容"""
    now = get_taiwan_time()
    opt = st.session_state.temp_option
    new_sd, new_st = now.date(), now.time()
    new_ed, new_et = now.date(), now.time()

    if opt == "未來 24H":
        f = now + timedelta(hours=24); new_ed, new_et = f.date(), f.time()
    elif opt == "未來 3 日":
        f = now + timedelta(hours=72); new_ed, new_et = f.date(), f.time()
    elif opt == "前 7 日":
        p = now - timedelta(days=7); new_sd, new_st = p.date(), dt_time(0, 0)
    elif opt == "本月整月":
        # 此處不再受 7 天限制，直接設為月初到今天
        first_day = now.replace(day=1, hour=0, minute=0)
        new_sd, new_st = first_day.date(), first_day.time()

    st.session_state.sd_key = new_sd
    st.session_state.st_key = new_st
    st.session_state.ed_key = new_ed
    st.session_state.et_key = new_et
    
    if opt != "手動調整":
        st.session_state.trigger_search = True

# --- 3. 核心爬蟲函數 (單次執行) ---
def run_scraper(start_time, end_time, current_step=1, total_steps=1):
    download_dir = os.path.join(os.getcwd(), "temp_downloads")
    if not os.path.exists(download_dir): os.makedirs(download_dir)
    # 僅在第一步時清理目錄
    if current_step == 1:
        for f in os.listdir(download_dir):
            try: os.remove(os.path.join(download_dir, f))
            except: pass

    step_info = f"({current_step}/{total_steps})" if total_steps > 1 else ""
    with st.status(f"🚢 正在執行查詢 {step_info}...", expanded=True) as status:
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
            try:
                h_tab = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(),'花蓮港')]")))
                driver.execute_script("arguments[0].click();", h_tab)
            except: pass

            val_start = start_time.strftime("%Y/%m/%d %H:%M")
            val_end = end_time.strftime("%Y/%m/%d %H:%M")
            status.write(f"📝 區段填寫: {val_start} ~ {val_end}")
            
            all_inps = driver.find_elements(By.TAG_NAME, "input")
            d_inps = [i for i in all_inps if i.get_attribute("value") and i.get_attribute("value").startswith("20")]
            if len(d_inps) >= 2:
                driver.execute_script(f"arguments[0].value = '{val_start}'; arguments[0].dispatchEvent(new Event('change'));", d_inps[0])
                driver.execute_script(f"arguments[0].value = '{val_end}'; arguments[0].dispatchEvent(new Event('change'));", d_inps[1])
            
            query_btn = driver.find_element(By.XPATH, "//*[contains(@value,'Query') or contains(@value,'查詢')]")
            driver.execute_script("arguments[0].click();", query_btn)
            time.sleep(4)
            
            btns = driver.find_elements(By.XPATH, "//*[contains(text(), 'XML') or contains(@value, 'XML')]")
            if btns: driver.execute_script("arguments[0].click();", btns[0])
            
            downloaded_file = None
            for _ in range(15):
                time.sleep(1)
                xml_fs = [os.path.join(download_dir, f) for f in os.listdir(download_dir) if f.lower().endswith('.xml')]
                if xml_fs:
                    # 取最新下載的檔案
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
                if gt < 500: continue

                w_n = ship.find('WHARF_CODE')
                raw_w = w_n.text if w_n is not None else ""
                w_label = raw_w
                if raw_w:
                    m = re.search(r'(\d+)', raw_w)
                    if m: w_label = f"{int(m.group(1)):02d}號碼頭"

                t_n = ship.find('PILOT_EXP_TM')
                raw_t = t_n.text if t_n is not None else ""
                d_s, t_s = "未排定", "未排定"
                if len(raw_t) >= 12:
                    d_s, t_s = f"{raw_t[4:6]}/{raw_t[6:8]}", f"{raw_t[8:10]}:{raw_t[10:12]}"

                parsed.append({
                    "日期": d_s, "時間": t_s, "狀態": ship.find('SP_STS').text if ship.find('SP_STS') is not None else "",
                    "碼頭": w_label, "中文船名": ship.find('VESSEL_CNAME').text if ship.find('VESSEL_CNAME') is not None else "",
                    "總噸位": gt
                })

            driver.quit()
            status.update(label=f"✅ 區段 {current_step} 完成", state="complete", expanded=False)
            return pd.DataFrame(parsed)
        except Exception as e:
            st.error(f"❌ 錯誤: {e}")
            if 'driver' in locals(): driver.quit()
            return pd.DataFrame()

# --- 4. UI 佈局 ---
st.title("🚢 花蓮港船舶動態查詢 (跨週合併版)")

st.radio(
    "⏱️ **快捷查詢區間 (點選後 2 秒自動執行)**",
    ["未來 24H", "未來 3 日", "前 7 日", "本月整月", "手動調整"],
    key="temp_option",
    on_change=update_time_fields,
    horizontal=True
)

now = get_taiwan_time()
with st.expander("📆 詳細時間確認", expanded=True):
    c1, c2 = st.columns(2)
    with c1:
        sd = st.date_input("開始日期", key="sd_key", value=now.date())
        st_i = st.time_input("開始時間", key="st_key", value=now.time(), label_visibility="collapsed")
    with c2:
        ed = st.date_input("結束日期", key="ed_key", value=now.date())
        et_i = st.time_input("結束時間", key="et_key", value=now.time(), label_visibility="collapsed")

start_dt = datetime.combine(sd, st_i)
end_dt = datetime.combine(ed, et_i)

# --- 5. 執行與合併邏輯 ---
if st.button("🚀 開始查詢", type="primary", use_container_width=True):
    st.session_state.trigger_search = True

if st.session_state.trigger_search:
    st.session_state.trigger_search = False
    
    # 拆分時間區段
    date_segments = split_date_range(start_dt, end_dt)
    all_results = []
    
    if len(date_segments) > 1:
        st.info(f"⏳ 偵測到區間超過 7 天，系統將分 {len(date_segments)} 次查詢並合併結果...")
    
    if st.session_state.temp_option != "手動調整":
        time.sleep(2) # 防抖延遲
    
    # 循環執行爬蟲
    for i, (seg_start, seg_end) in enumerate(date_segments):
        df_seg = run_scraper(seg_start, seg_end, current_step=i+1, total_steps=len(date_segments))
        if not df_seg.empty:
            all_results.append(df_seg)
    
    # 合併並去重
    if all_results:
        final_df = pd.concat(all_results).drop_duplicates().sort_values(by=["日期", "時間"])
        st.success(f"🎊 全部查詢完成！共計 {len(final_df)} 筆船舶資料。")
        st.dataframe(final_df, use_container_width=True, hide_index=True)
        
        csv = final_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 下載完整報表", csv, f"Monthly_Report_{now.strftime('%m%d')}.csv", use_container_width=True)
    else:
        st.warning("⚠️ 所選區間內查無船舶資料。")
