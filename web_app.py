import streamlit as st
import pandas as pd
import asyncio
from playwright.async_api import async_playwright
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, time as dt_time
import time

# --- 1. 基礎設定與時間處理 ---
st.set_page_config(page_title="花蓮港船舶即時查詢 (Playwright)", layout="wide")

def get_taiwan_time():
    return (datetime.utcnow() + timedelta(hours=8)).replace(second=0, microsecond=0)

def split_date_range(start, end):
    segments = []
    curr_start = start
    while curr_start < end:
        curr_end = min(curr_start + timedelta(days=7), end)
        segments.append((curr_start, curr_end))
        curr_start = curr_end + timedelta(seconds=1)
    return segments

# --- 2. 初始化 Session State ---
if 'trigger_search' not in st.session_state:
    st.session_state.trigger_search = False 
if 'expander_state' not in st.session_state:
    st.session_state.expander_state = False 

# --- 3. UI 連動回調 ---
def on_ui_change():
    now = get_taiwan_time()
    opt = st.session_state.ui_option
    sd, st_val = now.date(), now.time()
    ed, et_val = now.date(), now.time()

    if opt == "未來 24H":
        f = now + timedelta(hours=24); ed, et_val = f.date(), f.time()
    elif opt == "未來 3 日":
        f = now + timedelta(hours=72); ed, et_val = f.date(), f.time()
    elif opt == "前 7 日":
        p = now - timedelta(days=7); sd, st_val = p.date(), dt_time(0, 0)
    elif opt == "本月整月":
        first_day = now.replace(day=1, hour=0, minute=0)
        sd, st_val = first_day.date(), first_day.time()

    st.session_state.sd_key, st.session_state.st_key = sd, st_val
    st.session_state.ed_key, st.session_state.et_key = ed, et_val
    st.session_state.trigger_search = True
    st.session_state.expander_state = False

# --- 4. 核心爬蟲函數 (Playwright 非同步版) ---
async def run_playwright_scraper(start_time, end_time, step_text=""):
    download_dir = os.path.join(os.getcwd(), "temp_downloads")
    if not os.path.exists(download_dir): os.makedirs(download_dir)
    for f in os.listdir(download_dir):
        try: os.remove(os.path.join(download_dir, f))
        except: pass

    parsed_data = []
    with st.status(f"🚢 查詢中 (Playwright) {step_text}...", expanded=True) as status:
        async with async_playwright() as p:
            # 針對 Pi Zero 2W 優化啟動參數
            browser = await p.chromium.launch(headless=True, args=['--disable-gpu', '--single-process'])
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()
            
            try:
                await page.goto("https://tpnet.twport.com.tw/IFAWeb/Function?_RedirUrl=/IFAWeb/Reports/HistoryPortShipList", wait_until="networkidle")
                
                # 處理 iframe (與 Selenium 的 switch_to.frame(0) 對應)
                frame = page.frame(index=0) if page.frames else page
                
                # 點擊花蓮港
                await frame.get_by_text("花蓮港").click()

                # 填寫時間
                v_s, v_e = start_time.strftime("%Y/%m/%d %H:%M"), end_time.strftime("%Y/%m/%d %H:%M")
                status.write(f"📝 填寫區間: {v_s} ~ {v_e}")
                
                # 選取輸入框並寫入值 (利用 evaluate 模擬事件觸發)
                inputs = await frame.query_selector_all("input")
                d_inps = [inp for inp in inputs if (await inp.get_attribute("value") or "").startswith("20")]
                
                if len(d_inps) >= 2:
                    await d_inps[0].evaluate(f'(el, v) => {{ el.value = v; el.dispatchEvent(new Event("change")); }}', v_s)
                    await d_inps[1].evaluate(f'(el, v) => {{ el.value = v; el.dispatchEvent(new Event("change")); }}', v_e)

                # 取消所有勾選的 Checkbox
                for cb in await frame.query_selector_all("input[type='checkbox']:checked"):
                    await cb.click()

                # 排序與查詢
                try: await frame.select_option("select", index=1)
                except: pass
                
                await frame.get_by_role("button", name=re.compile(r"Query|查詢")).click()
                
                # 等待並下載 XML
                async with page.expect_download(timeout=10000) as download_info:
                    await frame.get_by_text("XML").first.click()
                
                download = await download_info.value
                xml_path = os.path.join(download_dir, download.suggested_filename)
                await download.save_as(xml_path)

                # 解析內容 (保持你的 Big5 邏輯)
                with open(xml_path, 'r', encoding='big5', errors='replace') as f:
                    content = f.read().replace('encoding="BIG5"', '').replace('encoding="big5"', '')
                
                root = ET.fromstring(content)
                for ship in root.findall('SHIP'):
                    gt_n = ship.find('GROSS_TOA')
                    gt = int(round(float(gt_n.text))) if gt_n is not None and gt_n.text else 0
                    if gt < 500 : continue

                    w_n = ship.find('WHARF_CODE')
                    raw_w = w_n.text if w_n is not None else ""
                    w_label = f"{int(re.search(r'(\d+)', raw_w).group(1)):02d}號" if raw_w and re.search(r'(\d+)', raw_w) else raw_w

                    raw_t = ship.find('PILOT_EXP_TM').text if ship.find('PILOT_EXP_TM') is not None else ""
                    d_s, t_s = "未排定", "未排定"
                    if len(raw_t) >= 12: d_s, t_s = f"{raw_t[4:6]}/{raw_t[6:8]}", f"{raw_t[8:10]}:{raw_t[10:12]}"

                    parsed_data.append({
                        "日期": d_s, "時間": t_s, "狀態": ship.find('SP_STS').text if ship.find('SP_STS') is not None else "",
                        "碼頭": w_label, "中文船名": ship.find('VESSEL_CNAME').text or "",
                        "長度(m)": int(round(float(ship.find('LOA').text))) if ship.find('LOA') is not None else 0,
                        "英文船名": ship.find('VESSEL_ENAME').text if ship.find('VESSEL_ENAME') is not None else "",
                        "總噸位": gt, "前一港": ship.find('BEFORE_PORT').text if ship.find('BEFORE_PORT') is not None else "",
                        "下一港": ship.find('NEXT_PORT').text if ship.find('NEXT_PORT') is not None else "",
                        "代理行": (ship.find('PBG_NAME').text or "")[:2]
                    })
                status.update(label="✅ 查詢完成", state="complete", expanded=False)
            except Exception as e:
                st.error(f"❌ 爬蟲發生錯誤: {e}")
            finally:
                await browser.close()
    return pd.DataFrame(parsed_data)

