# app.py
import streamlit as st
import pandas as pd
from pathlib import Path
from PIL import Image
import uuid
import io
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import base64

GITHUB_REPO   = st.secrets.get("GITHUB_REPO")
GITHUB_BRANCH = st.secrets.get("GITHUB_BRANCH", "main")
GITHUB_TOKEN  = st.secrets.get("GITHUB_TOKEN")  # 私有/需要寫入時建議設定

def _gh_headers():
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers

def gh_read_csv(path_in_repo: str) -> pd.DataFrame:
    """從 GitHub raw 讀取 CSV。"""
    if not GITHUB_REPO:
        raise RuntimeError("GITHUB_REPO 未設定")
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{path_in_repo}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return pd.read_csv(io.BytesIO(r.content), dtype=str)

def gh_get_file_sha(path_in_repo: str):
    """取得檔案目前的 SHA（用於更新），不存在則回傳 None。"""
    if not GITHUB_REPO:
        return None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path_in_repo}?ref={GITHUB_BRANCH}"
    r = requests.get(url, headers=_gh_headers(), timeout=15)
    if r.status_code == 200:
        try:
            return r.json().get("sha")
        except Exception:
            return None
    return None

def gh_upsert_file(path_in_repo: str, content_bytes: bytes, message: str) -> None:
    """在 GitHub 專案中建立/更新檔案內容。"""
    if not (GITHUB_REPO and GITHUB_BRANCH and GITHUB_TOKEN):
        raise RuntimeError("缺少 GitHub 設定或 Token，無法寫入 GitHub。")
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path_in_repo}"
    sha = gh_get_file_sha(path_in_repo)
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=_gh_headers(), json=payload, timeout=15)
    r.raise_for_status()

# ========= 基本設定 =========
st.set_page_config(page_title="月會下午茶線上點餐", page_icon="🍱", layout="wide")
TZ = ZoneInfo("Asia/Taipei")

# 截單（可用 "18:00" 或 "2025/10/14, 18:00" / "2025-10-14, 18:00"）
CUTOFF = "2025/10/14 12:30"

# 工作表名稱（僅用於下載的 Excel 檔名/工作表名）
ORDERS_WS = "Orders"
SUMMARY_WS = "Summary"

BASE = Path(".")
DATA_DIR = BASE / "data"
IMG_DIR = BASE /  "menus"   # 放兩張菜單圖片
DATA_DIR.mkdir(parents=True, exist_ok=True)
IMG_DIR.mkdir(parents=True, exist_ok=True)

# ========= 讀寫：GitHub 優先，缺設定則退回本地 =========
ORDERS_PATH_IN_REPO = "data/orders.csv"
ORDER_ITEMS_PATH_IN_REPO = "data/order_items.csv"
LOCAL_ORDERS_CSV = DATA_DIR / "orders.csv"
LOCAL_ORDER_ITEMS_CSV = DATA_DIR / "order_items.csv"

def _github_configured() -> bool:
    return bool(GITHUB_REPO and GITHUB_BRANCH and GITHUB_TOKEN)

def load_orders():
    # 優先從 GitHub 讀取
    if GITHUB_REPO:
        try:
            return gh_read_csv(ORDERS_PATH_IN_REPO)
        except Exception:
            pass
    # 退回本地檔案
    if LOCAL_ORDERS_CSV.exists():
        return pd.read_csv(LOCAL_ORDERS_CSV, dtype=str)
    return pd.DataFrame(columns=["order_id","user_name","note","created_at","is_paid"])

def load_order_items():
    # 優先從 GitHub 讀取
    if GITHUB_REPO:
        try:
            df = gh_read_csv(ORDER_ITEMS_PATH_IN_REPO).fillna("")
            if "qty" in df.columns:
                df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0).astype(int)
            if "unit_price" in df.columns:
                df["unit_price"] = pd.to_numeric(df["unit_price"], errors="coerce").fillna(0.0)
            return df
        except Exception:
            pass
    # 退回本地檔案
    if LOCAL_ORDER_ITEMS_CSV.exists():
        df = pd.read_csv(LOCAL_ORDER_ITEMS_CSV, dtype=str).fillna("")
        if "qty" in df.columns:
            df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0).astype(int)
        if "unit_price" in df.columns:
            df["unit_price"] = pd.to_numeric(df["unit_price"], errors="coerce").fillna(0.0)
        return df
    return pd.DataFrame(columns=["order_id","item_name","qty","unit_price"])

