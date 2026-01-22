import streamlit as st
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType
import time
import re
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

# --- 1. 基礎設定 ---
st.set_page_config(page_title="花蓮港船舶即時查詢", layout="wide")
TW_TZ = ZoneInfo("Asia/Taipei")

def get_taiwan_time():
    """取得台灣時間（timezone-aware）"""
    return datetime.now(TW_TZ).replace(second=0, microsecond=0)

def split_date_range(start, end):
    segments = []
    curr = start
    while curr < end:
        seg_end = min(curr + timedelta(days=7), end)
        segments.append((curr, seg_end))
        curr = seg_end + timedelta(seconds=1)
    return segments

# --- 2. Session State ---
if "trigger_search" not in st.session_state:
    st.session_state.trigger_search = False
if "expander_state" not in st.session_state:
    st.session_state.expander_state = False

# --- 3. UI 回調 ---
def on_ui_change():
    now = get_taiwan_time()
    opt = st.session_state.ui_option

    sd, stv = now.date(), now.time()
    ed, etv = now.date(), now.time()

    if opt == "未來 24H":
        f = now + timedelta(hours=24)
        ed, etv = f.date(), f.time()
    elif opt == "未來 3 日":
        f = now + timedelta(hours=72)
        ed, etv = f.date(), f.time()
    elif opt == "前 7 日":
        p = now - timedelta(days=7)
        sd, stv = p.date(), dt_time(0, 0)
    elif opt == "本月整月":
        first = now.replace(day=1, hour=0, minute=0)
        sd, stv = first.date(), first.time()

    st.session_state.sd_key = sd
    st.session_state.st_key = stv
    st.session_state.ed_key = ed
    st.session_state.et_key = etv
    st.session_state.expander_state = False
    st.session_state.trigger_search = True

# --- 4. 核心爬蟲 ---
def run_scraper_segment(start_time, end_time, step_text="", silent=False):
    download_dir = os.path.join(os.getcwd(), "temp_downloads")
    os.makedirs(download_dir, exist_ok=True)
    for f in os.listdir(download_dir):
        try:
            os.remove(os.path.join(download_dir, f))
        except:
            pass

    driver = None
    status = None if silent else st.status(f"🚢 查詢中 {step_text}", expanded=True)

    try:
        if status:
            status.__enter__()

        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_experimental_option("prefs", {"download.default_directory": download_dir})
        service = Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())
        driver = webdriver.Chrome(service=service, options=options)

        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": download_dir},
        )

        driver.get("https://tpnet.twport.com.tw/IFAWeb/Function?_RedirUrl=/IFAWeb/Reports/HistoryPortShipList")
        wait = WebDriverWait(driver, 20)

        if driver.find_elements(By.TAG_NAME, "iframe"):
            driver.switch_to.frame(0)

        h_tab = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(),'花蓮港')]")))
        driver.execute_script("arguments[0].click();", h_tab)

        v_s = start_time.strftime("%Y/%m/%d %H:%M")
        v_e = end_time.strftime("%Y/%m/%d %H:%M")

        inputs = [i for i in driver.find_elements(By.TAG_NAME, "input")
                  if i.get_attribute("value") and i.get_attribute("value").startswith("20")]

        if len(inputs) >= 2:
            driver.execute_script(
                f"arguments[0].value='{v_s}';arguments[0].dispatchEvent(new Event('change'));",
                inputs[0],
            )
            driver.execute_script(
                f"arguments[0].value='{v_e}';arguments[0].dispatchEvent(new Event('change'));",
                inputs[1],
            )

        for cb in driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']:checked"):
            driver.execute_script("arguments[0].click();", cb)

        try:
            sel = driver.find_element(By.XPATH, "//*[contains(text(),'Ordering by')]/following::select[1]")
            Select(sel).select_by_index(1)
        except:
            pass

        q_btn = driver.find_element(By.XPATH, "//*[contains(@value,'Query') or contains(@value,'查詢')]")
        driver.execute_script("arguments[0].click();", q_btn)
        time.sleep(4)

        xml_btn = driver.find_elements(By.XPATH, "//*[contains(text(),'XML') or contains(@value,'XML')]")
        if xml_btn:
            driver.execute_script("arguments[0].click();", xml_btn[0])

        xml_file = None
        for _ in range(15):
            time.sleep(1)
            files = [f for f in os.listdir(download_dir) if f.lower().endswith(".xml")]
            if files:
                xml_file = os.path.join(download_dir, files[0])
                break

        if not xml_file:
            return pd.DataFrame()

        with open(xml_file, "r", encoding="big5", errors="replace") as f:
            content = f.read().replace('encoding="BIG5"', '').replace('encoding="big5"', '')

        root = ET.fromstring(content)
        rows = []

        for ship in root.findall("SHIP"):
            gt = int(round(float(ship.findtext("GROSS_TOA", "0"))))
            if gt < 500:
                continue

            raw_w = ship.findtext("WHARF_CODE", "")
            m = re.search(r"(\d+)", raw_w)
            wharf = f"{int(m.group(1)):02d}號" if m else raw_w

            raw_t = ship.findtext("PILOT_EXP_TM", "")
            d_s, t_s = "未排定", "未排定"
            if len(raw_t) >= 12:
                d_s = f"{raw_t[4:6]}/{raw_t[6:8]}"
                t_s = f"{raw_t[8:10]}:{raw_t[10:12]}"

            rows.append({
                "日期": d_s,
                "時間": t_s,
                "狀態": ship.findtext("SP_STS", ""),
                "碼頭": wharf,
                "中文船名": ship.findtext("VESSEL_CNAME", ""),
                "長度(m)": int(round(float(ship.findtext("LOA", "0")))),
                "英文船名": ship.findtext("VESSEL_ENAME", ""),
                "總噸位": gt,
                "前一港": ship.findtext("BEFORE_PORT", ""),
                "下一港": ship.findtext("NEXT_PORT", ""),
                "代理行": ship.findtext("PBG_NAME", "")[:2],
            })

        return pd.DataFrame(rows)

    finally:
        if driver:
            driver.quit()
        if status:
            status.__exit__(None, None, None)

