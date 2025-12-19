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
    st.session_state.trigger_search = True  # 預設啟動初次查詢
if 'expander_state' not in st.session_state:
    st.session_state.expander_state = False # 預設摺疊
if 'last_option' not in st.session_state:
    st.session_state.last_option = "未來 24H"

# --- 3. UI 連動回調 (控制時間與摺疊狀態) ---
def on_ui_change():
    now = get_taiwan_time()
    opt = st.session_state.ui_option
    st.session_state.last_option = opt
    
    # 預設起訖
    sd, st_val = now.date(), now.time()
    ed, et_val = now.date(), now.time()

    if opt == "未來 24H":
        f = now + timedelta(hours=24); ed, et_val = f.date(), f.time()
        st.session_state.expander_state = False
    elif opt == "未來 3 日":
        f = now + timedelta(hours=72); ed, et_val = f.date(), f.time()
        st.session_state.expander_state = False
    elif opt == "前 7 日":
        # 修正：7 天前的 00:00 到 現在
        p = now - timedelta(days=7)
        sd, st_val = p.date(), dt_time(0, 0)
        st.session_state.expander_state = False
    elif opt == "本月整月":
        first_day = now.replace(day=1, hour=0, minute=0)
        sd, st_val = first_day.date(), first_day.time()
        st.session_state.expander_state = False
    elif opt == "手動調整":
        st.session_state.expander_state = True # 切換手動時自動展開

    # 強制更新輸入框
    st.session_state.sd_key = sd
    st.session_state.st_key = st_val
    st.session_state.ed_key = ed
    st.session_state.et_key = et_val
    
    if opt != "手動調整":
        st.session_state.trigger_search = True

# --- 4. 核心爬蟲函數 (統一所有查詢流程) ---
def run_scraper_segment(start_time, end_time, step_text=""):
    download_dir = os.path.join(os.getcwd(), "temp_downloads")
    if not os.path.exists(download_dir): os.makedirs(download_dir)
    for f in os.listdir(download_dir):
        try: os.remove(os.path.join(download_dir, f))
        except: pass

    with st.status(f"🚢 執行查詢 {step_text}...", expanded=True) as status:
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
            
            # A. 統一選港
            h_tab = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(),'花蓮港')]")))
            driver.execute_script("arguments[0].click();", h_tab)

            # B. 統一日期輸入 (JS)
            v_s, v_e = start_time.strftime("%Y/%m/%d %H:%M"), end_time.strftime("%Y/%m/%d %H:%M")
            status.write(f"📝 填寫區間: {v_s} ~ {v_e}")
            inps = driver.find_elements(By.TAG_NAME, "input")
            d_inps = [i for i in inps if i.get_attribute("value") and i.get_attribute("value").startswith("20")]
            if len(d_inps) >= 2:
                driver.execute_script(f"arguments[0].value = '{v_s}'; arguments[0].dispatchEvent(new Event('change'));", d_inps[0])
                driver.execute_script(f"arguments[0].value = '{v_e}'; arguments[0].dispatchEvent(new Event('change'));", d_inps[1])
            
            # C. 統一取消所有 Checkbox
            checked_boxes = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']:checked")
            for cb in checked_boxes: driver.execute_script("arguments[0].click();", cb)

            # D. 統一選擇「預計引水時間」排序
            try:
                sort_sel = driver.find_element(By.XPATH, "//*[contains(text(),'Ordering by')]/following::select[1]")
                Select(sort_sel).select_by_index(1) # Index 1 通常是引水時間
            except: pass

            # E. 統一按查詢
            btn = driver.find_element(By.XPATH, "//*[contains(@value,'Query') or contains(@value,'查詢')]")
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(4)
            
            # F. 統一按 XML 鈕
            xml_btns = driver.find_elements(By.XPATH, "//*[contains(text(), 'XML') or contains(@value, 'XML')]")
            if xml_btns: driver.execute_script("arguments[0].click();", xml_btns[0])
            
            # G. 等待下載並解析
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
                
                # 過濾：GT < 500 (東湧8號例外)
                if gt < 500 and "東湧8號" not in cname: continue

                w_n = ship.find('WHARF_CODE')
                raw_w = w_n.text if w_n is not None else ""
                w_label = f"{int(re.search(r'(\d+)', raw_w).group(1)):02d}號碼頭" if raw_w and re.search(r'(\d+)', raw_w) else raw_w

                t_n = ship.find('PILOT_EXP_TM')
                raw_t = t_n.text if t_n is not None else ""
                d_s, t_s = "未排定", "未排定"
                if len(raw_t) >= 12: d_s, t_s = f"{raw_t[4:6]}/{raw_t[6:8]}", f"{raw_t[8:10]}:{raw_t[10:12]}"

                parsed.append({
                    "日期": d_s, "時間": t_s, 
                    "狀態": ship.find('SP_STS').text if ship.find('SP_STS') is not None else "",
                    "碼頭": w_label, "中文船名": cname,
                    "長度(m)": int(round(float(ship.find('LOA').text))) if ship.find('LOA') is not None else 0,
                    "英文船名": ship.find('VESSEL_ENAME').text if ship.find('VESSEL_ENAME') is not None else "",
                    "總噸位": gt,
                    "前一港": ship.find('BEFORE_PORT').text if ship.find('BEFORE_PORT') is not None else "",
                    "下一港": ship.find('NEXT_PORT').text if ship.find('NEXT_PORT') is not None else "",
                    "代理行": (ship.find('PBG_NAME').text or "")[:2]
                })

            driver.quit()
            status.update(label=f"✅ 區段查詢完成", state="complete", expanded=False)
            return pd.DataFrame(parsed)
        except Exception as e:
            if 'driver' in locals(): driver.quit()
            st.error(f"❌ 錯誤: {e}")
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

