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

DB_NAME = "link_vault.db"


# ============================================================
# 2. 資料庫
# ============================================================

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

        # ------------------------
        # 分類表
        # ------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """)

        # ------------------------
        # 收藏表
        # ------------------------
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

        # ------------------------
        # 預設分類
        # ------------------------
        default_categories = [
            "工作",
            "貓咪",
            "旅遊",
            "未分類"
        ]

        for category in default_categories:
            cursor.execute(
                """
                INSERT OR IGNORE INTO categories (name)
                VALUES (?)
                """,
                (category,)
            )

        conn.commit()


init_db()


# ============================================================
# 3. 分類功能
# ============================================================

def fetch_categories():
    """
    取得所有分類。
    「未分類」固定排在最後。
    """

    with get_db() as conn:
        return conn.execute("""
            SELECT *
            FROM categories
            ORDER BY
                CASE
                    WHEN name = '未分類' THEN 1
                    ELSE 0
                END,
                name COLLATE NOCASE
        """).fetchall()


def add_category(name):
    """
    新增分類。
    """

    name = name.strip()

    if not name:
        return False

    with get_db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO categories (name)
                VALUES (?)
                """,
                (name,)
            )

            conn.commit()

            return True

        except sqlite3.IntegrityError:
            return False


def rename_category(category_id, new_name):
    """
    重新命名分類。
    """

    new_name = new_name.strip()

    if not new_name:
        return False, "分類名稱不能為空白。"

    with get_db() as conn:
        cursor = conn.cursor()

        category = cursor.execute(
            """
            SELECT *
            FROM categories
            WHERE id = ?
            """,
            (category_id,)
        ).fetchone()

        if not category:
            return False, "找不到這個分類。"

        if category["name"] == "未分類":
            return False, "「未分類」不能重新命名。"

        try:
            cursor.execute(
                """
                UPDATE categories
                SET name = ?
                WHERE id = ?
                """,
                (
                    new_name,
                    category_id
                )
            )

            conn.commit()

            return True, None

        except sqlite3.IntegrityError:
            return False, "這個分類名稱已經存在。"


def get_category_link_count(category_id):
    """
    取得分類中的收藏數量。
    """

    with get_db() as conn:
        result = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM links
            WHERE category_id = ?
            """,
            (category_id,)
        ).fetchone()

        return result["count"]


def delete_category(category_id):
    """
    刪除分類。

    分類中的收藏不會被刪除，
    而是移到「未分類」。
    """

    with get_db() as conn:
        cursor = conn.cursor()

        # 找到「未分類」
        uncategorized = cursor.execute(
            """
            SELECT id
            FROM categories
            WHERE name = '未分類'
            """
        ).fetchone()

        if not uncategorized:
            return False

        uncategorized_id = uncategorized["id"]

        # 不允許刪除「未分類」
        if category_id == uncategorized_id:
            return False

        # ------------------------
        # 原分類收藏 → 未分類
        # ------------------------
        cursor.execute(
            """
            UPDATE links
            SET category_id = ?
            WHERE category_id = ?
            """,
            (
                uncategorized_id,
                category_id
            )
        )

        # ------------------------
        # 刪除分類
        # ------------------------
        cursor.execute(
            """
            DELETE FROM categories
            WHERE id = ?
            """,
            (category_id,)
        )

        conn.commit()

        return True


# ============================================================
# 4. 收藏功能
# ============================================================

def add_link(
    title,
    url,
    category_id,
    note=""
):
    now = datetime.now().strftime(
        "%Y-%m-%d %H:%M"
    )

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO links (
                title,
                url,
                category_id,
                note,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                title.strip(),
                url.strip(),
                category_id,
                note.strip(),
                now
            )
        )

        conn.commit()


def update_link(
    link_id,
    title,
    url,
    category_id,
    note=""
):
    with get_db() as conn:
        conn.execute(
            """
            UPDATE links
            SET
                title = ?,
                url = ?,
                category_id = ?,
                note = ?
            WHERE id = ?
            """,
            (
                title.strip(),
                url.strip(),
                category_id,
                note.strip(),
                link_id
            )
        )

        conn.commit()


