import sqlite3
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import streamlit as st


# ============================================================
# 1. Streamlit 基本設定
# ============================================================

st.set_page_config(
    page_title="我的連結收藏",
    page_icon="🔖",
    layout="centered",
    initial_sidebar_state="collapsed",
)


# ============================================================
# 2. 資料庫
# ============================================================

DB_NAME = "link_vault.db"


def get_db():
    conn = sqlite3.connect(
        DB_NAME,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    # 啟用 SQLite Foreign Key
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


def init_db():
    with get_db() as conn:
        cursor = conn.cursor()

        # 分類
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """)

        # 收藏
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                category_id INTEGER,
                note TEXT,
                created_at TEXT,
                FOREIGN KEY (category_id)
                    REFERENCES categories(id)
            )
        """)

        # 預設分類
        default_categories = [
            "工作",
            "貓咪",
            "旅遊",
            "未分類"
        ]

        for category in default_categories:
            cursor.execute(
                "INSERT OR IGNORE INTO categories (name) VALUES (?)",
                (category,)
            )

        conn.commit()


init_db()


# ============================================================
# 3. 分類相關功能
# ============================================================

def fetch_categories():
    with get_db() as conn:
        return conn.execute("""
            SELECT *
            FROM categories
            ORDER BY
                CASE WHEN name = '未分類' THEN 1 ELSE 0 END,
                name
        """).fetchall()


def add_category(name):
    name = name.strip()

    if not name:
        return False

    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO categories (name) VALUES (?)",
                (name,)
            )
            conn.commit()
            return True

        except sqlite3.IntegrityError:
            return False


# ============================================================
# 4. 收藏相關功能
# ============================================================

