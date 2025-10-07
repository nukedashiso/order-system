# app.py
import streamlit as st
import pandas as pd
from pathlib import Path
from PIL import Image
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from openpyxl import load_workbook
import io  # for in-memory Excel download

# ===== 全域設定 =====
st.set_page_config(page_title="線上點餐系統（兩店版｜純圖片）", page_icon="🍱", layout="wide")
TZ = ZoneInfo("Asia/Taipei")

# 兩家店設定（可自行調整）
SHOPS = {
    "food": {
        "label": "餐點店",
        "cutoff_hhmm": "11:00",
        "excel_path": "./exports/food_orders.xlsx",  # 可留空 "" 表示不寫入實體 Excel
        "orders_ws": "Orders",
        "summary_ws": "Summary",
    },
    "drink": {
        "label": "飲料店",
        "cutoff_hhmm": "14:30",
        "excel_path": "./exports/drink_orders.xlsx",
        "orders_ws": "Orders",
        "summary_ws": "Summary",
    },
}

# 路徑
BASE = Path(".")
DATA_DIR = BASE / "data"
IMG_BASE = BASE / "images" / "shops"    # images/shops/<shop_key>/
DATA_DIR.mkdir(parents=True, exist_ok=True)
IMG_BASE.mkdir(parents=True, exist_ok=True)
for key in SHOPS:
    (IMG_BASE / key).mkdir(parents=True, exist_ok=True)
# exports 目錄（如有設定 excel_path）
for cfg in SHOPS.values():
    p = cfg.get("excel_path", "")
    if p:
        Path(p).parent.mkdir(parents=True, exist_ok=True)

# 本地 CSV
ORDERS_CSV = DATA_DIR / "orders.csv"          # 欄位: order_id, shop_key, user_name, note, created_at, is_paid
ORDER_ITEMS_CSV = DATA_DIR / "order_items.csv"  # 欄位: order_id, shop_key, item_name, qty, unit_price

def init_csv(path: Path, columns: list):
    if not path.exists():
        pd.DataFrame(columns=columns).to_csv(path, index=False)

init_csv(ORDERS_CSV, ["order_id","shop_key","user_name","note","created_at","is_paid"])
init_csv(ORDER_ITEMS_CSV, ["order_id","shop_key","item_name","qty","unit_price"])

def load_orders():
    if ORDERS_CSV.exists():
        return pd.read_csv(ORDERS_CSV, dtype=str)
    return pd.DataFrame(columns=["order_id","shop_key","user_name","note","created_at","is_paid"])

def load_order_items():
    if ORDER_ITEMS_CSV.exists():
        df = pd.read_csv(ORDER_ITEMS_CSV, dtype=str).fillna("")
        if "qty" in df.columns:
            df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0).astype(int)
        if "unit_price" in df.columns:
            df["unit_price"] = pd.to_numeric(df["unit_price"], errors="coerce").fillna(0.0)
        return df
    return pd.DataFrame(columns=["order_id","shop_key","item_name","qty","unit_price"])

def save_orders(df): df.to_csv(ORDERS_CSV, index=False)
def save_order_items(df): df.to_csv(ORDER_ITEMS_CSV, index=False)

# ===== 工具：截單 =====
def cutoff_state(hhmm: str):
    now = datetime.now(TZ)
    try:
        hh, mm = map(int, (hhmm or "11:00").split(":"))
    except:
        hh, mm = 11, 0
    cutoff = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now >= cutoff:
        return True, f"已截單（今日 {cutoff.strftime('%H:%M')}）"
    left = cutoff - now
    m = int(left.total_seconds() // 60); s = int(left.total_seconds() % 60)
    return False, f"距離截單剩餘 {m} 分 {s} 秒（今日 {cutoff.strftime('%H:%M')}）"

# ===== Excel I/O =====
def excel_append_order(excel_path: str, worksheet: str, row_values: list):
    """
    逐筆附加：時間, 訂單ID, 店別, 姓名, 明細(字串), 總額, 備註, 已收款
    """
    if not excel_path:
        return True, "excel_path 未設定，略過寫入"
    p = Path(excel_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    headers = ["時間","訂單ID","店別","姓名","明細","總額","備註","已收款"]

    if not p.exists():
        df = pd.DataFrame([row_values], columns=headers)
        with pd.ExcelWriter(p, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name=worksheet or "Orders", index=False)
        return True, "OK"

    try:
        wb = load_workbook(p)
        wsname = worksheet or "Orders"
        ws = wb[wsname] if wsname in wb.sheetnames else wb.create_sheet(wsname)
        # 若是新表，補表頭
        if ws.max_row == 1 and ws.max_column == 1 and ws["A1"].value is None:
            ws.append(headers)
        ws.append(row_values)
        wb.save(p)
        return True, "OK"
    except Exception as e:
        return False, str(e)

def excel_upsert_summary(excel_path: str, worksheet: str, df: pd.DataFrame):
    """
    覆蓋寫入 Summary：品項, 單價, 數量, 金額
    """
    if not excel_path:
        return True, "excel_path 未設定，略過寫入"
    p = Path(excel_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = "a" if p.exists() else "w"
        with pd.ExcelWriter(
            p, engine="openpyxl", mode=mode,
            if_sheet_exists=("replace" if mode=="a" else None)
        ) as writer:
            out = df.rename(columns={"item_name":"品項","unit_price":"單價","total_qty":"數量","amount":"金額"})
            out.to_excel(writer, sheet_name=worksheet or "Summary", index=False)
        return True, "OK"
    except Exception as e:
        return False, str(e)

# ===== 側邊欄：店家切換 & 上傳圖片 =====
st.sidebar.title("🍽️ 線上點