def delete_link(link_id):
    with get_db() as conn:
        conn.execute(
            """
            DELETE FROM links
            WHERE id = ?
            """,
            (link_id,)
        )

        conn.commit()


def fetch_links(
    category_id=None,
    keyword=""
):
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

    # ------------------------
    # 分類篩選
    # ------------------------
    if category_id is not None:
        query += """
            AND l.category_id = ?
        """

        params.append(
            category_id
        )

    # ------------------------
    # 搜尋
    # ------------------------
    keyword = keyword.strip()

    if keyword:
        query += """
            AND (
                l.title LIKE ?
                OR l.note LIKE ?
                OR l.url LIKE ?
                OR c.name LIKE ?
            )
        """

        value = f"%{keyword}%"

        params.extend([
            value,
            value,
            value,
            value
        ])

    query += """
        ORDER BY l.id DESC
    """

    with get_db() as conn:
        return conn.execute(
            query,
            params
        ).fetchall()


# ============================================================
# 5. URL 功能
# ============================================================

def normalize_url(url):
    """
    如果輸入：
    example.com

    自動變成：
    https://example.com
    """

    url = url.strip()

    if not url:
        return ""

    if not url.startswith(
        ("http://", "https://")
    ):
        url = "https://" + url

    return url


def is_valid_url(url):
    """
    簡單檢查網址格式。
    """

    try:
        parsed = urlparse(url)

        return (
            parsed.scheme in (
                "http",
                "https"
            )
            and bool(parsed.netloc)
        )

    except Exception:
        return False


# ============================================================
# 6. 自動取得網頁標題
# ============================================================

class TitleParser(HTMLParser):

    def __init__(self):
        super().__init__()

        self.in_title = False
        self.title = ""

    def handle_starttag(
        self,
        tag,
        attrs
    ):
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(
        self,
        tag
    ):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(
        self,
        data
    ):
        if self.in_title:
            self.title += data


def get_page_title(url):
    """
    嘗試取得網頁的 <title>。

    網站若封鎖、逾時或無法取得，
    會回傳 None。
    """

    try:
        request = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(iPhone; CPU iPhone OS 17_0 "
                    "like Mac OS X) "
                    "AppleWebKit/605.1.15 "
                    "(KHTML, like Gecko) "
                    "Version/17.0 "
                    "Mobile/15E148 "
                    "Safari/604.1"
                )
            }
        )

        with urlopen(
            request,
            timeout=5
        ) as response:

            # 只讀前 300 KB
            html = response.read(
                300_000
            )

            encoding = (
                response
                .headers
                .get_content_charset()
            )

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

        # 去掉換行 / 多餘空白
        title = " ".join(
            title.split()
        )

        if title:
            return title

    except Exception:
        pass

    return None


def fallback_title(url):
    """
    抓不到網頁 title 時，
    使用網站 domain 當標題。
    """

    try:
        parsed = urlparse(url)

        domain = parsed.netloc

        if domain.startswith("www."):
            domain = domain[4:]

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

if "editing_category_id" not in st.session_state:
    st.session_state.editing_category_id = None

if "delete_category_id" not in st.session_state:
    st.session_state.delete_category_id = None

if "flash_message" not in st.session_state:
    st.session_state.flash_message = None


# ============================================================
# 8. Header
# ============================================================

st.title("🔖 我的連結收藏")

st.caption(
    "看到想保存的內容，就快速貼進來。"
)


# ============================================================
# 9. 顯示一次性成功訊息
# ============================================================

if st.session_state.flash_message:

    st.success(
        st.session_state.flash_message
    )

    st.session_state.flash_message = None


# ============================================================
# 10. 取得分類
# ============================================================

categories = fetch_categories()

cat_dict = {
    category["name"]: category["id"]
    for category in categories
}

cat_names = list(
    cat_dict.keys()
)


# ============================================================
# 11. 三個主要分頁
# ============================================================

tab_add, tab_library, tab_categories = st.tabs([
    "➕ 新增收藏",
    "📚 收藏庫",
    "🏷️ 分類"
])


# ============================================================
# TAB 1：新增收藏
# ============================================================

