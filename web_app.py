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
def get_rounded_time(dt=None):
    if dt is None:
        dt = get_taiwan_time()
    minute_interval = 20
    new_minute = (dt.minute // minute_interval) * minute_interval
    return dt.replace(minute=new_minute, second=0, microsecond=0)

# --- 初始化 Session State ---
if 'init_done' not in st.session_state:
    now = get_taiwan_time()
    base_time = get_rounded_time(now)
    
    st.session_state['start_date'] = base_time.date()
    st.session_state['start_time'] = base_time.time()
    
    future = base_time + timedelta(hours=24)
    st.session_state['end_date'] = future.date()
    st.session_state['end_time'] = future.time()
    
    st.session_state['auto_run'] = True
    st.session_state['init_done'] = True

if 'start_date' not in st.session_state: st.session_state['start_date'] = get_taiwan_time().date()
if 'start_time' not in st.session_state: st.session_state['start_time'] = get_taiwan_time().time()
if 'end_date' not in st.session_state: st.session_state['end_date'] = get_taiwan_time().date()
if 'end_time' not in st.session_state: st.session_state['end_time'] = get_taiwan_time().time()
if 'auto_run' not in st.session_state: st.session_state['auto_run'] = False

# --- 主畫面標題 ---
st.title("🚢 花蓮港船舶動態查詢 (Web V12 - V7核心版)")

# --- 操作面板 ---
with st.container():
    st.write("⏱️ **快速查詢 (點擊即執行)**")
    b1, b2, b3, b4 = st.columns(4)
    now = get_taiwan_time()

    with b1:
        if st.button("⏰ 未來24H", use_container_width=True):
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

# --- 核心爬蟲 (V7 邏輯完全移植) ---
@st.cache_data(ttl=1200, show_spinner=False)
def run_scraper_cached(str_start_param, str_end_param):
    download_dir = os.path.join(os.getcwd(), "temp_downloads")
    if not os.path.exists(download_dir): os.makedirs(download_dir)
    for f in os.listdir(download_dir):
        try: os.remove(os.path.join(download_dir, f))
        except: pass

    status_text = st.empty()
    status_text.info("🚀 啟動 V7 穩定核心...")
    
    driver = None
    try:
        # --- 瀏覽器設定 (保持 Streamlit 必要設定) ---
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new") 
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        prefs = {
            "download.default_directory": download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True
        }
        options.add_experimental_option("prefs", prefs)
        
        service = Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())
        driver = webdriver.Chrome(service=service, options=options)
        
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"""
        })
        driver.execute_cdp_cmd('Page.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': download_dir})
        
        status_text.info(f"🔗 連線中...")
        driver.get("https://tpnet.twport.com.tw/IFAWeb/Function?_RedirUrl=/IFAWeb/Reports/HistoryPortShipList")
        
        wait = WebDriverWait(driver, 20)
        
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        if iframes: driver.switch_to.frame(0)
        time.sleep(1)
        
        try:
            hualien_tab = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(),'花蓮港')]")))
            driver.execute_script("arguments[0].click();", hualien_tab)
            time.sleep(1.5)
        except: pass

        # =======================================================
        # ★★★ V7 核心互動邏輯 (完全複製自 downloader_v7穩定.py) ★★★
        # =======================================================

        # 1. 尋找並填寫日期
        all_inputs = driver.find_elements(By.TAG_NAME, "input")
        text_inputs = [i for i in all_inputs if i.get_attribute('type') in ['text', '']]
        
        # V7 邏輯：優先找有值的欄位，如果沒有才找前兩個
        target_date_inputs = [inp for inp in text_inputs if inp.get_attribute("value") and inp.get_attribute("value").startswith("20")]
        
        if len(target_date_inputs) < 2 and len(text_inputs) >= 2:
            target_date_inputs = [text_inputs[0], text_inputs[1]]
            
        if len(target_date_inputs) >= 2:
            # 這裡使用傳入的參數 (str_start_param) 替代 V7 原本的變數
            driver.execute_script(f"arguments[0].value = '{str_start_param}'; arguments[0].dispatchEvent(new Event('change'));", target_date_inputs[0])
            driver.execute_script(f"arguments[0].value = '{str_end_param}'; arguments[0].dispatchEvent(new Event('change'));", target_date_inputs[1])
            status_text.info(f"📝 查詢區間: {str_start_param} ~ {str_end_param}")
        else:
            status_text.warning("⚠️ 警告：無法自動填入日期")

        # 2. 排序 (Ordering by)
        try:
            sort_select = driver.find_element(By.XPATH, "//*[contains(text(),'Ordering by')]/following::select[1]")
            Select(sort_select).select_by_index(1)
        except: pass
        
        # 3. 清除 Checkbox
        try:
            checked_boxes = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']:checked")
            for cb in checked_boxes: driver.execute_script("arguments[0].click();", cb)
        except: pass
        
        # 4. 點擊查詢
        query_btn = driver.find_element(By.XPATH, "//*[contains(@value,'Query') or contains(@value,'查詢')]")
        driver.execute_script("arguments[0].click();", query_btn)
        status_text.info("🔍 送出查詢...")
        time.sleep(4)
        
        # 5. 下載 XML (V7 方法 A + 方法 B 邏輯)
        status_text.info("📥 嘗試下載 XML...")
        try:
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(0)
            except: pass
            
            clicked = False
            
            # 方法 A: 尋找直接的 XML 按鈕
            if not clicked:
                try:
                    btns = driver.find_elements(By.XPATH, "//*[contains(text(), 'XML') or contains(@value, 'XML')]")
                    for btn in btns:
                        if btn.is_displayed():
                            driver.execute_script("arguments[0].click();", btn)
                            clicked = True
                            break
                except: pass
            
            # 方法 B: 尋找 Export 圖示或連結
            if not clicked:
                try:
                    export_btns = driver.find_elements(By.XPATH, "//a[contains(@title, 'Export') or contains(@title, '匯出')]")
                    if not export_btns:
                            export_btns = driver.find_elements(By.XPATH, "//img[contains(@alt, 'Export') or contains(@alt, '匯出')]/..")
                    if export_btns:
                        driver.execute_script("arguments[0].click();", export_btns[0])
                        time.sleep(1)
                        xml_items = driver.find_elements(By.XPATH, "//a[contains(text(), 'XML')]")
                        if xml_items:
                            driver.execute_script("arguments[0].click();", xml_items[0])
                            clicked = True
                except: pass
            
            if not clicked:
                raise Exception("找不到 XML 下載按鈕 (V7邏輯)")

            # 等待下載
            waited = 0
            downloaded_file = None
            while waited < 20:
                time.sleep(1)
                waited += 1
                files = [f for f in os.listdir(download_dir) if f.endswith('.xml')]
                if files:
                    downloaded_file = os.path.join(download_dir, files[0])
                    break
            
            if not downloaded_file:
                raise Exception("下載逾時，未找到 XML 檔案")
        
        except Exception as e:
            raise Exception(f"下載流程錯誤: {e}")

        # =======================================================
        # ★★★ V7 邏輯移植結束 ★★★
        # =======================================================

        status_text.info("⚙️ 解析資料 (Big5)...")
        
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
                if gt <= 500 : continue
                # V7 有過濾，但後來要求全部顯示，所以這裡不做過濾
                
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
                
                # V7 的碼頭邏輯
                raw_wharf = ship.find('WHARF_CODE').text or ""
                wharf_display = raw_wharf
                match = re.search(r'(\d+)', raw_wharf)
                if match:
                    wharf_num = int(match.group(1))
                    wharf_display = f"{wharf_num:02d}號碼頭"

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
        
        status_text.empty()
        return pd.DataFrame(parsed_data)

    except Exception as e:
        status_text.error(f"❌ 錯誤: {str(e)}")
        return None
    finally:
        if driver: driver.quit()

# --- 觸發執行 ---
if manual_run or st.session_state.get('auto_run', False):
    st.session_state['auto_run'] = False
    
    if start_dt > end_dt:
        st.error("❌ 開始時間不能晚於結束時間")
    else:
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