def save_orders(df: pd.DataFrame):
    if _github_configured():
        gh_upsert_file(
            ORDERS_PATH_IN_REPO,
            df.to_csv(index=False).encode("utf-8"),
            message="chore: update orders.csv via Streamlit app",
        )
    else:
        df.to_csv(LOCAL_ORDERS_CSV, index=False)

def save_order_items(df: pd.DataFrame):
    if _github_configured():
        gh_upsert_file(
            ORDER_ITEMS_PATH_IN_REPO,
            df.to_csv(index=False).encode("utf-8"),
            message="chore: update order_items.csv via Streamlit app",
        )
    else:
        df.to_csv(LOCAL_ORDER_ITEMS_CSV, index=False)

# ========= 截單判定 =========
def cutoff_state(cutoff_str: str):
    """
    回傳 (passed, msg)
    passed: True=已截單；False=仍可下單
    支援 'HH:MM' 或 'YYYY/MM/DD, %H:%M' / 'YYYY-%m-%d, %H:%M' / 'YYYY/MM/DD %H:%M'
    """
    now = datetime.now(TZ)
    try:
        if any(ch in cutoff_str for ch in ["/", "-", "年", "月"]):
            for fmt in ["%Y/%m/%d, %H:%M", "%Y-%m-%d, %H:%M", "%Y/%m/%d %H:%M"]:
                try:
                    cutoff = datetime.strptime(cutoff_str.strip(), fmt).replace(tzinfo=TZ)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError("cutoff_str format error")
        else:
            hh, mm = map(int, cutoff_str.strip().split(":"))
            cutoff = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except Exception:
        cutoff = now.replace(hour=18, minute=0, second=0, microsecond=0)

    if now >= cutoff:
        return True, f"已截單（{cutoff.strftime('%Y/%m/%d %H:%M')}）"

    left = cutoff - now
    d = left.days
    h = left.seconds // 3600
    m = (left.seconds % 3600) // 60
    return False, f"距離截單剩餘 {d} 天 {h} 小時 {m} 分（{cutoff.strftime('%Y/%m/%d %H:%M')}）"

# （已簡化）移除 Excel 寫入磁碟的功能，僅保留即時下載

# ========= 側邊欄：上傳菜單圖 =========
st.sidebar.title("🍽️ 線上點餐")
with st.sidebar.expander("菜單圖片維護", expanded=False):
    files = st.file_uploader("上傳圖片（jpg/png/jpeg）", type=["jpg","jpeg","png"], accept_multiple_files=True)
    if files:
        for f in files:
            Image.open(f).save(IMG_DIR / f"{uuid.uuid4().hex}.png")
        st.success("圖片已上傳！重新整理即可看到。")

mode = st.sidebar.radio("模式 / Mode", ["點餐模式", "確認模式"])

