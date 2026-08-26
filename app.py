import sqlite3
from datetime import datetime
import streamlit as st

# --- 1. 資料庫初始化 ---
def get_db():
    conn = sqlite3.connect("link_vault.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        # 建立分類表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """)
        # 建立連結表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                category_id INTEGER,
                note TEXT,
                created_at TEXT,
                FOREIGN KEY (category_id) REFERENCES categories (id)
            )
        """)
        # 預設基礎分類
        cursor.execute("INSERT OR IGNORE INTO categories (name) VALUES ('工作'), ('貓咪'), ('旅遊'), ('未分類')")
        conn.commit()

init_db()

# --- 2. 資料庫操作函數 ---
def fetch_categories():
    with get_db() as conn:
        return conn.execute("SELECT * FROM categories ORDER BY name").fetchall()

def add_category(name):
    with get_db() as conn:
        try:
            conn.execute("INSERT INTO categories (name) VALUES (?)", (name.strip(),))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

def add_link(title, url, category_id, note):
    with get_db() as conn:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        conn.execute(
            "INSERT INTO links (title, url, category_id, note, created_at) VALUES (?, ?, ?, ?, ?)",
            (title.strip(), url.strip(), category_id, note.strip(), now)
        )
        conn.commit()

def fetch_links(category_id=None, keyword=""):
    query = """
        SELECT l.*, c.name as category_name 
        FROM links l 
        LEFT JOIN categories c ON l.category_id = c.id
        WHERE 1=1
    """
    params = []
    if category_id:
        query += " AND l.category_id = ?"
        params.append(category_id)
    if keyword:
        query += " AND (l.title LIKE ? OR l.note LIKE ? OR l.url LIKE ?)"
        params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
    query += " ORDER BY l.id DESC"
    
    with get_db() as conn:
        return conn.execute(query, params).fetchall()

def delete_link(link_id):
    with get_db() as conn:
        conn.execute("DELETE FROM links WHERE id = ?", (link_id,))
        conn.commit()

# --- 3. Streamlit 介面配置 ---
st.set_page_config(page_title="連結收藏庫", layout="wide", page_icon="🔖")
st.title("🔖 個人連結分類收藏庫")

categories = fetch_categories()
cat_dict = {cat["name"]: cat["id"] for cat in categories}

# --- 側邊欄：新增連結 & 分類 ---
with st.sidebar:
    st.header("➕ 新增連結")
    with st.form("add_link_form", clear_on_submit=True):
        link_title = st.text_input("標題 / 名稱 *", placeholder="例: 好用的 Python 技巧教學")
        link_url = st.text_input("網址連結 *", placeholder="https://...")
        selected_cat_name = st.selectbox("選擇分類", list(cat_dict.keys()))
        link_note = st.text_area("備註說明", placeholder="記錄重點或關鍵字")
        
        if st.form_submit_button("儲存連結", use_container_width=True):
            if link_title and link_url:
                add_link(link_title, link_url, cat_dict[selected_cat_name], link_note)
                st.success("儲存成功！")
                st.rerun()
            else:
                st.error("標題與網址為必填項。")

    st.markdown("---")
    st.header("🏷️ 分類管理")
    new_cat_name = st.text_input("新增自訂分類", placeholder="例: 料理、投資")
    if st.button("建立分類", use_container_width=True):
        if new_cat_name:
            if add_category(new_cat_name):
                st.success(f"已新增分類「{new_cat_name}」")
                st.rerun()
            else:
                st.warning("該分類已存在。")

# --- 主畫面：瀏覽與檢視 ---
col_filter, col_search = st.columns([1, 2])
with col_filter:
    filter_cat = st.selectbox("📂 依分類篩選", ["全部"] + list(cat_dict.keys()))
with col_search:
    search_keyword = st.text_input("🔍 關鍵字搜尋 (搜尋標題、備註或網址)", "")

current_cat_id = None if filter_cat == "全部" else cat_dict[filter_cat]
links = fetch_links(category_id=current_cat_id, keyword=search_keyword)

st.markdown(f"**共找到 {len(links)} 筆收藏**")

# 卡片式清單呈現
for item in links:
    with st.container(border=True):
        c_info, c_action = st.columns([5, 1])
        with c_info:
            st.markdown(f"### [{item['title']}]({item['url']})")
            st.caption(f"🏷️ **{item['category_name']}** ｜ 🕒 {item['created_at']}")
            if item["note"]:
                st.write(item["note"])
        with c_action:
            if st.button("🗑️ 刪除", key=f"del_{item['id']}", use_container_width=True):
                delete_link(item["id"])
                st.rerun()
