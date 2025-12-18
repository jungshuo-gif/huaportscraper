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

# --- 1. 網頁基礎設定 ---
st.set_page_config(page_title="花蓮港船舶即時查詢", layout="wide")

def get_taiwan_time():
    return datetime.utcnow() + timedelta(hours=8)

# --- 2. 初始化 Session State ---
if 'last_option' not in st.session_state:
    st.session_state.last_option = "未來 24H"
if 'trigger_search' not in st.session_state:
    st.session_state.trigger_search = False

# --- 3. 核心爬蟲函數 (精確恢復 V7 邏輯) ---
def run_scraper(start_time, end_time):
    download_dir = os.path.join(os.getcwd(), "temp_downloads")
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)
    
    # 清理舊檔
    for f in os.listdir(download_dir):
        try: os.remove(os.path.join(download_dir, f))
        except: pass

    with st.status("🚢 正在連線花蓮港務系統...", expanded=True) as status:
        try:
            options = webdriver.ChromeOptions()
            options.add_argument("--headless=new") 
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1920,1080")
            options.add_experimental_option("prefs", {
                "download.default_directory": download_dir,
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True
            })
            
            service = Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())
            driver = webdriver.Chrome(service=service, options=options)
            driver.execute_cdp_cmd('Page.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': download_dir})
            
            status.write("🔗 連線中...")
            driver.get("https://tpnet.twport.com.tw/IFAWeb/Function?_RedirUrl=/IFAWeb/Reports/HistoryPortShipList")
            
            wait = WebDriverWait(driver, 20)
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            if iframes: driver.switch_to.frame(0)
            
            # --- V7 核心：選取花蓮港 ---
            try:
                hualien_tab = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(),'花蓮港')]")))
                driver.execute_script("arguments[0].click();", hualien_tab)
                time.sleep(1)
            except: pass

            # --- V7 核心：填入日期 ---
            val_start = start_time.strftime("%Y/%m/%d %H:%M")
            val_end = end_time.strftime("%Y/%m/%d %H:%M")
            status.write(f"📝 填寫區間: {val_start} ~ {val_end}")
            
            all_inputs = driver.find_elements(By.TAG_NAME, "input")
            target_date_inputs = [inp for inp in all_inputs if inp.get_attribute("value") and inp.get_attribute("value").startswith("20")]
            
            if len(target_date_inputs) >= 2:
                driver.execute_script(f"arguments[0].value = '{val_start}'; arguments[0].dispatchEvent(new Event('change'));", target_date_inputs[0])
                driver.execute_script(f"arguments[0].value = '{val_end}'; arguments[0].dispatchEvent(new Event('change'));", target_date_inputs[1])
            
            # --- V7 核心：排序與查詢 ---
            try:
                sort_select = driver.find_element(By.XPATH, "//*[contains(text(),'Ordering by')]/following::select[1]")
                Select(sort_select).select_by_index(1)
            except: pass
            
            query_btn = driver.find_element(By.XPATH, "//*[contains(@value,'Query') or contains(@value,'查詢')]")
            driver.execute_script("arguments[0].click();", query_btn)
            time.sleep(4)
            
            # --- V7 核心：下載 XML ---
            status.write("📥 嘗試下載 XML...")
            btns = driver.find_elements(By.XPATH, "//*[contains(text(), 'XML') or contains(@value, 'XML')]")
            if btns:
                driver.execute_script("arguments[0].click();", btns[0])
            
            # 等待下載
            downloaded_file = None
            for _ in range(20):
                time.sleep(1)
                xml_files = [f for f in os.listdir(download_dir) if f.lower().endswith('.xml')]
                if xml_files:
                    downloaded_file = os.path.join(download_dir, xml_files[0])
                    break
            
            if not downloaded_file: raise Exception("下載逾時，未找到 XML 檔案")

            # --- V7 核心：解析 (加入安全防護) ---
            status.write("⚙️ 解析資料...")
            with open(downloaded_file, 'r', encoding='big5', errors='replace') as f:
                xml_content = f.read().replace('encoding="BIG5"', '').replace('encoding="big5"', '')
            
            root = ET.fromstring(xml_content)
            parsed_data = []
            
            for ship in root.findall('SHIP'):
                # 總噸位過濾 (安全讀取)
                gt_text = ship.find('GROSS_TOA').text if ship.find('GROSS_TOA') is not None else "0"
                try: gt = int(round(float(gt_text)))
                except: gt = 0
                if gt < 500: continue

                # 碼頭安全解析 (解決 NoneType 報錯)
                raw_wharf = ship.find('WHARF_CODE').text if ship.find('WHARF_CODE') is not None else ""
                wharf_display = raw_wharf
                if raw_wharf:
                    match = re.search(r'(\d+)', raw_wharf)
                    if match:
                        wharf_display = f"{int(match.group(1)):02d}號碼頭"

                # 時間安全解析
                raw_tm = ship.find('PILOT_EXP_TM').text if ship.find('PILOT_EXP_TM') is not None else ""
                d_disp, t_disp = "未知", "未知"
                if len(raw_tm) >= 12:
                    d_disp = f"{raw_tm[4:6]}/{raw_tm[6:8]}"
                    t_disp = f"{raw_tm[8:10]}:{raw_tm[10:12]}"

                # 代理行簡化
                raw_agent = ship.find('PBG_NAME').text if ship.find('PBG_NAME') is not None else ""
                agent_name = raw_agent[:2]
                if "台灣船運" in raw_agent: agent_name = "台船"

                parsed_data.append({
                    "日期": d_disp, "時間": t_disp, "狀態": ship.find('SP_STS').text if ship.find('SP_STS') is not None else "",
                    "碼頭": wharf_display, "中文船名": ship.find('VESSEL_CNAME').text if ship.find('VESSEL_CNAME') is not None else "",
                    "長度(m)": int(round(float(ship.find('LOA').text))) if ship.find('LOA') is not None else 0,
                    "總噸位": gt, "代理行": agent_name
                })

            driver.quit()
            status.update(label="✅ 查詢完成！", state="complete", expanded=False)
            return pd.DataFrame(parsed_data)

        except Exception as e:
            st.error(f"❌ 發生錯誤: {e}")
            if 'driver' in locals(): driver.quit()
            return None

