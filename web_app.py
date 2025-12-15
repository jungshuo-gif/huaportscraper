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
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, time as dt_time

# --- 網頁設定 ---
st.set_page_config(page_title="花蓮港船舶即時查詢", layout="wide")

# 定義台灣時間 (UTC+8)
def get_taiwan_time():
    return datetime.utcnow() + timedelta(hours=8)

# --- 關鍵函式：將時間鎖定在最近的 20 分鐘 (為了讓快取生效) ---
def get_rounded_time(dt=None, minute_interval=20):
    if dt is None:
        dt = get_taiwan_time()
    # 將分鐘數捨去到最近的 20 分鐘倍數 (例如 14:15 -> 14:00, 14:25 -> 14:20)
    new_minute = (dt.minute // minute_interval) * minute_interval
    return dt.replace(minute=new_minute, second=0, microsecond=0)

# --- 核心爬蟲 (加入快取機制 ttl=1200秒/20分鐘) ---
@st.cache_data(ttl=1200, show_spinner=False)
def run_scraper_cached(base_time_str):
    # 注意：這裡傳入字串 base_time_str 只是為了讓快取機制辨識「輸入變了沒」
    # 我們實際計算還是用當下時間，但要還原回 datetime 物件
    
    # 解析傳入的時間字串
    base_time = datetime.strptime(base_time_str, "%Y-%m-%d %H:%M:%S")
    
    # 設定查詢範圍：從「鎖定的時間點」往後推 24 小時
    start_time = base_time
    end_time = base_time + timedelta(hours=24)
    
    # --- 以下是原本的爬蟲邏輯 ---
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
        
        try:
            hualien_tab = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(),'花蓮港')]")))
            driver.execute_script("arguments[0].click();", hualien_tab)
            time.sleep(1)
        except: pass

        # 輸入日期
        str_start = start_time.strftime("%Y/%m/%d %H:%M") 
        str_end = end_time.strftime("%Y/%m/%d %H:%M")
        
        all_inputs = driver.find_elements(By.TAG_NAME, "input")
        text_inputs = [i for i in all_inputs if i.get_attribute('type') in ['text', '']]
        visible_inputs = [i for i in text_inputs if i.is_displayed()]
        
        if len(visible_inputs) >= 2:
            driver.execute_script(f"arguments[0].value = '{str_start}'; arguments[0].dispatchEvent(new Event('change'));", visible_inputs[0])
            driver.execute_script(f"arguments[0].value = '{str_end}'; arguments[0].dispatchEvent(new Event('change'));", visible_inputs[1])

        # 排序與清除 Checkbox
        try:
            sort_select = driver.find_element(By.XPATH, "//*[contains(text(),'Ordering by')]/following::select[1]")
            Select(sort_select).select_by_index(1)
        except: pass
        try:
            checked_boxes = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']:checked")
            for cb in checked_boxes: driver.execute_script("arguments[0].click();", cb)
        except: pass
        
        query_btn = driver.find_element(By.XPATH, "//*[contains(@value,'Query') or contains(@value,'查詢')]")
        driver.execute_script("arguments[0].click();", query_btn)
        time.sleep(4)
        
        # 下載
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
        
        if not downloaded_file: return None
            
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

                parsed_data.append({
                    "日期": date_display,
                    "時間": time_display,
                    "狀態": ship.find('SP_STS').text,
                    "碼頭": ship.find('WHARF_CODE').text,
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
    except: return None
    finally:
        if driver: driver.quit()

# --- 主畫面標題 ---
st.title("🚢 花蓮港船舶即時看板")
st.caption("預設顯示未來 24 小時動態 (每 20 分鐘自動更新)")

# --- 自動執行邏輯 ---
# 1. 取得現在時間，並鎖定到最近的 20 分鐘 (例如 14:13 -> 14:00)
# 這樣 20 分鐘內進來的人，base_time 都是一樣的，就會共用同一份快取！
rounded_now = get_rounded_time()
base_time_str = rounded_now.strftime("%Y-%m-%d %H:%M:%S")

# 2. 直接執行 (如果有快取會秒開，沒快取會跑爬蟲)
with st.spinner(f"正在載入最新資料 (上次更新: {rounded_now.strftime('%H:%M')})..."):
    df = run_scraper_cached(base_time_str)

# 3. 顯示結果
if df is not None and not df.empty:
    df = df.sort_values(by=["日期", "時間"])
    
    # 統計指標
    col1, col2, col3 = st.columns(3)
    col1.metric("總船數", f"{len(df)} 艘")
    col2.metric("更新時間", rounded_now.strftime("%H:%M"))
    col3.metric("下次更新", (rounded_now + timedelta(minutes=20)).strftime("%H:%M"))

    cols = ["日期", "時間", "狀態", "碼頭", "中文船名", "長度(m)", "英文船名", "總噸位", "前一港", "下一港", "代理行"]
    final_cols = [c for c in cols if c in df.columns]
    
    st.dataframe(df[final_cols], use_container_width=True, hide_index=True)
    
    csv = df.to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 下載 CSV", data=csv, file_name=f"花蓮港_{rounded_now.strftime('%H%M')}.csv", mime="text/csv", type="primary", use_container_width=True)

elif df is None:
    st.error("❌ 連線失敗，請重新整理頁面。")
else:
    st.warning("⚠️ 目前未來 24 小時內無船舶動態。")