def add_link(title, url, category_id, note=""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    with get_db() as conn:
        conn.execute("""
            INSERT INTO links (
                title,
                url,
                category_id,
                note,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            title.strip(),
            url.strip(),
            category_id,
            note.strip(),
            now
        ))

        conn.commit()


def update_link(link_id, title, url, category_id, note=""):
    with get_db() as conn:
        conn.execute("""
            UPDATE links
            SET
                title = ?,
                url = ?,
                category_id = ?,
                note = ?
            WHERE id = ?
        """, (
            title.strip(),
            url.strip(),
            category_id,
            note.strip(),
            link_id
        ))

        conn.commit()


def delete_link(link_id):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM links WHERE id = ?",
            (link_id,)
        )

        conn.commit()


def fetch_links(category_id=None, keyword=""):
    query = """
        SELECT
            l.*,
            c.name AS category_name
        FROM links l
        LEFT JOIN categories c
            ON l.category_id = c.id
        WHERE 1 = 1
    """

    params = []

    # 分類篩選
    if category_id is not None:
        query += " AND l.category_id = ?"
        params.append(category_id)

    # 關鍵字搜尋
    if keyword.strip():
        keyword = keyword.strip()

        query += """
            AND (
                l.title LIKE ?
                OR l.note LIKE ?
                OR l.url LIKE ?
                OR c.name LIKE ?
            )
        """

        search_value = f"%{keyword}%"

        params.extend([
            search_value,
            search_value,
            search_value,
            search_value
        ])

    query += " ORDER BY l.id DESC"

    with get_db() as conn:
        return conn.execute(
            query,
            params
        ).fetchall()


# ============================================================
# 5. URL 處理
# ============================================================

def normalize_url(url):
    """
    如果使用者只輸入：
    youtube.com/xxx

    自動轉為：
    https://youtube.com/xxx
    """

    url = url.strip()

    if not url:
        return ""

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    return url


def is_valid_url(url):
    try:
        parsed = urlparse(url)

        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
        )

    except Exception:
        return False


# ============================================================
# 6. 自動抓取網頁標題
# ============================================================

class TitleParser(HTMLParser):

    def __init__(self):
        super().__init__()

        self.in_title = False
        self.title = ""

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


def get_page_title(url):
    """
    嘗試取得網頁 <title>。

    如果網站阻擋、逾時或沒有 title，
    回傳 None。
    """

    try:
        request = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 "
                    "Version/17.0 Mobile/15E148 Safari/604.1"
                )
            }
        )

        with urlopen(
            request,
            timeout=5
        ) as response:

            # 只讀取前面一部分即可
            html = response.read(300_000)

            # 嘗試取得網站 encoding
            encoding = response.headers.get_content_charset()

            if not encoding:
                encoding = "utf-8"

            try:
                text = html.decode(
                    encoding,
                    errors="ignore"
                )
            except Exception:
                text = html.decode(
                    "utf-8",
                    errors="ignore"
                )

        parser = TitleParser()
        parser.feed(text)

        title = parser.title.strip()

        # 去掉多餘空白
        title = " ".join(title.split())

        if title:
            return title

    except Exception:
        pass

    return None


def fallback_title(url):
    """
    如果抓不到網頁標題，
    至少使用 domain 當標題。
    """

    try:
        parsed = urlparse(url)

        domain = parsed.netloc

        domain = domain.replace("www.", "")

        if domain:
            return domain

    except Exception:
        pass

    return "未命名收藏"


# ============================================================
# 7. Session State
# ============================================================

if "editing_link_id" not in st.session_state:
    st.session_state.editing_link_id = None

if "delete_link_id" not in st.session_state:
    st.session_state.delete_link_id = None


# ============================================================
# 8. 介面標題
# ============================================================

st.title("🔖 我的連結收藏")

st.caption(
    "看到想保存的內容，就快速貼進來。"
)


# ============================================================
# 9. 讀取分類
# ============================================================

categories = fetch_categories()

cat_dict = {
    category["name"]: category["id"]
    for category in categories
}

cat_names = list(cat_dict.keys())


# ============================================================
# 10. Tabs
# ============================================================

tab_add, tab_library = st.tabs([
    "➕ 新增收藏",
    "📚 收藏庫"
])


# ============================================================
# TAB 1：新增收藏
# ============================================================

with tab_add:

    st.markdown("### 快速收藏")

    with st.form(
        "add_link_form",
        clear_on_submit=True
    ):

        # ----------------------------
        # 標題
        # ----------------------------

        link_title = st.text_input(
            "標題",
            placeholder="可以不填，系統會嘗試自動取得"
        )

        # ----------------------------
        # URL
        # ----------------------------

        link_url = st.text_input(
            "Link",
            placeholder="https://..."
        )

        # ----------------------------
        # 分類
        # ----------------------------

        selected_cat_name = st.selectbox(
            "分類",
            cat_names
        )

        # ----------------------------
        # 儲存
        # ----------------------------

        submitted = st.form_submit_button(
            "💾 儲存收藏",
            type="primary",
            use_container_width=True
        )

        if submitted:

            url = normalize_url(link_url)

            # URL 沒填
            if not url:
                st.error("請輸入網址。")

            # URL 格式錯誤
            elif not is_valid_url(url):
                st.error(
                    "網址格式似乎不正確，"
                    "請輸入有效的網址。"
                )

            else:

                title = link_title.strip()

                # 如果沒填標題
                # 自動抓取網頁 title
                if not title:

                    with st.spinner(
                        "正在取得網頁標題..."
                    ):
                        title = get_page_title(url)

                    # 還是抓不到
                    if not title:
                        title = fallback_title(url)

                add_link(
                    title=title,
                    url=url,
                    category_id=cat_dict[
                        selected_cat_name
                    ],
                    note=""
                )

                st.success(
                    f"✅ 已收藏：{title}"
                )


    # --------------------------------------------------------
    # 新增分類
    # --------------------------------------------------------

    with st.expander("＋ 新增分類"):

        new_cat_name = st.text_input(
            "分類名稱",
            placeholder="例如：料理、投資、AI 工具",
            key="new_category_name"
        )

        if st.button(
            "建立分類",
            use_container_width=True
        ):

            new_cat_name = new_cat_name.strip()

            if not new_cat_name:

                st.warning(
                    "請輸入分類名稱。"
                )

            elif add_category(new_cat_name):

                st.success(
                    f"✅ 已新增分類「{new_cat_name}」"
                )

                st.rerun()

            else:

                st.warning(
                    "這個分類已經存在。"
                )


# ============================================================
# TAB 2：收藏庫
# ============================================================

with tab_library:

    st.markdown("### 📚 收藏庫")

    # --------------------------------------------------------
    # 搜尋
    # --------------------------------------------------------

    search_keyword = st.text_input(
        "搜尋收藏",
        placeholder="🔍 搜尋標題、網址、分類..."
    )

    # --------------------------------------------------------
    # 分類
    # --------------------------------------------------------

    filter_cat = st.selectbox(
        "分類",
        ["全部"] + cat_names,
        key="library_filter"
    )

    current_cat_id = (
        None
        if filter_cat == "全部"
        else cat_dict[filter_cat]
    )

    links = fetch_links(
        category_id=current_cat_id,
        keyword=search_keyword
    )

    st.caption(
        f"目前共有 {len(links)} 筆收藏"
    )

    # --------------------------------------------------------
    # 沒有資料
    # --------------------------------------------------------

    if not links:

        st.info(
            "目前沒有符合條件的收藏。"
        )

    # --------------------------------------------------------
    # 收藏卡片
    # --------------------------------------------------------

    for item in links:

        link_id = item["id"]

        with st.container(border=True):

            # =================================================
            # 編輯模式
            # =================================================

            if (
                st.session_state.editing_link_id
                == link_id
            ):

                st.markdown("#### ✏️ 編輯收藏")

                edit_title = st.text_input(
                    "標題",
                    value=item["title"],
                    key=f"edit_title_{link_id}"
                )

                edit_url = st.text_input(
                    "Link",
                    value=item["url"],
                    key=f"edit_url_{link_id}"
                )

                # 目前分類 index
                try:
                    current_index = (
                        cat_names.index(
                            item["category_name"]
                        )
                    )
                except ValueError:
                    current_index = 0

                edit_category = st.selectbox(
                    "分類",
                    cat_names,
                    index=current_index,
                    key=f"edit_category_{link_id}"
                )

                edit_note = st.text_area(
                    "備註",
                    value=item["note"] or "",
                    placeholder="可選填",
                    key=f"edit_note_{link_id}"
                )

                col_save, col_cancel = st.columns(2)

                with col_save:

                    if st.button(
                        "💾 儲存修改",
                        key=f"save_{link_id}",
                        type="primary",
                        use_container_width=True
                    ):

                        new_url = normalize_url(
                            edit_url
                        )

                        if not edit_title.strip():

                            st.error(
                                "標題不能為空白。"
                            )

                        elif not is_valid_url(
                            new_url
                        ):

                            st.error(
                                "網址格式不正確。"
                            )

                        else:

                            update_link(
                                link_id=link_id,
                                title=edit_title,
                                url=new_url,
                                category_id=cat_dict[
                                    edit_category
                                ],
                                note=edit_note
                            )

                            st.session_state[
                                "editing_link_id"
                            ] = None

                            st.success(
                                "修改完成！"
                            )

                            st.rerun()

                with col_cancel:

                    if st.button(
                        "取消",
                        key=f"cancel_edit_{link_id}",
                        use_container_width=True
                    ):

                        st.session_state[
                            "editing_link_id"
                        ] = None

                        st.rerun()


            # =================================================
            # 一般瀏覽模式
            # =================================================

            else:

                # 標題
                st.markdown(
                    f"### {item['title']}"
                )

                # 分類與日期
                category_name = (
                    item["category_name"]
                    or "未分類"
                )

                st.caption(
                    f"🏷️ {category_name}"
                    f"　·　🕒 {item['created_at']}"
                )

                # 備註
                if item["note"]:

                    st.write(
                        item["note"]
                    )

                # ------------------------------------------------
                # 操作按鈕
                # ------------------------------------------------

                col_open, col_edit, col_delete = (
                    st.columns([3, 1, 1])
                )

                with col_open:

                    st.link_button(
                        "🔗 開啟連結",
                        item["url"],
                        use_container_width=True
                    )

                with col_edit:

                    if st.button(
                        "✏️",
                        key=f"edit_{link_id}",
                        help="編輯",
                        use_container_width=True
                    ):

                        st.session_state[
                            "editing_link_id"
                        ] = link_id

                        st.session_state[
                            "delete_link_id"
                        ] = None

                        st.rerun()

                with col_delete:

                    if st.button(
                        "🗑️",
                        key=f"delete_{link_id}",
                        help="刪除",
                        use_container_width=True
                    ):

                        st.session_state[
                            "delete_link_id"
                        ] = link_id

                        st.session_state[
                            "editing_link_id"
                        ] = None

                        st.rerun()

                # ------------------------------------------------
                # 刪除確認
                # ------------------------------------------------

                if (
                    st.session_state.delete_link_id
                    == link_id
                ):

                    st.warning(
                        f"確定要刪除「"
                        f"{item['title']}」嗎？"
                    )

                    confirm_col, cancel_col = (
                        st.columns(2)
                    )

                    with confirm_col:

                        if st.button(
                            "確定刪除",
                            key=f"confirm_delete_{link_id}",
                            type="primary",
                            use_container_width=True
                        ):

                            delete_link(
                                link_id
                            )

                            st.session_state[
                                "delete_link_id"
                            ] = None

                            st.rerun()

                    with cancel_col:

                        if st.button(
                            "取消",
                            key=f"cancel_delete_{link_id}",
                            use_container_width=True
                        ):

                            st.session_state[
                                "delete_link_id"
                            ] = None

                            st.rerun()