# 摺疊式面板：expanded 綁定 session_state
with st.expander("📆 詳細時間確認 (手動變更後需點擊按鈕)", expanded=st.session_state.expander_state):
    c1, c2 = st.columns(2)
    with c1:
        sd_in = st.date_input("開始日期", key="sd_key", value=now_init.date())
        st_in = st.time_input("開始時間", key="st_key", value=now_init.time(), label_visibility="collapsed")
    with c2:
        ed_in = st.date_input("結束日期", key="ed_key", value=f24.date())
        et_in = st.time_input("結束時間", key="et_key", value=f24.time(), label_visibility="collapsed")

start_dt = datetime.combine(sd_in, st_in)
end_dt = datetime.combine(ed_in, et_in)

# --- 6. 執行邏輯 (含分段合併) ---
if st.button("🚀 開始查詢", type="primary", use_container_width=True):
    st.session_state.trigger_search = True

if st.session_state.trigger_search:
    st.session_state.trigger_search = False
    
    # 拆分區段 (突破 7 天限制)
    date_segments = split_date_range(start_dt, end_dt)
    all_dfs = []
    
    # 延遲防抖
    if st.session_state.ui_option != "手動調整":
        time.sleep(1.5)

    for i, (seg_s, seg_e) in enumerate(date_segments):
        df_seg = run_scraper_segment(seg_s, seg_e, f"({i+1}/{len(date_segments)})")
        if not df_seg.empty:
            all_dfs.append(df_seg)
    
    if all_dfs:
        final_df = pd.concat(all_dfs).drop_duplicates().sort_values(by=["日期", "時間"])
        cols = ["日期", "時間", "狀態", "碼頭", "中文船名", "長度(m)", "英文船名", "總噸位", "前一港", "下一港", "代理行"]
        final_df = final_df[cols]
        
        st.success(f"🎊 查詢完成！共獲取 {len(final_df)} 筆船舶資料。")
        st.dataframe(final_df, use_container_width=True, hide_index=True)
        csv = final_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 下載完整報表", csv, f"Report_{start_dt.strftime('%m%d')}.csv", use_container_width=True)
    else:
        st.warning("⚠️ 該區間查無符合條件的船舶資料。")
