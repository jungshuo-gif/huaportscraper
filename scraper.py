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
from datetime import datetime, timedelta

def get_taiwan_time():
    """取得當前台灣時間"""
    return (datetime.utcnow() + timedelta(hours=8)).replace(second=0, microsecond=0)

def run_scraper():
    """執行爬蟲並儲存資料"""
    now_tw = get_taiwan_time()
    end_tw = now_tw + timedelta(hours=24)
    
    download_dir = os.path.join(os.getcwd(), "temp_downloads")
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)
    
    # 清理舊檔案
    for f in os.listdir(download_dir):
        try:
            os.remove(os.path.join(download_dir, f))
        except:
            pass
    
    driver = None
    try:
        print(f"🚢 開始查詢: {now_tw.strftime('%Y/%m/%d %H:%M')} ~ {end_tw.strftime('%Y/%m/%d %H:%M')}")
        
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
        
        if driver.find_elements(By.TAG_NAME, "iframe"):
            driver.switch_to.frame(0)
        
        # 點選花蓮港
        h_tab = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(),'花蓮港')]")))
        driver.execute_script("arguments[0].click();", h_tab)
        
        # 填寫時間區間
        v_s = now_tw.strftime("%Y/%m/%d %H:%M")
        v_e = end_tw.strftime("%Y/%m/%d %H:%M")
        
        inps = driver.find_elements(By.TAG_NAME, "input")
        d_inps = [i for i in inps if i.get_attribute("value") and i.get_attribute("value").startswith("20")]
        
        if len(d_inps) >= 2:
            driver.execute_script(f"arguments[0].value = '{v_s}'; arguments[0].dispatchEvent(new Event('change'));", d_inps[0])
            driver.execute_script(f"arguments[1].value = '{v_e}'; arguments[1].dispatchEvent(new Event('change'));", d_inps[1])
        
        # 取消勾選
        checked_boxes = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']:checked")
        for cb in checked_boxes:
            driver.execute_script("arguments[0].click();", cb)
        
        # 排序
        try:
            sort_sel = driver.find_element(By.XPATH, "//*[contains(text(),'Ordering by')]/following::select[1]")
            Select(sort_sel).select_by_index(1)
        except:
            pass
        
        # 查詢
        btn = driver.find_element(By.XPATH, "//*[contains(@value,'Query') or contains(@value,'查詢')]")
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(4)
        
        # 下載 XML
        xml_btns = driver.find_elements(By.XPATH, "//*[contains(text(), 'XML') or contains(@value, 'XML')]")
        if xml_btns:
            driver.execute_script("arguments[0].click();", xml_btns[0])
        
        # 等待下載完成
        downloaded_file = None
        for _ in range(15):
            time.sleep(1)
            xml_fs = [os.path.join(download_dir, f) for f in os.listdir(download_dir) if f.lower().endswith('.xml')]
            if xml_fs:
                downloaded_file = max(xml_fs, key=os.path.getmtime)
                break
        
        if not downloaded_file:
            print("❌ 未找到下載的 XML 檔案")
            return
        
        # 解析 XML
        with open(downloaded_file, 'r', encoding='big5', errors='replace') as f:
            content = f.read().replace('encoding="BIG5"', '').replace('encoding="big5"', '')
        
        root = ET.fromstring(content)
        parsed = []
        
        for ship in root.findall('SHIP'):
            gt_n = ship.find('GROSS_TOA')
            gt = int(round(float(gt_n.text))) if gt_n is not None and gt_n.text else 0
            if gt < 500:
                continue
            
            w_n = ship.find('WHARF_CODE')
            raw_w = w_n.text if w_n is not None else ""
            w_label = f"{int(re.search(r'(\d+)', raw_w).group(1)):02d}號" if raw_w and re.search(r'(\d+)', raw_w) else raw_w
            
            t_n = ship.find('PILOT_EXP_TM')
            raw_t = t_n.text if t_n is not None else ""
            d_s, t_s = "未排定", "未排定"
            if len(raw_t) >= 12:
                d_s, t_s = f"{raw_t[4:6]}/{raw_t[6:8]}", f"{raw_t[8:10]}:{raw_t[10:12]}"
            
            parsed.append({
                "日期": d_s,
                "時間": t_s,
                "狀態": ship.find('SP_STS').text if ship.find('SP_STS') is not None else "",
                "碼頭": w_label,
                "中文船名": ship.find('VESSEL_CNAME').text or "",
                "長度(m)": int(round(float(ship.find('LOA').text))) if ship.find('LOA') is not None else 0,
                "英文船名": ship.find('VESSEL_ENAME').text if ship.find('VESSEL_ENAME') is not None else "",
                "總噸位": gt,
                "前一港": ship.find('BEFORE_PORT').text if ship.find('BEFORE_PORT') is not None else "",
                "下一港": ship.find('NEXT_PORT').text if ship.find('NEXT_PORT') is not None else "",
                "代理行": (ship.find('PBG_NAME').text or "")[:2]
            })
        
        df = pd.DataFrame(parsed)
        
        if not df.empty:
            df = df.drop_duplicates().sort_values(by=["日期", "時間"])
            
            # 加入更新時間資訊
            update_time = now_tw.strftime('%Y-%m-%d %H:%M')
            
            # 儲存為 CSV (包含更新時間在第一行)
            with open('port_data_cache.csv', 'w', encoding='utf-8-sig') as f:
                f.write(f"# 更新時間: {update_time}\n")
                df.to_csv(f, index=False)
            
            print(f"✅ 成功儲存 {len(df)} 筆資料，更新時間: {update_time}")
        else:
            print("⚠️ 查無資料")
    
    except Exception as e:
        print(f"❌ 執行錯誤: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    run_scraper()