# --- 5. 快取與執行邏輯 (封裝 async) ---
@st.cache_data(ttl=1200, show_spinner=False)
def get_shared_24h_data():
    now_tw = get_taiwan_time()
    f24 = now_tw + timedelta(hours=24)
    # 在 Streamlit 中同步運行 async 函數的寫法
    df = asyncio.run(run_playwright_scraper(now_tw, f24, "(全域同步)"))
    if not df.empty:
        return df.drop_duplicates().sort_values(by=["日期", "時間"]), get_taiwan_time()
    return None, None

# --- 6. UI 介面佈局 ---
st.markdown("### 🚢 花蓮港船舶動態查詢 (Playwright 版)")

st.radio("⏱️ **預設顯示未來24H動態 (每20分鐘自動更新)**", 
         ["未來 24H", "未來 3 日", "前 7 日", "本月整月"], 
         key="ui_option", on_change=on_ui_change, horizontal=True)

with st.expander("更改查詢時段", expanded=st.session_state.expander_state):
    c1, c2 = st.columns(2)
    with c1:
        sd_in = st.date_input("開始日期", key="sd_key", value=get_taiwan_time().date())
        st_in = st.time_input("開始時間", key="st_key", value=get_taiwan_time().time(), label_visibility="collapsed")
    with c2:
        ed_in = st.date_input("結束日期", key="ed_key", value=(get_taiwan_time()+timedelta(hours=24)).date())
        et_in = st.time_input("結束時間", key="et_key", value=(get_taiwan_time()+timedelta(hours=24)).time(), label_visibility="collapsed")
    if st.button("🚀 開始查詢", type="primary", use_container_width=True):
        st.session_state.trigger_search = True
        if st.session_state.ui_option != "未來 24H": st.cache_data.clear()

# --- 7. 主邏輯執行 ---
if st.session_state.ui_option == "未來 24H" and not st.session_state.trigger_search:
    shared_df, update_time = get_shared_24h_data()
    if shared_df is not None:
        st.success(f"⚡ 全域資料更新時間: {update_time.strftime('%H:%M')}")
        st.dataframe(shared_df, use_container_width=True, hide_index=True)
        st.stop()

if st.session_state.trigger_search:
    st.session_state.trigger_search = False
    start_dt = datetime.combine(st.session_state.sd_key, st.session_state.st_key)
    end_dt = datetime.combine(st.session_state.ed_key, st.session_state.et_key)
    
    segments = split_date_range(start_dt, end_dt)
    all_dfs = []
    for i, (s, e) in enumerate(segments):
        df_seg = asyncio.run(run_playwright_scraper(s, e, f"({i+1}/{len(segments)})"))
        if not df_seg.empty: all_dfs.append(df_seg)
    
    if all_dfs:
        final_df = pd.concat(all_dfs).drop_duplicates().sort_values(by=["日期", "時間"])
        st.dataframe(final_df, use_container_width=True, hide_index=True)
        st.download_button("📥 下載報表", final_df.to_csv(index=False).encode('utf-8-sig'), "Report.csv", use_container_width=True)
    else:
        st.warning("⚠️ 該區間查無資料。")
