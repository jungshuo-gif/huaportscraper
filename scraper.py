import os
import time
import re
import pandas as pd
import xml.etree.ElementTree as ET
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from datetime import datetime, timedelta

def get_taiwan_time():
    """取得當前台灣時間 (抹除秒數)"""
    return (datetime.utcnow() + timedelta(hours=8)).replace(second=0, microsecond=0)

def run_scraper():
    download_dir = os.path.join(os.getcwd(), "downloads")
    if not os.path.exists(download_dir): os.makedirs(download_dir)
    
    chrome_options = Options()
    chrome_options.add_argument("--headless") # 無頭模式
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    prefs = {"download.default_directory": download_dir}
    chrome_options.add_experimental_option("prefs", prefs)
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    driver.execute_cdp_cmd('Page.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': download_dir})

    try:
        driver.get("https://tpnet.twport.com.tw/IFAWeb/Function?_RedirUrl=/IFAWeb/Reports/HistoryPortShipList")
        time.sleep(5)
        if driver.find_elements(By.TAG_NAME, "iframe"): driver.switch_to.frame(0)
        
        h_tab = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(),'花蓮港')]")))
        driver.execute_script("arguments[0].click();", h_tab)

        v_s = get_taiwan_time().strftime("%Y/%m/%d %H:%M")
        v_e = (get_taiwan_time() + timedelta(hours=24)).strftime("%Y/%m/%d %H:%M")
        
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
        
        if downloaded_file:
            with open(downloaded_file, 'r', encoding='big5', errors='replace') as f:
                content = f.read().replace('encoding="BIG5"', '').replace('encoding="big5"', '')
            
            root = ET.fromstring(content)
            parsed = []
            for ship in root.findall('SHIP'):
                gt_n = ship.find('GROSS_TOA')
                gt = int(round(float(gt_n.text))) if gt_n is not None and gt_n.text else 0
                if gt < 500 : continue

                w_n = ship.find('WHARF_CODE')
                raw_w = w_n.text if w_n is not None else ""
                
                # --- 僅此處為語法修正，邏輯不變 ---
                match = re.search(r'(\d+)', raw_w) if raw_w else None
                if match:
                    w_label = "{:02d}號".format(int(match.group(1)))
                else:
                    w_label = raw_w
                # -------------------------------

                t_n = ship.find('PILOT_EXP_TM')
                raw_t = t_n.text if t_n is not None else ""
                d_s, t_s = "未排定", "未排定"
                if len(raw_t) >= 12: d_s, t_s = f"{raw_t[4:6]}/{raw_t[6:8]}", f"{raw_t[8:10]}:{raw_t[10:12]}"

                parsed.append({
                    "日期": d_s, "時間": t_s, "狀態": ship.find('SP_STS').text if ship.find('SP_STS') is not None else "",
                    "碼頭": w_label, "中文船名": ship.find('VESSEL_CNAME').text or "",
                    "長度(m)": int(round(float(ship.find('LOA').text))) if ship.find('LOA') is not None else 0,
                    "英文船名": ship.find('VESSEL_ENAME').text if ship.find('VESSEL_ENAME') is not None else "",
                    "總噸位": gt, "前一港": ship.find('BEFORE_PORT').text if ship.find('BEFORE_PORT') is not None else "",
                    "下一港": ship.find('NEXT_PORT').text if ship.find('NEXT_PORT') is not None else "",
                    "代理行": (ship.find('PBG_NAME').text or "")[:2]
                })
            
            df = pd.DataFrame(parsed)
            # 確保欄位順序與 Streamlit 端完全一致
            cols = ["日期", "時間", "狀態", "碼頭", "中文船名", "長度(m)", "英文船名", "總噸位", "前一港", "下一港", "代理行"]
            df = df[cols].drop_duplicates().sort_values(by=["日期", "時間"])
            df.to_csv("port_data_cache.csv", index=False, encoding='utf-8-sig')
    finally:
        if 'driver' in locals(): driver.quit()

if __name__ == "__main__":
    run_scraper()
