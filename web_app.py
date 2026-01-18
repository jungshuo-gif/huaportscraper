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
from datetime import datetime, timedelta

# --- 1. 基礎配置與路徑 ---
st.set_page_config(page_title="花蓮港船舶即時查詢", layout="wide")
CACHE_FILE = "port_data_cache.csv"

def get_taiwan_time():
    return (datetime.utcnow() + timedelta(hours=8)).replace(second=0, microsecond=0)

# --- 2. 核心爬蟲函數 (優化版) ---
def run_scraper_logic(start_time, end_time, label="自動同步"):
    download_dir = os.path.join(os.getcwd(), "temp_downloads")
    if not os.path.exists(download_dir): os.makedirs(download_dir)
    
    driver = None
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

        v_s = start_time.strftime("%Y/%m/%d %H:%M")
        v_e = end_time.strftime("%Y/%m/%d %H:%M")
        
        inps = driver.find_elements(By.TAG_NAME, "input")
        d_inps = [i for i in inps if i.get_attribute("value") and i.get_attribute("value").startswith("20")]
        if len(d_inps) >= 2:
            driver.execute_script(f"arguments[0].value = '{v_s}'; arguments[0].dispatchEvent(new Event('change'));", d_inps[0])
            driver.execute_script(f"arguments[0].value = '{v_e}'; arguments[0].dispatchEvent(new Event('change'));", d_inps[1])
        
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
            gt = int(round(float(ship.find('GROSS_TOA').text))) if ship.find('GROSS_TOA') is not None else 0
            if gt < 500: continue
            
            raw_w = ship.find('WHARF_CODE').text if ship.find('WHARF_CODE') is not None else ""
            w_label = f"{int(re.search(r'(\d+)', raw_w).group(1)):02d}號" if raw_w and re.search(r'(\d+)', raw_w) else raw_w
            raw_t = ship.find('PILOT_EXP_TM').text if ship.find('PILOT_EXP_TM') is not None else ""
            d_s, t_s = (f"{raw_t[4:6]}/{raw_t[6:8]}", f"{raw_t[8:10]}:{raw_t[10:12]}") if len(raw_t) >= 12 else ("未排定", "未排定")

            parsed.append({
                "日期": d_s, "時間": t_s, "狀態": ship.find('SP_STS').text if ship.find('SP_STS') is not None else "",
                "碼頭": w_label, "中文船名": ship.find('VESSEL_CNAME').text or "",
                "長度(m)": int(round(float(ship.find('LOA').text))) if ship.find('LOA') is not None else 0,
                "英文船名": ship.find('VESSEL_ENAME').text or "",
                "總噸位": gt, "前一港": ship.find('BEFORE_PORT').text or "",
                "下一港": ship.find('NEXT_PORT').text or "",
                "代理行": (ship.find('PBG_NAME').text or "")[:2]
            })
        # 刪除暫存檔
        os.remove(downloaded_file)
        return pd.DataFrame(parsed)
    except:
        return pd.DataFrame()
    finally:
        if driver: driver.quit()

# --- 3. 讀取快取邏輯 ---
def get_cached_data():
    if os.path.exists(CACHE_FILE):
        mtime = os.path.getmtime(CACHE_FILE)
        df = pd.read_csv(CACHE_FILE)
        return df, datetime.fromtimestamp(mtime)
    return None, None

# --- 4. UI 介面 ---
st.markdown("### 🚢 花蓮港船舶動態 (20分鐘自動更新)")

df_current, last_update_time = get_cached_data()
now = get_taiwan_time()

# 判斷是否需要啟動爬蟲 (沒檔案 或 超過20分鐘)
need_update = False
if last_update_time is None or (now - last_update_time).total_seconds() > 1200:
    need_update = True

# 🚀 關鍵：先顯示現有資料，不讓使用者等待
if df_current is not None:
    st.caption(f"📅 資料最後同步時間：{last_update_time.strftime('%Y-%m-%d %H:%M:%S')}")
    st.dataframe(df_current, use_container_width=True, hide_index=True)
    csv_data = df_current.to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 下載目前報表", csv_data, "Hualien_Port_Report.csv", use_container_width=True)
else:
    st.warning("⏳ 首次執行，正在進行初次數據擷取，請稍候約 15 秒...")

# --- 5. 背景執行更新 ---
if need_update:
    with st.status("🔄 正在從港務局同步最新數據...", expanded=False) as status:
        # 執行未來 24 小時的爬取
        f24 = now + timedelta(hours=24)
        new_df = run_scraper_logic(now, f24)
        
        if not new_df.empty:
            new_df.to_csv(CACHE_FILE, index=False)
            status.update(label="✅ 同步成功！", state="complete")
            st.rerun() # 更新完後重新整理頁面，使用者就會看到最新結果
        else:
            status.update(label="❌ 同步失敗 (將保留舊數據)", state="error")

# --- 6. 進階查詢 (手動) ---
st.divider()
with st.expander("🔍 手動查詢特定時段"):
    c1, c2 = st.columns(2)
    with c1:
        sd = st.date_input("開始日期", value=now.date())
        st_t = st.time_input("開始時間", value=now.time())
    with c2:
        ed = st.date_input("結束日期", value=(now + timedelta(days=1)).date())
        et_t = st.time_input("結束時間", value=(now + timedelta(days=1)).time())
    
    if st.button("🚀 開始手動查詢", type="primary", use_container_width=True):
        with st.spinner("手動查詢中..."):
            manual_df = run_scraper_logic(datetime.combine(sd, st_t), datetime.combine(ed, et_t), "手動查詢")
            if not manual_df.empty:
                st.write("📋 查詢結果：")
                st.dataframe(manual_df, use_container_width=True, hide_index=True)
            else:
                st.error("查無資料或網站回應異常。")

# --- 7. 自動重整機制 ---
# 確保頁面開著也會每分鐘自動檢查一次是否需要更新
if 'last_check' not in st.session_state:
    st.session_state.last_check = time.time()

if time.time() - st.session_state.last_check > 60:
    st.session_state.last_check = time.time()
    st.rerun()
