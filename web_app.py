import streamlit as st
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType
import time
import os
import xml.etree.ElementTree as ET

# --- 網頁設定 ---
st.set_page_config(page_title="花蓮港船舶即時查詢", layout="wide")
st.title("🚢 花蓮港船舶動態查詢系統")
st.markdown("---")

# --- 側邊欄：輸入條件 ---
with st.sidebar:
    st.header("🔍 查詢設定")
    start_date = st.date_input("開始日期")
    end_date = st.date_input("結束日期")
    run_btn = st.button("開始查詢", type="primary")

# --- 核心爬蟲邏輯 ---
def run_scraper(start_time, end_time):
    download_dir = os.path.join(os.getcwd(), "temp_downloads")
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)
    
    # 清理舊檔
    for f in os.listdir(download_dir):
        try: os.remove(os.path.join(download_dir, f))
        except: pass

    status_text = st.empty()
    status_text.info("🚀 正在啟動雲端瀏覽器核心...")
    
    driver = None
    try:
        options = webdriver.ChromeOptions()
        # --- 雲端環境必要設定 (Headless) ---
        options.add_argument("--headless") 
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
        
        # --- 關鍵：在 Linux 環境使用 Chromium ---
        # 這裡指定使用 ChromeType.CHROMIUM，這是 Streamlit Cloud 支援的版本
        service = Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())
        driver = webdriver.Chrome(service=service, options=options)
        
        # 防偵測設定
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"""
        })
        driver.execute_cdp_cmd('Page.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': download_dir})
        
        status_text.info(f"🔗 連線中...")
        driver.get("https://tpnet.twport.com.tw/IFAWeb/Function?_RedirUrl=/IFAWeb/Reports/HistoryPortShipList")
        
        wait = WebDriverWait(driver, 20)
        
        # --- 切換 iFrame ---
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        if iframes: driver.switch_to.frame(0)
        time.sleep(1)
        
        # --- 點擊花蓮港 ---
        try:
            hualien_tab = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(),'花蓮港')]")))
            driver.execute_script("arguments[0].click();", hualien_tab)
            time.sleep(1)
        except: pass

        # --- 輸入日期 ---
        str_start = start_time.strftime("%Y/%m/%d") + " 00:00"
        str_end = end_time.strftime("%Y/%m/%d") + " 23:59"
        
        all_inputs = driver.find_elements(By.TAG_NAME, "input")
        text_inputs = [i for i in all_inputs if i.get_attribute('type') in ['text', '']]
        target_inputs = [inp for inp in text_inputs if inp.get_attribute("value") and "20" in inp.get_attribute("value")]
        
        if len(target_inputs) >= 2:
            driver.execute_script(f"arguments[0].value = '{str_start}'; arguments[0].dispatchEvent(new Event('change'));", target_inputs[0])
            driver.execute_script(f"arguments[0].value = '{str_end}'; arguments[0].dispatchEvent(new Event('change'));", target_inputs[1])
        
        # --- 點擊查詢 ---
        status_text.info("🔍 查詢資料中...")
        query_btn = driver.find_element(By.XPATH, "//*[contains(@value,'Query') or contains(@value,'查詢')]")
        driver.execute_script("arguments[0].click();", query_btn)
        time.sleep(5) 
        
        # --- 下載 XML ---
        status_text.info("📥 嘗試下載報表...")
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
            export_btns = driver.find_elements(By.XPATH, "//a[contains(@title, 'Export')]")
            if not export_btns: export_btns = driver.find_elements(By.XPATH, "//img[contains(@alt, 'Export')]/..")
            if export_btns:
                driver.execute_script("arguments[0].click();", export_btns[0])
                time.sleep(1)
                xml_items = driver.find_elements(By.XPATH, "//a[contains(text(), 'XML')]")
                if xml_items:
                    driver.execute_script("arguments[0].click();", xml_items[0])

        # --- 等待檔案 ---
        downloaded_file = None
        for _ in range(15):
            time.sleep(1)
            files = [f for f in os.listdir(download_dir) if f.endswith('.xml')]
            if files:
                downloaded_file = os.path.join(download_dir, files[0])
                break
        
        if not downloaded_file:
            raise Exception("未偵測到下載檔案")
            
        # --- 解析 XML ---
        status_text.info("⚙️ 解析資料...")
        with open(downloaded_file, 'r', encoding='big5', errors='replace') as f:
            xml_content = f.read().replace('encoding="BIG5"', '').replace('encoding="big5"', '')
            
        root = ET.fromstring(xml_content)
        parsed_data = []
        
        for ship in root.findall('SHIP'):
            try:
                cname = ship.find('VESSEL_CNAME').text or ""
                gt_str = ship.find('GROSS_TOA').text or "0"
                try: gt = int(float(gt_str))
                except: gt = 0
                
                if gt <= 500 and "東湧8號" not in cname: continue
                
                pilot_time_raw = ship.find('PILOT_EXP_TM').text or ""
                date_display, time_display = "", ""
                if len(pilot_time_raw) >= 12:
                    date_display = f"{pilot_time_raw[4:6]}/{pilot_time_raw[6:8]}"
                    time_display = f"{pilot_time_raw[8:10]}:{pilot_time_raw[10:12]}"
                
                parsed_data.append({
                    "日期": date_display,
                    "時間": time_display,
                    "狀態": ship.find('SP_STS').text,
                    "碼頭": ship.find('WHARF_CODE').text,
                    "中文船名": cname,
                    "英文船名": ship.find('VESSEL_ENAME').text,
                    "GT": gt,
                    "前一港": ship.find('BEFORE_PORT').text,
                    "下一港": ship.find('NEXT_PORT').text,
                })
            except: continue
            
        status_text.success("✅ 成功！")
        return pd.DataFrame(parsed_data)

    except Exception as e:
        status_text.error(f"❌ 錯誤: {str(e)}")
        return None
    finally:
        if driver: driver.quit()

# --- 主程式 ---
if run_btn:
    if start_date > end_date:
        st.error("❌ 日期錯誤")
    else:
        df = run_scraper(start_date, end_date)
        if df is not None and not df.empty:
            df = df.sort_values(by=["日期", "時間"])
            st.dataframe(df, use_container_width=True)
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 下載 CSV", csv, "shipping_data.csv", "text/csv")
        elif df is not None:
            st.warning("⚠️ 查無資料")