# --- 4. UI 介面 ---
st.title("🚢 花蓮港船舶動態查詢")

option = st.radio(
    "⏱️ **快捷查詢區間 (點選後 2 秒自動執行)**",
    ["未來 24H", "未來 3 日", "前 7 日", "本月整月", "手動調整"],
    index=0, horizontal=True
)

# 日期連動邏輯
now = get_taiwan_time()
sd_v, st_v, ed_v, et_v = now.date(), now.time(), now.date(), now.time()

if option == "未來 24H":
    f = now + timedelta(hours=24); ed_v, et_v = f.date(), f.time()
elif option == "未來 3 日":
    f = now + timedelta(hours=72); ed_v, et_v = f.date(), f.time()
elif option == "前 7 日":
    p = now - timedelta(days=7); sd_v, st_v = p.date(), dt_time(0, 0)
elif option == "本月整月":
    sd_v = now.replace(day=1).date(); st_v = dt_time(0, 0)

with st.expander("📆 詳細時間確認", expanded=True):
    c1, c2 = st.columns(2)
    with c1:
        s_date = st.date_input("開始日期", value=sd_v)
        s_time = st.time_input("開始時間", value=st_v, label_visibility="collapsed")
    with c2:
        e_date = st.date_input("結束日期", value=ed_v)
        e_time = st.time_input("結束時間", value=et_v, label_visibility="collapsed")

start_dt = datetime.combine(s_date, s_time)
end_dt = datetime.combine(e_date, e_time)

# --- 5. 觸發與顯示 ---
if option != st.session_state.last_option:
    st.session_state.last_option = option
    if option != "手動調整":
        with st.info("⏳ 偵測到選項更換，2 秒後開始查詢..."):
            time.sleep(2)
            st.session_state.trigger_search = True
            st.rerun()

if st.button("🚀 開始查詢", type="primary", use_container_width=True):
    st.session_state.trigger_search = True

if st.session_state.trigger_search:
    st.session_state.trigger_search = False
    df = run_scraper(start_dt, end_dt)
    if df is not None and not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)
        csv = df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 下載報表", csv, f"Report_{now.strftime('%m%d')}.csv", use_container_width=True)