# --- 5. 全域快取（台灣時間） ---
@st.cache_data(ttl=3600, show_spinner=False)
def get_shared_24h_data():
    now = get_taiwan_time()
    df = run_scraper_segment(now, now + timedelta(hours=24), silent=True)
    if not df.empty:
        cols = ["日期","時間","狀態","碼頭","中文船名","長度(m)","英文船名","總噸位","前一港","下一港","代理行"]
        return df[cols].drop_duplicates().sort_values(["日期","時間"]), now
    return None, None

# --- 6. UI ---
st.markdown("<h3>🚢 花蓮港船舶動態查詢</h3>", unsafe_allow_html=True)

now_init = get_taiwan_time()
f24_init = now_init + timedelta(hours=24)

st.radio(
    "⏱️ 快速選擇時段",
    ["未來 24H","未來 3 日","前 7 日","本月整月"],
    key="ui_option",
    on_change=on_ui_change,
    horizontal=True,
)

with st.expander("更改查詢時段", expanded=st.session_state.expander_state):
    c1, c2 = st.columns(2)
    with c1:
        sd = st.date_input("開始日期", key="sd_key", value=now_init.date())
        stt = st.time_input("開始時間", key="st_key", value=now_init.time())
    with c2:
        ed = st.date_input("結束日期", key="ed_key", value=f24_init.date())
        ett = st.time_input("結束時間", key="et_key", value=f24_init.time())

start_dt = datetime.combine(sd, stt, tzinfo=TW_TZ)
end_dt = datetime.combine(ed, ett, tzinfo=TW_TZ)

if st.button("🚀 開始查詢", type="primary", use_container_width=True):
    st.session_state.trigger_search = True
    if st.session_state.ui_option != "未來 24H":
        st.cache_data.clear()

# --- 7. 顯示 ---
if st.session_state.ui_option == "未來 24H" and not st.session_state.trigger_search:
    df, ut = get_shared_24h_data()
    if df is not None:
        diff_min = (get_taiwan_time() - ut).total_seconds() / 60
        st.success(f"🟢 更新時間（台灣）：{ut.strftime('%H:%M')}｜{int(diff_min)} 分鐘前")
        st.dataframe(df, use_container_width=True, hide_index=True)
    st.stop()
