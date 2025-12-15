import streamlit as st
import pandas as pd
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType
from selenium.webdriver.support.ui import Select
import time
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, time as dt_time

# --- 網頁設定 ---
st.set_page_config(page_title="花蓮港船舶即時查詢", layout="wide")

# 定義台灣時間 (UTC+8)
def get_taiwan_time():
    return datetime.utcnow() + timedelta(hours=8)

# --- 關鍵函式：取得「鎖定」的時間 (最近的 20 分鐘) ---
# 用途：讓 20 分鐘內進來的所有使用者，查詢參數都一樣，才能命中快取！
def get_rounded_time(dt=None):
    if dt is None:
        dt = get_taiwan_time()
    # 將分鐘數捨去到最近的 20 分鐘倍數 (例如 14:15 -> 14:00, 14:25 -> 14:20)
    minute_interval = 20
    new_minute = (dt.minute // minute_interval) * minute_interval
    return dt.replace(minute=new_minute, second=0, microsecond=0)

# --- 初始化 Session State (自動執行邏輯) ---
if 'init_done' not in st.session_state:
    # 這是使用者第一次打開網頁 (或重新整理)
    now = get_taiwan_time()
    
    # 1. 預設設定：未來 24 小時
    # 注意：這裡我們用 rounded_time 作為基準，確保能吃到快取
    base_time = get_rounded_time(now)
    
    st.session_state['start_date'] = base_time.date()
    st.session_state['start_time'] = base_time.time()
    
    future = base_time + timedelta(hours=24)
    st.session_state['end_date'] = future.date()
    st.session_state['end_time'] = future.time()
    
    # 2. 開啟自動執行開關 (一進來就跑！)
    st.session_state['auto_run'] = True
    
    # 3. 標記初始化完成
    st.session_state['init_done'] = True

# 補齊其他變數
if 'start_date' not in st.session_state: st.session_state['start_date'] = get_taiwan_time().date()
if 'start_time' not in st.session_state: st.session_state['start_time'] = get_taiwan_time().time()
if 'end_date' not in st.session_state: st.session_state['end_date'] = get_taiwan_time().date()
if 'end_time' not in st.session_state: st.session_state['end_time'] = get_taiwan_time().time()
if 'auto_run' not in st.session_state: st.session_state['auto_run'] = False

# --- 主畫面標題 ---
st.title("🚢 花蓮港船舶動態查詢 (Web V11)")

# --- 操作面板 ---
with st.container():
    st.write("⏱️ **快速查詢 (點擊即執行)**")
    b1, b2, b3, b4 = st.columns(4)
    now = get_taiwan_time()

    with b1:
        if st.button("⏰ 未來24H", use_container_width=True):
            # 按下按鈕時，我們也使用 rounded_time，這樣能利用到自動執行的快取
            r_now = get_rounded_time(now)
            st.session_state['start_date'] = r_now.date()
            st.session_state['start_time'] = r_now.time()
            future = r_now + timedelta(hours=24)
            st.session_state['end_date'] = future.date()
            st.session_state['end_time'] = future.time()
            st.session_state['auto_run'] = True
            st.rerun()

    with b2:
        if st.button("📅 未來3日", use_container_width=True):
            st.session_state['start_date'] = now.date()
            st.session_state['start_time'] = now.time()
            future = now + timedelta(hours=72)
            st.session_state['end_date'] = future.date()
            st.session_state['end_time'] = future.time()
            st.session_state['auto_run'] = True
            st.rerun()

    with b3:
        if st.button("⏮️ 前3日", use_container_width=True):
            past = now - timedelta(days=3)
            st.session_state['start_date'] = past.date()
            st.session_state['start_time'] = dt_time(0, 0)
            st.session_state['end_date'] = now.date()
            st.session_state['end_time'] = now.time()
            st.session_state['auto_run'] = True
            st.rerun()

    with b4:
        if st.button("🗓️ 本月整月", use_container_width=True):
            first_day = now.replace(day=1, hour=0, minute=0, second=0)
            st.session_state['start_date'] = first_day.date()
            st.session_state['start_time'] = first_day.time()
            st.session_state['end_date'] = now.date()
            st.session_state['end_time'] = now.time()
            st.session_state['auto_run'] = True
            st.rerun()

    with st.expander("📆 詳細日期設定 (點擊展開)", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            st.caption("開始時間")
            col_d1, col_t1 = st.columns([3, 2])
            with col_d1: s_date = st.date_input("開始日期", key='start_date', label_visibility="collapsed")
            with col_t1: s_time = st.time_input("開始時間", key='start_time', label_visibility="collapsed")
        with c2:
            st.caption("結束時間")
            col_d2, col_t2 = st.columns([3, 2])
            with col_d2: e_date = st.date_input("結束日期", key='end_date', label_visibility="collapsed")
            with col_t2: e_time = st.time_input("結束時間", key='end_time', label_visibility="collapsed")

    start_dt = datetime.combine(s_date, s_time)
    end_dt = datetime.combine(e_date, e_time)

    manual_run = st.button("🚀 開始查詢", type="primary", use_container_width=True)
    st.markdown("---")

# --- 核心爬蟲 (加入快取機制: ttl=1200秒/20分鐘) ---
# 只要輸入參數 (str_start, str_end) 相同，20分鐘內就會直接回傳快取結果
@st.cache_data(ttl=1200, show_spinner=False)
def run_scraper_cached(str_start_param, str_end_param):
    
    # 這裡將傳入的參數轉回 datetime，用於後續計算 (雖然爬蟲用不到，但保持邏輯一致)
    # 真正的爬蟲輸入是直接用參數的字串，確保 Cache Key 一致
    
    download_dir = os.path.join(os.getcwd(), "temp_downloads")
    if not os.path.exists(download_dir): os.makedirs(download_dir)
    for f in os.listdir(download_dir):
        try: os.remove(os.path.join(download_dir, f))
        except: pass

    driver = None
    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new") 
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        prefs = {"download.default_directory": download_dir, "download.prompt_for_download": False, "download.directory_upgrade": True, "safebrowsing.enabled": True}
        options.add_experimental_option("prefs", prefs)
        
        service = Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())
        driver = webdriver.Chrome(service=service, options=options)
        
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": """Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"""})
        driver.execute_cdp_cmd('Page.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': download_dir})
        
        driver.get("https://tpnet.twport.com.tw/IFAWeb/Function?_RedirUrl=/IFAWeb/Reports/HistoryPortShipList")
        wait = WebDriverWait(driver, 20)
        
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        if iframes: driver.switch_to.frame(0)
        time.sleep(1)
        
        try:
            hualien_tab = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(),'花蓮港')]")))
            driver.execute_script("arguments[0].click();", hualien_tab)
            time.sleep(1)
        except: pass

        # --- 使用傳入的參數填寫日期 ---
        # 注意：這邊直接用傳進來的 str_start_param (已經是格式化好的字串)
        
        all_inputs = driver.find_elements(By.TAG_NAME, "input")
        text_inputs = [i for i in all_inputs if i.get_attribute('type') in ['text', '']]
        visible_inputs = [i for i in text_inputs if i.is_displayed()]
        
        if len(visible_inputs) >= 2:
            driver.execute_script(f"arguments[0].value = '{str_start_param}'; arguments[0].dispatchEvent(new Event('change'));", visible_inputs[0])
            driver.execute_script(f"arguments[0].value = '{str_end_param}'; arguments[0].dispatchEvent(new Event('change'));", visible_inputs[1])
        
        # 排序
        try:
            sort_select = driver.find_element(By.XPATH, "//*[contains(text(),'Ordering by')]/following::select[1]")
            Select(sort_select).select_by_index(1)
        except: pass
        
        # 清除 Checkbox
        try:
            checked_boxes = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']:checked")
            for cb in checked_boxes: driver.execute_script("arguments[0].click();", cb)
        except: pass
        
        query_btn = driver.find_element(By.XPATH, "//*[contains(@value,'Query') or contains(@value,'查詢')]")
        driver.execute_script("arguments[0].click();", query_btn)
        time.sleep(4)
        
        # 下載 XML
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(0)
        except: pass
        
        clicked = False
        btns = driver.find_elements(By.XPATH, "//*[contains(text(), 'XML') or contains(@value, 'XML')]")
        for btn in btns:
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                clicked = True
                break
        
        if not clicked:
            export_btns = driver.find_elements(By.XPATH, "//a[contains(@title, 'Export') or contains(@title, '匯出')]")
            if not export_btns: export_btns = driver.find_elements(By.XPATH, "//img[contains(@alt, 'Export') or contains(@alt, '匯出')]/..")
            if export_btns:
                driver.execute_script("arguments[0].click();", export_btns[0])
                time.sleep(1)
                xml_items = driver.find_elements(By.XPATH, "//a[contains(text(), 'XML')]")
                if xml_items:
                    driver.execute_script("arguments[0].click();", xml_items[0])
                    clicked = True

        downloaded_file = None
        for _ in range(20):
            time.sleep(1)
            files = [f for f in os.listdir(download_dir) if f.endswith('.xml')]
            if files:
                downloaded_file = os.path.join(download_dir, files[0])
                break
        
        if not downloaded_file: raise Exception("未偵測到下載檔案")
            
        with open(downloaded_file, 'r', encoding='big5', errors='replace') as f:
            xml_content = f.read().replace('encoding="BIG5"', '').replace('encoding="big5"', '')
            
        root = ET.fromstring(xml_content)
        parsed_data = []
        
        for ship in root.findall('SHIP'):
            try:
                cname = ship.find('VESSEL_CNAME').text or ""
                
                gt_str = ship.find('GROSS_TOA').text or "0"
                try: gt = int(round(float(gt_str)))
                except: gt = 0
                
                # if gt <= 500 ... (已移除過濾)
                
                pilot_time_raw = ship.find('PILOT_EXP_TM').text or ""
                date_display, time_display = "", ""
                if len(pilot_time_raw) >= 12:
                    date_display = f"{pilot_time_raw[4:6]}/{pilot_time_raw[6:8]}"
                    time_display = f"{pilot_time_raw[8:10]}:{pilot_time_raw[10:12]}"
                
                raw_agent = ship.find('PBG_NAME').text or ""
                agent_full = raw_agent.strip()
                if "台灣船運" in agent_full: agent_name = "台船"
                elif "海軍" in agent_full: agent_name = "海軍"
                else: agent_name = agent_full[:2] 
                
                loa_str = ship.find('LOA').text or "0"
                try: loa = int(round(float(loa_str)))
                except: loa = 0
                
                # --- 處理碼頭名稱 ---
                raw_wharf = ship.find('WHARF_CODE').text or ""
                wharf_display = raw_wharf
                match = re.search(r'(\d+)', raw_wharf)
                if match:
                    wharf_num = int(match.group(1))
                    wharf_display = f"{wharf_num:02d}號碼頭"
                # ------------------

                parsed_data.append({
                    "日期": date_display,
                    "時間": time_display,
                    "狀態": ship.find('SP_STS').text,
                    "碼頭": wharf_display,
                    "中文船名": cname,
                    "長度(m)": loa,
                    "英文船名": ship.find('VESSEL_ENAME').text,
                    "代理行": agent_name,  
                    "總噸位": gt,
                    "前一港": ship.find('BEFORE_PORT').text,
                    "下一港": ship.find('NEXT_PORT').text,
                })
            except: continue
        
        return pd.DataFrame(parsed_data)

    except Exception as e:
        st.error(f"❌ 發生錯誤: {str(e)}")
        return None
    finally:
        if driver: driver.quit()

# --- 觸發執行 (整合自動與手動) ---
if manual_run or st.session_state.get('auto_run', False):
    st.session_state['auto_run'] = False # 關閉自動執行，避免重整頁面重複觸發
    
    if start_dt > end_dt:
        st.error("❌ 開始時間不能晚於結束時間")
    else:
        # 這裡將日期轉成字串傳給爬蟲函式
        # 這樣做的好處是：如果不同使用者的 'start_dt' 是一樣的(例如都被鎖定在 14:00)，
        # 傳進去的字串就會一樣，Streamlit 就會直接調用快取，不會重新跑爬蟲！
        s_str = start_dt.strftime("%Y/%m/%d %H:%M")
        e_str = end_dt.strftime("%Y/%m/%d %H:%M")
        
        with st.spinner("⏳ 正在連線更新資料 (若為快取則瞬間顯示)..."):
            df = run_scraper_cached(s_str, e_str)
            
        if df is not None and not df.empty:
            df = df.sort_values(by=["日期", "時間"])
            
            st.success(f"✅ 查詢完成！({s_str} - {e_str})")
            
            cols = ["日期", "時間", "狀態", "碼頭", "中文船名", "長度(m)", "英文船名", "總噸位", "前一港", "下一港", "代理行"]
            final_cols = [c for c in cols if c in df.columns]
            
            st.dataframe(
                df[final_cols], 
                use_container_width=True, 
                hide_index=True
            )
            
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 下載報表",
                data=csv,
                file_name=f"花蓮港_{start_dt.strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv",
                type="primary",
                use_container_width=True
            )
        elif df is not None:
            st.warning("⚠️ 此區間查無符合條件的船舶資料")
