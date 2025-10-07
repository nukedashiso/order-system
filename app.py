# app.py
import streamlit as st
import pandas as pd
from pathlib import Path
from PIL import Image
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from openpyxl import load_workbook

# ===== 全域設定 =====
st.set_page_config(page_title="線上點餐系統（兩店版｜純圖片菜單）", page_icon="🍱", layout="wide")
TZ = ZoneInfo("Asia/Taipei")

# 兩家店設定（你可以調整）
SHOPS = {
    "food": {
        "label": "餐點店",
        "cutoff_hhmm": "18:00",
        "excel_path": "./exports/food_orders.xlsx",
        "orders_ws": "Orders",
        "summary_ws": "Summary",
    },
    "drink": {
        "label": "飲料店",
        "cutoff_hhmm": "18:00",
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

# 本地 CSV
ORDERS_CSV = DATA_DIR / "orders.csv"         # 欄位: order_id, shop_key, user_name, note, created_at, is_paid
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
        ws = wb[worksheet] if worksheet in wb.sheetnames else wb.create_sheet(worksheet or "Orders")
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
st.sidebar.title("🍽️ 線上點餐（兩店版｜純圖片）")
shop_labels = {cfg["label"]: key for key, cfg in SHOPS.items()}
chosen_label = st.sidebar.radio("選擇店家", list(shop_labels.keys()))
shop_key = shop_labels[chosen_label]
cfg = SHOPS[shop_key]

with st.sidebar.expander("菜單圖片維護（可多張）", expanded=False):
    files = st.file_uploader("上傳圖片（jpg/png，多選）", type=["jpg","jpeg","png"], accept_multiple_files=True, key=f"upl_{shop_key}")
    if files:
        target_dir = IMG_BASE / shop_key
        for f in files:
            Image.open(f).save(target_dir / f"{uuid.uuid4().hex}.png")
        st.success("圖片已上傳！重新整理即可看到。")

mode = st.sidebar.radio("模式 / Mode", ["前台點餐", "管理者模式"], key=f"mode_{shop_key}")

# ===== 前台 =====
if mode == "前台點餐":
    st.title(f"📋 {cfg['label']}｜線上點餐")
    passed, msg = cutoff_state(cfg["cutoff_hhmm"])
    st.info(msg)

    # 圖片菜單牆
    shop_img_dir = IMG_BASE / shop_key
    imgs = sorted([p for p in shop_img_dir.glob("*") if p.suffix.lower() in [".jpg",".jpeg",".png"]])
    if imgs:
        st.subheader("菜單")
        cols = st.columns(2)
        for i, p in enumerate(imgs):
            with cols[i % 2]:
                st.image(str(p), use_container_width=True)
        st.divider()
    else:
        st.warning("此店家尚未上傳菜單圖片。")

    # 自由列輸入
    st.subheader("填寫餐點")
    session_key = f"rows_{shop_key}"
    if session_key not in st.session_state:
        st.session_state[session_key] = [{"item_name":"","unit_price":0.0,"qty":0}]

    def add_row():
        st.session_state[session_key].append({"item_name":"","unit_price":0.0,"qty":0})

    def clear_rows():
        st.session_state[session_key] = [{"item_name":"","unit_price":0.0,"qty":0}]

    c1, c2, _ = st.columns([1,1,6])
    c1.button("新增一列", on_click=add_row, disabled=passed, use_container_width=True, key=f"add_{shop_key}")
    c2.button("清空", on_click=clear_rows, disabled=passed, use_container_width=True, key=f"clr_{shop_key}")

    total = 0
    with st.form(f"order_form_{shop_key}", clear_on_submit=False):
        rows = st.session_state[session_key]
        for i, r in enumerate(rows):
            a, b, c, d = st.columns([4,2,2,2])
            r["item_name"]  = a.text_input("品項名稱", r["item_name"], key=f"nm_{shop_key}_{i}", disabled=passed)
            r["unit_price"] = b.number_input("單價", min_value=0.0, step=1.0, value=float(r["unit_price"]), key=f"pr_{shop_key}_{i}", disabled=passed)
            r["qty"]        = c.number_input("數量", min_value=0,   step=1,   value=int(r["qty"]), key=f"qt_{shop_key}_{i}", disabled=passed)
            d.write(f"小計：${int(r['unit_price']*r['qty'])}")
            total += int(r["unit_price"]*r["qty"])

        st.markdown(f"### 總計：${total}")
        name = st.text_input("姓名/暱稱", "", disabled=passed, key=f"name_{shop_key}")
        note = st.text_input("備註（例如不要香菜）", "", disabled=passed, key=f"note_{shop_key}")
        submitted = st.form_submit_button("送出訂單", type="primary", use_container_width=True, disabled=passed)


    if submitted:
        if not name.strip():
            st.error("請輸入姓名/暱稱"); st.stop()
        valid_rows = [r for r in st.session_state[session_key] if r["item_name"].strip() and r["qty"]>0]
        if not valid_rows:
            st.error("請至少填一列有效餐點"); st.stop()

        # 寫入本地 CSV
        orders_df = load_orders()
        items_df  = load_order_items()
        oid = uuid.uuid4().hex[:12]
        now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

        new_order = pd.DataFrame([{
            "order_id": oid, "shop_key": shop_key, "user_name": name.strip(),
            "note": note.strip(), "created_at": now, "is_paid": "否"
        }])
        orders_df = pd.concat([orders_df, new_order], ignore_index=True)

        item_rows = []
        for r in valid_rows:
            item_rows.append({
                "order_id": oid, "shop_key": shop_key,
                "item_name": r["item_name"].strip(),
                "qty": int(r["qty"]),
                "unit_price": float(r["unit_price"])
            })
        items_df = pd.concat([items_df, pd.DataFrame(item_rows)], ignore_index=True)
        save_orders(orders_df); save_order_items(items_df)

        # 寫入 Excel（各店各自的檔案）
        excel_path = cfg["excel_path"]
        if excel_path:
            detail_str = "; ".join([f"{r['item_name']}x{int(r['qty'])}@{int(r['unit_price'])}" for r in item_rows])
            ok, info = excel_append_order(
                excel_path, cfg["orders_ws"],
                [now, oid, SHOPS[shop_key]["label"], name.strip(), detail_str, total, note.strip(), "否"]
            )
            if ok: st.caption(f"已寫入 Excel：{excel_path}（{cfg['orders_ws']}）")
            else:  st.warning(f"訂單已保存，但寫入 Excel 失敗：{info}")

        st.success(f"訂單送出成功！編號：{oid}")
        st.balloons()

# ===== 管理者模式 =====
else:
    st.title(f"🔧 管理者面板｜{cfg['label']}")

    orders = load_orders()
    items  = load_order_items()

    orders = orders[orders["shop_key"]==shop_key]
    items  = items[items["shop_key"]==shop_key]

    if orders.empty:
        st.info("此店家尚無訂單")
        st.stop()

    # 彙總（以 品項 + 單價 分組）
    agg = items.groupby(["item_name","unit_price"], as_index=False).agg(total_qty=("qty","sum"))
    agg["amount"] = (agg["total_qty"] * agg["unit_price"]).astype(int)

    st.subheader("品項彙總")
    st.dataframe(
        agg.rename(columns={"item_name":"品項","unit_price":"單價","total_qty":"數量","amount":"金額"}),
        use_container_width=True
    )
    st.markdown(f"**總金額：** ${int(agg['amount'].sum())}")

    # 訂單列表 + 切換收款
    order_total = items.groupby("order_id", as_index=False).apply(
        lambda df: pd.Series({"order_total": int((df["qty"]*df["unit_price"]).sum())})
    ).reset_index(drop=True)
    orders = orders.merge(order_total, on="order_id", how="left").fillna({"order_total":0})
    orders = orders.sort_values("created_at", ascending=False)

    st.subheader("訂單列表")
    for _, od in orders.iterrows():
        with st.container(border=True):
            st.markdown(f"**訂單 {od['order_id']}**｜{od['created_at']}｜{od['user_name']}｜金額 ${int(od['order_total'])}")
            if str(od["note"]).strip():
                st.caption(f"備註：{od['note']}")
            c1, c2 = st.columns([1,1])
            with c1:
                st.write(f"已收款：{od['is_paid']}")
            with c2:
                if st.button(f"切換收款（{od['order_id']}）", key=f"pay_{shop_key}_{od['order_id']}"):
                    all_orders = load_orders()
                    mask = (all_orders["order_id"] == od["order_id"])
                    cur = all_orders.loc[mask, "is_paid"].iloc[0]
                    all_orders.loc[mask, "is_paid"] = "否" if cur=="是" else "是"
                    save_orders(all_orders); st.success("已更新收款狀態"); st.rerun()

    st.divider()
    st.subheader("一鍵同步彙總 → Excel")
    st.caption("覆蓋寫入 Summary 工作表（只影響此店家設定的 Excel）。")
    if st.button("同步彙總", key=f"sum_{shop_key}"):
        excel_path = cfg["excel_path"]
        if not excel_path:
            st.warning("此店家未設定 excel_path")
        else:
            ok, info = excel_upsert_summary(excel_path, cfg["summary_ws"], agg)
            if ok: st.success(f"彙總已寫入：{excel_path}（{cfg['summary_ws']}）")
            else:  st.warning(f"寫入 Excel 失敗：{info}")
# ===== 一鍵同步彙總下載 =====

import base64
with open(EXCEL_PATH, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()
    href = f'<a href="data:application/octet-stream;base64,{b64}" download="orders.xlsx">⬇️ 下載 Excel 檔</a>'
    st.markdown(href, unsafe_allow_html=True)