with tab_add:

    st.markdown(
        "### 快速收藏"
    )

    with st.form(
        "add_link_form",
        clear_on_submit=True
    ):

        # ------------------------
        # 標題
        # ------------------------
        link_title = st.text_input(
            "標題",
            placeholder=(
                "可以不填，"
                "系統會自動嘗試取得"
            )
        )

        # ------------------------
        # Link
        # ------------------------
        link_url = st.text_input(
            "Link",
            placeholder="https://..."
        )

        # ------------------------
        # 分類
        # ------------------------
        selected_cat_name = st.selectbox(
            "分類",
            cat_names
        )

        # ------------------------
        # 儲存
        # ------------------------
        submitted = st.form_submit_button(
            "💾 儲存收藏",
            type="primary",
            use_container_width=True
        )

        if submitted:

            url = normalize_url(
                link_url
            )

            # 沒有網址
            if not url:

                st.error(
                    "請輸入網址。"
                )

            # 網址格式錯誤
            elif not is_valid_url(url):

                st.error(
                    "網址格式似乎不正確。"
                )

            else:

                title = (
                    link_title.strip()
                )

                # ------------------------
                # 沒標題 → 自動取得
                # ------------------------
                if not title:

                    with st.spinner(
                        "正在取得網頁標題..."
                    ):

                        title = (
                            get_page_title(url)
                        )

                    # 抓不到 title
                    if not title:

                        title = (
                            fallback_title(url)
                        )

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


# ============================================================
# TAB 2：收藏庫
# ============================================================

with tab_library:

    st.markdown(
        "### 📚 收藏庫"
    )

    # ------------------------
    # 搜尋
    # ------------------------
    search_keyword = st.text_input(
        "搜尋收藏",
        placeholder=(
            "🔍 搜尋標題、網址、分類..."
        ),
        key="library_search"
    )

    # ------------------------
    # 分類篩選
    # ------------------------
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

    # ------------------------
    # 沒有結果
    # ------------------------
    if not links:

        st.info(
            "目前沒有符合條件的收藏。"
        )

    # ------------------------
    # 收藏卡片
    # ------------------------
    for item in links:

        link_id = item["id"]

        with st.container(
            border=True
        ):

            # =================================================
            # 編輯模式
            # =================================================
            if (
                st.session_state.editing_link_id
                == link_id
            ):

                st.markdown(
                    "#### ✏️ 編輯收藏"
                )

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

                # ------------------------
                # 現在分類的位置
                # ------------------------
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
                    key=(
                        f"edit_category_"
                        f"{link_id}"
                    )
                )

                edit_note = st.text_area(
                    "備註",
                    value=(
                        item["note"]
                        or ""
                    ),
                    placeholder="可選填",
                    key=f"edit_note_{link_id}"
                )

                col_save, col_cancel = (
                    st.columns(2)
                )

                # ------------------------
                # 儲存
                # ------------------------
                with col_save:

                    if st.button(
                        "💾 儲存修改",
                        key=f"save_{link_id}",
                        type="primary",
                        use_container_width=True
                    ):

                        new_url = (
                            normalize_url(
                                edit_url
                            )
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
                                category_id=(
                                    cat_dict[
                                        edit_category
                                    ]
                                ),
                                note=edit_note
                            )

                            st.session_state[
                                "editing_link_id"
                            ] = None

                            st.session_state[
                                "flash_message"
                            ] = "✅ 收藏已更新。"

                            st.rerun()

                # ------------------------
                # 取消
                # ------------------------
                with col_cancel:

                    if st.button(
                        "取消",
                        key=(
                            f"cancel_edit_"
                            f"{link_id}"
                        ),
                        use_container_width=True
                    ):

                        st.session_state[
                            "editing_link_id"
                        ] = None

                        st.rerun()


            # =================================================
            # 一般模式
            # =================================================
            else:

                st.markdown(
                    f"### {item['title']}"
                )

                category_name = (
                    item["category_name"]
                    or "未分類"
                )

                st.caption(
                    f"🏷️ {category_name}"
                    f"　·　"
                    f"🕒 {item['created_at']}"
                )

                # ------------------------
                # 備註
                # ------------------------
                if item["note"]:

                    st.write(
                        item["note"]
                    )

                # ------------------------
                # 操作
                # ------------------------
                (
                    col_open,
                    col_edit,
                    col_delete
                ) = st.columns(
                    [3, 1, 1]
                )

                # 開啟
                with col_open:

                    st.link_button(
                        "🔗 開啟連結",
                        item["url"],
                        use_container_width=True
                    )

                # 編輯
                with col_edit:

                    if st.button(
                        "✏️",
                        key=f"edit_{link_id}",
                        help="編輯收藏",
                        use_container_width=True
                    ):

                        st.session_state[
                            "editing_link_id"
                        ] = link_id

                        st.session_state[
                            "delete_link_id"
                        ] = None

                        st.rerun()

                # 刪除
                with col_delete:

                    if st.button(
                        "🗑️",
                        key=f"delete_{link_id}",
                        help="刪除收藏",
                        use_container_width=True
                    ):

                        st.session_state[
                            "delete_link_id"
                        ] = link_id

                        st.session_state[
                            "editing_link_id"
                        ] = None

                        st.rerun()

                # ------------------------
                # 刪除確認
                # ------------------------
                if (
                    st.session_state.delete_link_id
                    == link_id
                ):

                    st.warning(
                        f"確定要刪除"
                        f"「{item['title']}」嗎？"
                    )

                    (
                        confirm_col,
                        cancel_col
                    ) = st.columns(2)

                    with confirm_col:

                        if st.button(
                            "確定刪除",
                            key=(
                                f"confirm_delete_"
                                f"{link_id}"
                            ),
                            type="primary",
                            use_container_width=True
                        ):

                            delete_link(
                                link_id
                            )

                            st.session_state[
                                "delete_link_id"
                            ] = None

                            st.session_state[
                                "flash_message"
                            ] = "🗑️ 收藏已刪除。"

                            st.rerun()

                    with cancel_col:

                        if st.button(
                            "取消",
                            key=(
                                f"cancel_delete_"
                                f"{link_id}"
                            ),
                            use_container_width=True
                        ):

                            st.session_state[
                                "delete_link_id"
                            ] = None

                            st.rerun()


# ============================================================
# TAB 3：分類管理
# ============================================================

with tab_categories:

    st.markdown(
        "### 🏷️ 分類管理"
    )

    st.caption(
        "新增、重新命名或刪除你的收藏分類。"
    )


    # ========================================================
    # 新增分類
    # ========================================================

    st.markdown(
        "#### ＋ 新增分類"
    )

    # 使用 Form + clear_on_submit
    # 不再手動修改 text_input 對應的 session_state
    with st.form(
        "add_category_form",
        clear_on_submit=True
    ):

        new_cat_name = st.text_input(
            "分類名稱",
            placeholder=(
                "例如：料理、投資、AI 工具"
            )
        )

        add_category_submitted = (
            st.form_submit_button(
                "＋ 新增分類",
                type="primary",
                use_container_width=True
            )
        )

        if add_category_submitted:

            category_name = (
                new_cat_name.strip()
            )

            if not category_name:

                st.warning(
                    "請輸入分類名稱。"
                )

            elif add_category(
                category_name
            ):

                # 使用 flash message，
                # 不修改 widget 本身的 state
                st.session_state[
                    "flash_message"
                ] = (
                    f"✅ 已新增分類"
                    f"「{category_name}」。"
                )

                st.rerun()

            else:

                st.warning(
                    "這個分類已經存在。"
                )


    st.divider()


    # ========================================================
    # 我的分類
    # ========================================================

    st.markdown(
        "#### 我的分類"
    )

    # 重新取得最新分類
    category_list = (
        fetch_categories()
    )

    for category in category_list:

        category_id = (
            category["id"]
        )

        category_name = (
            category["name"]
        )

        link_count = (
            get_category_link_count(
                category_id
            )
        )

        with st.container(
            border=True
        ):

            # =================================================
            # 未分類
            # =================================================
            if category_name == "未分類":

                (
                    col_info,
                    col_lock
                ) = st.columns(
                    [5, 1]
                )

                with col_info:

                    st.markdown(
                        f"**📦 {category_name}**"
                    )

                    st.caption(
                        f"{link_count} 筆收藏"
                    )

                with col_lock:

                    st.markdown(
                        "🔒"
                    )


            # =================================================
            # 編輯分類模式
            # =================================================
            elif (
                st.session_state.editing_category_id
                == category_id
            ):

                st.markdown(
                    f"**✏️ 編輯「{category_name}」**"
                )

                new_name = st.text_input(
                    "新的分類名稱",
                    value=category_name,
                    key=(
                        f"rename_category_"
                        f"{category_id}"
                    )
                )

                (
                    col_save,
                    col_cancel
                ) = st.columns(2)

                # ------------------------
                # 儲存
                # ------------------------
                with col_save:

                    if st.button(
                        "儲存",
                        type="primary",
                        use_container_width=True,
                        key=(
                            f"save_category_"
                            f"{category_id}"
                        )
                    ):

                        success, error = (
                            rename_category(
                                category_id,
                                new_name
                            )
                        )

                        if success:

                            st.session_state[
                                "editing_category_id"
                            ] = None

                            st.session_state[
                                "flash_message"
                            ] = (
                                "✅ 分類名稱已更新。"
                            )

                            st.rerun()

                        else:

                            st.error(
                                error
                            )

                # ------------------------
                # 取消
                # ------------------------
                with col_cancel:

                    if st.button(
                        "取消",
                        use_container_width=True,
                        key=(
                            f"cancel_category_edit_"
                            f"{category_id}"
                        )
                    ):

                        st.session_state[
                            "editing_category_id"
                        ] = None

                        st.rerun()


            # =================================================
            # 一般分類模式
            # =================================================
            else:

                (
                    col_info,
                    col_edit,
                    col_delete
                ) = st.columns(
                    [5, 1, 1]
                )

                # ------------------------
                # 分類資訊
                # ------------------------
                with col_info:

                    st.markdown(
                        f"**🏷️ {category_name}**"
                    )

                    st.caption(
                        f"{link_count} 筆收藏"
                    )

                # ------------------------
                # 修改分類
                # ------------------------
                with col_edit:

                    if st.button(
                        "✏️",
                        key=(
                            f"edit_category_"
                            f"{category_id}"
                        ),
                        help="重新命名",
                        use_container_width=True
                    ):

                        st.session_state[
                            "editing_category_id"
                        ] = category_id

                        st.session_state[
                            "delete_category_id"
                        ] = None

                        st.rerun()

                # ------------------------
                # 刪除分類
                # ------------------------
                with col_delete:

                    if st.button(
                        "🗑️",
                        key=(
                            f"delete_category_"
                            f"{category_id}"
                        ),
                        help="刪除分類",
                        use_container_width=True
                    ):

                        st.session_state[
                            "delete_category_id"
                        ] = category_id

                        st.session_state[
                            "editing_category_id"
                        ] = None

                        st.rerun()


                # =================================================
                # 刪除分類確認
                # =================================================

                if (
                    st.session_state.delete_category_id
                    == category_id
                ):

                    if link_count > 0:

                        st.warning(
                            f"確定要刪除"
                            f"「{category_name}」嗎？"
                            f"\n\n"
                            f"其中的 {link_count} 筆收藏"
                            f"會移到「未分類」。"
                        )

                    else:

                        st.warning(
                            f"確定要刪除"
                            f"「{category_name}」嗎？"
                        )

                    (
                        col_confirm,
                        col_cancel
                    ) = st.columns(2)

                    # ------------------------
                    # 確定刪除
                    # ------------------------
                    with col_confirm:

                        if st.button(
                            "確定刪除",
                            type="primary",
                            use_container_width=True,
                            key=(
                                f"confirm_category_"
                                f"{category_id}"
                            )
                        ):

                            success = (
                                delete_category(
                                    category_id
                                )
                            )

                            st.session_state[
                                "delete_category_id"
                            ] = None

                            if success:

                                st.session_state[
                                    "flash_message"
                                ] = (
                                    f"🗑️ 已刪除分類"
                                    f"「{category_name}」。"
                                )

                            st.rerun()

                    # ------------------------
                    # 取消
                    # ------------------------
                    with col_cancel:

                        if st.button(
                            "取消",
                            use_container_width=True,
                            key=(
                                f"cancel_category_"
                                f"{category_id}"
                            )
                        ):

                            st.session_state[
                                "delete_category_id"
                            ] = None

                            st.rerun()