# ========= 前台點餐 =========
if mode == "點餐模式":
    st.title("📋 線上點餐")
    passed, msg = cutoff_state(CUTOFF)
    st.info(msg)

    # 顯示兩張菜單（取前兩張），簡化為直接顯示
    imgs = sorted([p for p in IMG_DIR.glob("*") if p.suffix.lower() in [".jpg", ".jpeg", ".png"]])
    show_imgs = imgs[:2]

    st.subheader("菜單")
    if not show_imgs:
        st.warning("尚未上傳菜單圖片（側邊欄可上傳）。")
    else:
        cols = st.columns(2)
        for i, p in enumerate(show_imgs):
            with cols[i % 2]:
                st.image(str(p), use_container_width=True, caption=f"菜單 {i+1}")
    st.divider()

    # ====== 填寫餐點（改為 Data Editor，簡潔好用） ======
    st.subheader("填寫餐點")
    default_df = pd.DataFrame([
        {"item_name": "", "qty": 0, "unit_price": 0.0},
        {"item_name": "", "qty": 0, "unit_price": 0.0},
    ])

    with st.form("order_form", clear_on_submit=False):
        edited_df = st.data_editor(
            default_df,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            disabled=passed,
            column_config={
                "item_name": st.column_config.TextColumn("品項名稱"),
                "qty": st.column_config.NumberColumn("數量", min_value=0, step=1),
                "unit_price": st.column_config.NumberColumn("單價", min_value=0.0, step=1.0),
            },
            key="editor_table",
        )

        safe_df = edited_df.copy() if isinstance(edited_df, pd.DataFrame) else default_df.copy()
        for col in ["qty", "unit_price"]:
            if col in safe_df.columns:
                safe_df[col] = pd.to_numeric(safe_df[col], errors="coerce").fillna(0)
        total = int((safe_df.get("qty", 0) * safe_df.get("unit_price", 0)).sum())

        st.markdown(f"### 總計：${total}")
        name = st.text_input("姓名/暱稱", st.session_state.get("name_single_store", ""), disabled=passed)
        note = st.text_input("備註（例如不要香菜／飲品糖冰）", st.session_state.get("note_single_store", ""), disabled=passed)
        submitted = st.form_submit_button("送出訂單", type="primary", use_container_width=True, disabled=passed)

    # 回存姓名/備註到固定鍵（讓下次顯示預設值）
    st.session_state["name_single_store"] = name if 'name' in locals() else st.session_state.get("name_single_store", "")
    st.session_state["note_single_store"] = note if 'note' in locals() else st.session_state.get("note_single_store", "")

    if submitted:
        if not name.strip():
            st.error("請輸入姓名/暱稱"); st.stop()

        valid = safe_df[(safe_df.get("item_name", "").astype(str).str.strip() != "") & (safe_df.get("qty", 0) > 0)].copy()
        if valid.empty:
            st.error("請至少填一列有效餐點"); st.stop()

        orders_df = load_orders()
        items_df  = load_order_items()
        oid = uuid.uuid4().hex[:12]
        now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

        orders_df = pd.concat([orders_df, pd.DataFrame([{
            "order_id": oid,
            "user_name": name.strip(),
            "note": note.strip(),
            "created_at": now,
            "is_paid": "否",
        }])], ignore_index=True)

        valid["order_id"] = oid
        valid["item_name"] = valid["item_name"].astype(str).str.strip()
        valid["qty"] = pd.to_numeric(valid["qty"], errors="coerce").fillna(0).astype(int)
        valid["unit_price"] = pd.to_numeric(valid["unit_price"], errors="coerce").fillna(0.0)
        items_df = pd.concat([items_df, valid.loc[:, ["order_id","item_name","qty","unit_price"]]], ignore_index=True)

        try:
            save_orders(orders_df)
            save_order_items(items_df)
            st.success(f"訂單送出成功！編號：{oid}")
            st.balloons()
        except Exception as e:
            st.error(f"訂單已暫存在本地，但推送到 GitHub 失敗：{e}")

# ========= 管理者模式 =========
else:
    st.title("🔧 確認模式")

    orders = load_orders()
    items  = load_order_items()

    if orders.empty:
        st.info("尚無訂單")
        st.stop()

    # 彙總（品項 + 單價）
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
                if st.button(f"切換收款（{od['order_id']}）", key=f"pay_{od['order_id']}"):
                    all_orders = load_orders()
                    mask = (all_orders["order_id"] == od["order_id"])
                    cur = all_orders.loc[mask, "is_paid"].iloc[0]
                    all_orders.loc[mask, "is_paid"] = "否" if cur=="是" else "是"
                    save_orders(all_orders); st.success("已更新收款狀態"); st.rerun()

    # （已簡化）移除「寫入 Excel 檔案」功能

    # 即時生成並下載（不依賴磁碟）
    st.divider()
    st.subheader("下載 Excel")
    st.caption("包含 Orders（逐單）與 Summary（彙總）兩張工作表。")

    # 組「明細」與「總額」欄位
    detail = items.groupby("order_id").apply(
        lambda d: "; ".join([f"{r['item_name']}x{int(r['qty'])}@{int(r['unit_price'])}" for _, r in d.iterrows()])
    ).reset_index(name="明細")
    total = items.groupby("order_id").apply(
        lambda d: int((d["qty"]*d["unit_price"]).sum())
    ).reset_index(name="總額")

    export_orders = (
        orders.merge(detail, on="order_id", how="left")
              .merge(total,  on="order_id", how="left")
              .loc[:, ["created_at","order_id","user_name","明細","總額","note","is_paid"]]
              .rename(columns={"created_at":"時間","order_id":"訂單ID","user_name":"姓名","note":"備註","is_paid":"已收款"})
    )

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        export_orders.to_excel(writer, sheet_name=ORDERS_WS, index=False)
        agg_out = agg.rename(columns={"item_name":"品項","unit_price":"單價","total_qty":"數量","amount":"金額"})
        agg_out.to_excel(writer, sheet_name=SUMMARY_WS, index=False)
    buf.seek(0)

    st.download_button(
        "⬇️ 下載 Excel（即時產生）",
        data=buf.getvalue(),
        file_name=f"orders_{datetime.now(TZ):%Y%m%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )




