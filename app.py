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

PAGE_ADD = "➕ 新增收藏"
PAGE_LIBRARY = "📚 收藏庫"
PAGE_CATEGORIES = "🏷️ 分類"


# ============================================================
# 2. 資料庫
# ============================================================

def get_db():
    conn = sqlite3.connect(
        DB_NAME,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


def init_db():
    with get_db() as conn:
        cursor = conn.cursor()

        # ----------------------------------------------------
        # 分類表
        # ----------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """)

        # ----------------------------------------------------
        # 收藏表
        # ----------------------------------------------------
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

        # ----------------------------------------------------
        # 只有第一次建立資料庫時才加入預設分類
        #
        # 避免使用者刪掉「工作 / 貓咪 / 旅遊」後，
        # 下一次重新執行又被建立回來。
        # ----------------------------------------------------
        count = cursor.execute(
            "SELECT COUNT(*) AS count FROM categories"
        ).fetchone()["count"]

        if count == 0:

            default_categories = [
                "工作",
                "貓咪",
                "旅遊",
                "未分類"
            ]

            for category in default_categories:
                cursor.execute(
                    """
                    INSERT INTO categories (name)
                    VALUES (?)
                    """,
                    (category,)
                )

        else:
            # 「未分類」是安全分類，必須永遠存在
            cursor.execute(
                """
                INSERT OR IGNORE INTO categories (name)
                VALUES ('未分類')
                """
            )

        conn.commit()


init_db()


# ============================================================
# 3. 分類功能
# ============================================================

def fetch_categories():
    """
    取得所有分類。
    「未分類」固定放最後。
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


def fetch_categories_with_counts():
    """
    取得分類以及每個分類中的收藏數量。
    """

    with get_db() as conn:
        return conn.execute("""
            SELECT
                c.id,
                c.name,
                COUNT(l.id) AS link_count
            FROM categories c
            LEFT JOIN links l
                ON l.category_id = c.id
            GROUP BY
                c.id,
                c.name
            ORDER BY
                CASE
                    WHEN c.name = '未分類' THEN 1
                    ELSE 0
                END,
                c.name COLLATE NOCASE
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
    修改分類名稱。
    """

    new_name = new_name.strip()

    if not new_name:
        return False, "分類名稱不能是空白。"

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


def delete_category(category_id):
    """
    刪除分類。

    分類裡面的收藏不會刪除，
    而是全部移到「未分類」。
    """

    with get_db() as conn:
        cursor = conn.cursor()

        # ----------------------------------------------------
        # 找到未分類
        # ----------------------------------------------------
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

        # ----------------------------------------------------
        # 未分類本身不能刪除
        # ----------------------------------------------------
        if category_id == uncategorized_id:
            return False

        # ----------------------------------------------------
        # 原分類中的收藏 → 未分類
        # ----------------------------------------------------
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

        # ----------------------------------------------------
        # 刪除分類
        # ----------------------------------------------------
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

    # --------------------------------------------------------
    # 分類篩選
    # --------------------------------------------------------
    if category_id is not None:

        query += """
            AND l.category_id = ?
        """

        params.append(
            category_id
        )

    # --------------------------------------------------------
    # 關鍵字搜尋
    # --------------------------------------------------------
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
    example.com
        ↓
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
    簡單驗證網址。
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
    嘗試取得網頁 <title>。
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

            # 最多讀 300KB
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
    抓不到標題時，
    使用 domain 當標題。
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
# 9. 取得目前分類
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
# 10. 頁面切換
#
# 重點：
# 不再使用 st.tabs()
#
# page_selector 有固定 key，
# 所以按按鈕造成 rerun 時會保留目前頁面。
# ============================================================

active_page = st.segmented_control(
    "功能",
    options=[
        PAGE_ADD,
        PAGE_LIBRARY,
        PAGE_CATEGORIES
    ],
    selection_mode="single",
    default=PAGE_ADD,
    required=True,
    key="page_selector",
    label_visibility="collapsed",
    width="stretch"
)


# ============================================================
# 11. 一次性訊息
# ============================================================

if st.session_state.flash_message:

    st.success(
        st.session_state.flash_message
    )

    st.session_state.flash_message = None


# ============================================================
# PAGE 1：新增收藏
# ============================================================

if active_page == PAGE_ADD:

    st.markdown(
        "### 快速收藏"
    )

    with st.form(
        "add_link_form",
        clear_on_submit=True
    ):

        # ----------------------------------------------------
        # 標題
        # ----------------------------------------------------
        link_title = st.text_input(
            "標題",
            placeholder=(
                "可以不填，系統會自動取得"
            )
        )

        # ----------------------------------------------------
        # Link
        # ----------------------------------------------------
        link_url = st.text_input(
            "Link",
            placeholder="https://..."
        )

        # ----------------------------------------------------
        # 分類
        # ----------------------------------------------------
        selected_cat_name = st.selectbox(
            "分類",
            cat_names
        )

        # ----------------------------------------------------
        # 儲存
        # ----------------------------------------------------
        submitted = st.form_submit_button(
            "💾 儲存收藏",
            type="primary",
            use_container_width=True
        )

        if submitted:

            url = normalize_url(
                link_url
            )

            if not url:

                st.error(
                    "請輸入網址。"
                )

            elif not is_valid_url(url):

                st.error(
                    "網址格式似乎不正確。"
                )

            else:

                title = (
                    link_title.strip()
                )

                # ------------------------------------------------
                # 如果沒有輸入標題，自動抓網頁標題
                # ------------------------------------------------
                if not title:

                    with st.spinner(
                        "正在取得網頁標題..."
                    ):

                        title = (
                            get_page_title(url)
                        )

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
# PAGE 2：收藏庫
# ============================================================

elif active_page == PAGE_LIBRARY:

    st.markdown(
        "### 📚 收藏庫"
    )

    # --------------------------------------------------------
    # 搜尋
    # --------------------------------------------------------
    search_keyword = st.text_input(
        "搜尋收藏",
        placeholder=(
            "🔍 搜尋標題、網址、分類..."
        ),
        key="library_search"
    )

    # --------------------------------------------------------
    # 分類選項
    # --------------------------------------------------------
    library_options = (
        ["全部"]
        + cat_names
    )

    # --------------------------------------------------------
    # 如果之前選的分類已被刪除或改名，
    # 自動回到全部。
    #
    # 必須在建立 selectbox 前處理。
    # --------------------------------------------------------
    if (
        "library_filter"
        in st.session_state
        and
        st.session_state.library_filter
        not in library_options
    ):
        st.session_state.library_filter = "全部"

    # --------------------------------------------------------
    # 分類篩選
    # --------------------------------------------------------
    filter_cat = st.selectbox(
        "分類",
        library_options,
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
    # 沒有搜尋結果
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

        with st.container(
            border=True
        ):

            # =================================================
            # 編輯收藏模式
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
                    key=(
                        f"edit_title_"
                        f"{link_id}"
                    )
                )

                edit_url = st.text_input(
                    "Link",
                    value=item["url"],
                    key=(
                        f"edit_url_"
                        f"{link_id}"
                    )
                )

                # ------------------------------------------------
                # 找出目前分類 index
                # ------------------------------------------------
                try:

                    current_index = (
                        cat_names.index(
                            item[
                                "category_name"
                            ]
                        )
                    )

                except (
                    ValueError,
                    TypeError
                ):

                    current_index = (
                        cat_names.index(
                            "未分類"
                        )
                        if "未分類"
                        in cat_names
                        else 0
                    )

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
                    key=(
                        f"edit_note_"
                        f"{link_id}"
                    )
                )

                (
                    col_save,
                    col_cancel
                ) = st.columns(2)

                # ------------------------------------------------
                # 儲存修改
                # ------------------------------------------------
                with col_save:

                    if st.button(
                        "💾 儲存修改",
                        key=(
                            f"save_"
                            f"{link_id}"
                        ),
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
                            ] = (
                                "✅ 收藏已更新。"
                            )

                            st.rerun()

                # ------------------------------------------------
                # 取消修改
                # ------------------------------------------------
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
            # 一般收藏顯示
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

                # ------------------------------------------------
                # 備註
                # ------------------------------------------------
                if item["note"]:

                    st.write(
                        item["note"]
                    )

                # ------------------------------------------------
                # 操作按鈕
                # ------------------------------------------------
                (
                    col_open,
                    col_edit,
                    col_delete
                ) = st.columns(
                    [3, 1, 1]
                )

                # ------------------------------------------------
                # 開啟
                # ------------------------------------------------
                with col_open:

                    st.link_button(
                        "🔗 開啟連結",
                        item["url"],
                        use_container_width=True
                    )

                # ------------------------------------------------
                # 編輯
                # ------------------------------------------------
                with col_edit:

                    if st.button(
                        "✏️",
                        key=(
                            f"edit_"
                            f"{link_id}"
                        ),
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

                # ------------------------------------------------
                # 刪除
                # ------------------------------------------------
                with col_delete:

                    if st.button(
                        "🗑️",
                        key=(
                            f"delete_"
                            f"{link_id}"
                        ),
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

                # =================================================
                # 刪除收藏確認
                # =================================================
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

                    # ------------------------------------------------
                    # 確認刪除
                    # ------------------------------------------------
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
                            ] = (
                                "🗑️ 收藏已刪除。"
                            )

                            st.rerun()

                    # ------------------------------------------------
                    # 取消
                    # ------------------------------------------------
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
# PAGE 3：分類管理
# ============================================================

elif active_page == PAGE_CATEGORIES:

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

    # --------------------------------------------------------
    # 使用 Form + clear_on_submit
    #
    # 避免先前發生的：
    # StreamlitAPIException
    # --------------------------------------------------------
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

                st.session_state[
                    "flash_message"
                ] = (
                    f"✅ 已新增分類"
                    f"「{category_name}」。"
                )

                # ------------------------------------------------
                # 現在 rerun 後仍會停留在「分類」
                # ------------------------------------------------
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

    category_list = (
        fetch_categories_with_counts()
    )

    for category in category_list:

        category_id = (
            category["id"]
        )

        category_name = (
            category["name"]
        )

        link_count = (
            category["link_count"]
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

                # ------------------------------------------------
                # 儲存
                # ------------------------------------------------
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

                # ------------------------------------------------
                # 取消
                # ------------------------------------------------
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

                # ------------------------------------------------
                # 分類資訊
                # ------------------------------------------------
                with col_info:

                    st.markdown(
                        f"**🏷️ {category_name}**"
                    )

                    st.caption(
                        f"{link_count} 筆收藏"
                    )

                # ------------------------------------------------
                # 修改分類
                # ------------------------------------------------
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

                # ------------------------------------------------
                # 刪除分類
                # ------------------------------------------------
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

                    # ------------------------------------------------
                    # 確定刪除
                    # ------------------------------------------------
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

                    # ------------------------------------------------
                    # 取消
                    # ------------------------------------------------
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
