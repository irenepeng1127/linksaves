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
    page_title="Link Vault",
    page_icon="🔖",
    layout="centered",
    initial_sidebar_state="collapsed",
)

DB_NAME = "link_vault.db"

PAGE_ADD = "➕ 新增收藏"
PAGE_LIBRARY = "📚 收藏庫"
PAGE_CATEGORIES = "🏷️ 分類"


# ============================================================
# 2. 家庭使用者設定
# ============================================================

PROFILES = [
    {
        "slug": "honey",
        "name": "Honey",
    },
    {
        "slug": "eirene",
        "name": "Eirene",
    },
    {
        "slug": "tinney",
        "name": "Tinney",
    },
    {
        "slug": "lyris",
        "name": "Lyris",
    },
]


# ------------------------------------------------------------
# 舊版 link_vault.db 裡原本的資料
# 第一次升級時要歸到哪一個人
#
# 可改成：
# "honey"
# "eirene"
# "tinney"
# "lyris"
# ------------------------------------------------------------

LEGACY_PROFILE_SLUG = "eirene"


# ============================================================
# 3. 資料庫
# ============================================================

def get_db():
    conn = sqlite3.connect(
        DB_NAME,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    return conn


def table_exists(conn, table_name):
    result = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        AND name = ?
        """,
        (table_name,)
    ).fetchone()

    return result is not None


def get_meta(conn, key):
    result = conn.execute(
        """
        SELECT value
        FROM app_meta
        WHERE key = ?
        """,
        (key,)
    ).fetchone()

    if result:
        return result["value"]

    return None


def set_meta(conn, key, value):
    conn.execute(
        """
        INSERT INTO app_meta (
            key,
            value
        )
        VALUES (?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value
        """,
        (
            key,
            value
        )
    )


def init_db():

    with get_db() as conn:

        cursor = conn.cursor()

        # ====================================================
        # App metadata
        # ====================================================

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )


        # ====================================================
        # Profiles
        # ====================================================

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL
            )
            """
        )


        # ====================================================
        # 新版分類
        #
        # 注意：
        # 每一個 profile 都可以有自己的「旅遊」、
        # 「工作」等同名分類。
        # ====================================================

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                name TEXT COLLATE NOCASE NOT NULL,

                UNIQUE (
                    profile_id,
                    name
                ),

                FOREIGN KEY (profile_id)
                    REFERENCES profiles(id)
                    ON DELETE CASCADE
            )
            """
        )


        # ====================================================
        # 新版收藏
        # ====================================================

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                category_id INTEGER,
                note TEXT,
                created_at TEXT,

                FOREIGN KEY (profile_id)
                    REFERENCES profiles(id)
                    ON DELETE CASCADE,

                FOREIGN KEY (category_id)
                    REFERENCES profile_categories(id)
            )
            """
        )


        # ====================================================
        # 建立四位使用者
        # ====================================================

        for profile in PROFILES:

            cursor.execute(
                """
                INSERT OR IGNORE INTO profiles (
                    slug,
                    name
                )
                VALUES (?, ?)
                """,
                (
                    profile["slug"],
                    profile["name"]
                )
            )


        # ====================================================
        # 每個人至少都要有「未分類」
        # ====================================================

        db_profiles = cursor.execute(
            """
            SELECT *
            FROM profiles
            """
        ).fetchall()

        for profile in db_profiles:

            cursor.execute(
                """
                INSERT OR IGNORE INTO profile_categories (
                    profile_id,
                    name
                )
                VALUES (?, '未分類')
                """,
                (
                    profile["id"],
                )
            )


        # ====================================================
        # 舊版資料遷移
        #
        # 舊版：
        # categories
        # links
        #
        # 新版：
        # profile_categories
        # profile_links
        # ====================================================

        migrated = get_meta(
            conn,
            "legacy_migrated"
        )

        if migrated != "1":

            legacy_profile = cursor.execute(
                """
                SELECT *
                FROM profiles
                WHERE slug = ?
                """,
                (
                    LEGACY_PROFILE_SLUG,
                )
            ).fetchone()

            if legacy_profile:

                legacy_profile_id = (
                    legacy_profile["id"]
                )

                category_map = {}


                # --------------------------------------------
                # 搬舊分類
                # --------------------------------------------

                if table_exists(
                    conn,
                    "categories"
                ):

                    old_categories = (
                        cursor.execute(
                            """
                            SELECT *
                            FROM categories
                            """
                        ).fetchall()
                    )

                    for old_category in old_categories:

                        cursor.execute(
                            """
                            INSERT OR IGNORE
                            INTO profile_categories (
                                profile_id,
                                name
                            )
                            VALUES (?, ?)
                            """,
                            (
                                legacy_profile_id,
                                old_category["name"]
                            )
                        )

                        new_category = (
                            cursor.execute(
                                """
                                SELECT id
                                FROM profile_categories
                                WHERE profile_id = ?
                                AND name = ?
                                """,
                                (
                                    legacy_profile_id,
                                    old_category["name"]
                                )
                            ).fetchone()
                        )

                        if new_category:

                            category_map[
                                old_category["id"]
                            ] = (
                                new_category["id"]
                            )


                # --------------------------------------------
                # 找到新版未分類 ID
                # --------------------------------------------

                uncategorized = (
                    cursor.execute(
                        """
                        SELECT id
                        FROM profile_categories
                        WHERE profile_id = ?
                        AND name = '未分類'
                        """,
                        (
                            legacy_profile_id,
                        )
                    ).fetchone()
                )

                uncategorized_id = (
                    uncategorized["id"]
                    if uncategorized
                    else None
                )


                # --------------------------------------------
                # 搬舊收藏
                # --------------------------------------------

                if table_exists(
                    conn,
                    "links"
                ):

                    old_links = (
                        cursor.execute(
                            """
                            SELECT *
                            FROM links
                            ORDER BY id
                            """
                        ).fetchall()
                    )

                    for old_link in old_links:

                        old_category_id = (
                            old_link["category_id"]
                        )

                        new_category_id = (
                            category_map.get(
                                old_category_id,
                                uncategorized_id
                            )
                        )

                        cursor.execute(
                            """
                            INSERT INTO profile_links (
                                profile_id,
                                title,
                                url,
                                category_id,
                                note,
                                created_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                legacy_profile_id,
                                old_link["title"],
                                old_link["url"],
                                new_category_id,
                                old_link["note"],
                                old_link["created_at"]
                            )
                        )


            # --------------------------------------------
            # 防止下一次又重複搬資料
            # --------------------------------------------

            set_meta(
                conn,
                "legacy_migrated",
                "1"
            )

        conn.commit()


init_db()


# ============================================================
# 4. Profile 功能
# ============================================================

def get_profile_by_slug(slug):

    if not slug:
        return None

    with get_db() as conn:

        return conn.execute(
            """
            SELECT *
            FROM profiles
            WHERE LOWER(slug) = LOWER(?)
            """,
            (
                slug.strip(),
            )
        ).fetchone()


# ============================================================
# 5. 分類功能
# ============================================================

def fetch_categories(profile_id):

    with get_db() as conn:

        return conn.execute(
            """
            SELECT *
            FROM profile_categories
            WHERE profile_id = ?

            ORDER BY
                CASE
                    WHEN name = '未分類'
                    THEN 1
                    ELSE 0
                END,

                name COLLATE NOCASE
            """,
            (
                profile_id,
            )
        ).fetchall()


def fetch_categories_with_counts(
    profile_id
):

    with get_db() as conn:

        return conn.execute(
            """
            SELECT
                c.id,
                c.name,
                COUNT(l.id) AS link_count

            FROM profile_categories c

            LEFT JOIN profile_links l
                ON l.category_id = c.id
                AND l.profile_id = c.profile_id

            WHERE c.profile_id = ?

            GROUP BY
                c.id,
                c.name

            ORDER BY
                CASE
                    WHEN c.name = '未分類'
                    THEN 1
                    ELSE 0
                END,

                c.name COLLATE NOCASE
            """,
            (
                profile_id,
            )
        ).fetchall()


def add_category(
    profile_id,
    name
):

    name = name.strip()

    if not name:
        return False

    with get_db() as conn:

        try:

            conn.execute(
                """
                INSERT INTO profile_categories (
                    profile_id,
                    name
                )
                VALUES (?, ?)
                """,
                (
                    profile_id,
                    name
                )
            )

            conn.commit()

            return True

        except sqlite3.IntegrityError:

            return False


def rename_category(
    profile_id,
    category_id,
    new_name
):

    new_name = new_name.strip()

    if not new_name:

        return (
            False,
            "分類名稱不能為空白。"
        )

    with get_db() as conn:

        cursor = conn.cursor()

        category = cursor.execute(
            """
            SELECT *
            FROM profile_categories

            WHERE id = ?
            AND profile_id = ?
            """,
            (
                category_id,
                profile_id
            )
        ).fetchone()

        if not category:

            return (
                False,
                "找不到這個分類。"
            )

        if category["name"] == "未分類":

            return (
                False,
                "「未分類」不能重新命名。"
            )

        try:

            cursor.execute(
                """
                UPDATE profile_categories

                SET name = ?

                WHERE id = ?
                AND profile_id = ?
                """,
                (
                    new_name,
                    category_id,
                    profile_id
                )
            )

            conn.commit()

            return (
                True,
                None
            )

        except sqlite3.IntegrityError:

            return (
                False,
                "這個分類名稱已經存在。"
            )


def delete_category(
    profile_id,
    category_id
):

    with get_db() as conn:

        cursor = conn.cursor()

        # ----------------------------------------------------
        # 此使用者的未分類
        # ----------------------------------------------------

        uncategorized = (
            cursor.execute(
                """
                SELECT id
                FROM profile_categories

                WHERE profile_id = ?
                AND name = '未分類'
                """,
                (
                    profile_id,
                )
            ).fetchone()
        )

        if not uncategorized:
            return False

        uncategorized_id = (
            uncategorized["id"]
        )

        # ----------------------------------------------------
        # 未分類不能刪除
        # ----------------------------------------------------

        if category_id == uncategorized_id:
            return False


        # ----------------------------------------------------
        # 收藏搬到未分類
        # ----------------------------------------------------

        cursor.execute(
            """
            UPDATE profile_links

            SET category_id = ?

            WHERE profile_id = ?
            AND category_id = ?
            """,
            (
                uncategorized_id,
                profile_id,
                category_id
            )
        )


        # ----------------------------------------------------
        # 刪除分類
        # ----------------------------------------------------

        cursor.execute(
            """
            DELETE FROM profile_categories

            WHERE id = ?
            AND profile_id = ?
            """,
            (
                category_id,
                profile_id
            )
        )

        conn.commit()

        return True


# ============================================================
# 6. 收藏功能
# ============================================================

def add_link(
    profile_id,
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
            INSERT INTO profile_links (
                profile_id,
                title,
                url,
                category_id,
                note,
                created_at
            )

            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                title.strip(),
                url.strip(),
                category_id,
                note.strip(),
                now
            )
        )

        conn.commit()


def update_link(
    profile_id,
    link_id,
    title,
    url,
    category_id,
    note=""
):

    with get_db() as conn:

        conn.execute(
            """
            UPDATE profile_links

            SET
                title = ?,
                url = ?,
                category_id = ?,
                note = ?

            WHERE id = ?
            AND profile_id = ?
            """,
            (
                title.strip(),
                url.strip(),
                category_id,
                note.strip(),
                link_id,
                profile_id
            )
        )

        conn.commit()


def delete_link(
    profile_id,
    link_id
):

    with get_db() as conn:

        conn.execute(
            """
            DELETE FROM profile_links

            WHERE id = ?
            AND profile_id = ?
            """,
            (
                link_id,
                profile_id
            )
        )

        conn.commit()


def fetch_links(
    profile_id,
    category_id=None,
    keyword=""
):

    query = """
        SELECT
            l.*,
            c.name AS category_name

        FROM profile_links l

        LEFT JOIN profile_categories c
            ON l.category_id = c.id

        WHERE l.profile_id = ?
    """

    params = [
        profile_id
    ]


    # --------------------------------------------------------
    # 分類
    # --------------------------------------------------------

    if category_id is not None:

        query += """
            AND l.category_id = ?
        """

        params.append(
            category_id
        )


    # --------------------------------------------------------
    # 搜尋
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
# 7. URL 功能
# ============================================================

def normalize_url(url):

    url = url.strip()

    if not url:
        return ""

    if not url.startswith(
        (
            "http://",
            "https://"
        )
    ):

        url = (
            "https://"
            + url
        )

    return url


def is_valid_url(url):

    try:

        parsed = urlparse(
            url
        )

        return (
            parsed.scheme
            in (
                "http",
                "https"
            )
            and bool(
                parsed.netloc
            )
        )

    except Exception:

        return False


# ============================================================
# 8. 自動讀網頁標題
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

        parser.feed(
            text
        )

        title = (
            parser.title.strip()
        )

        title = " ".join(
            title.split()
        )

        if title:
            return title

    except Exception:
        pass

    return None


def fallback_title(url):

    try:

        parsed = urlparse(
            url
        )

        domain = parsed.netloc

        if domain.startswith(
            "www."
        ):

            domain = domain[4:]

        if domain:
            return domain

    except Exception:
        pass

    return "未命名收藏"


# ============================================================
# 9. 取得 URL 中的 profile
# ============================================================

profile_slug = st.query_params.get(
    "profile",
    ""
)

# 某些版本可能回傳 list
if isinstance(
    profile_slug,
    list
):

    profile_slug = (
        profile_slug[0]
        if profile_slug
        else ""
    )

profile_slug = (
    profile_slug
    .strip()
    .lower()
)

profile = get_profile_by_slug(
    profile_slug
)


# ============================================================
# 10. 首頁
#
# 如果沒有指定 Profile，
# 首頁只顯示四張卡片。
# ============================================================

if not profile:

    # --------------------------------------------------------
    # 首頁 CSS
    # --------------------------------------------------------

    st.markdown(
        """
        <style>

        /* 整個首頁背景 */
        .stApp {
            background-color: #212121;
        }

        /* 隱藏 Streamlit 上方 UI */
        [data-testid="stHeader"] {
            display: none;
        }

        [data-testid="stToolbar"] {
            display: none;
        }

        [data-testid="stDecoration"] {
            display: none;
        }

        #MainMenu {
            visibility: hidden;
        }

        footer {
            visibility: hidden;
        }

        /* 首頁不要留多餘空白 */
        .block-container {
            padding-top: 0 !important;
            padding-bottom: 0 !important;
            max-width: 1200px;
        }

        /* 垂直置中 */
        .profile-wrapper {
            min-height: 100vh;

            display: flex;
            align-items: center;
            justify-content: center;

            padding: 20px;
            box-sizing: border-box;
        }

        /* 固定 1 x 4 */
        .profile-grid {
            width: 100%;

            display: grid;

            grid-template-columns:
                repeat(4, minmax(0, 1fr));

            gap: 14px;
        }

        /* Profile 卡片 */
        .profile-card {
            min-height: 190px;

            background-color: #B196E4;

            border-radius: 22px;

            display: flex;
            align-items: center;
            justify-content: center;

            text-align: center;

            color: #212121 !important;

            font-size:
                clamp(
                    17px,
                    2.5vw,
                    28px
                );

            font-weight: 700;

            text-decoration: none !important;

            transition:
                transform 0.15s ease,
                background-color 0.15s ease,
                box-shadow 0.15s ease;

            box-shadow:
                0 8px 24px
                rgba(0, 0, 0, 0.20);

            -webkit-tap-highlight-color:
                transparent;
        }

        /* 滑鼠移上去 */
        .profile-card:hover {
            background-color: #BDA6E8;

            transform:
                translateY(-3px);

            box-shadow:
                0 12px 30px
                rgba(0, 0, 0, 0.28);
        }

        /* 手機按下去 */
        .profile-card:active {
            transform:
                scale(0.97);
        }

        /* 小螢幕 */
        @media (
            max-width: 600px
        ) {

            .profile-wrapper {
                padding:
                    12px 8px;
            }

            .profile-grid {
                gap: 8px;
            }

            .profile-card {
                min-height: 165px;
                border-radius: 18px;
            }

        }

        </style>
        """,
        unsafe_allow_html=True
    )


    # --------------------------------------------------------
    # 四張卡片
    # --------------------------------------------------------

    st.markdown(
        """
        <div class="profile-wrapper">

            <div class="profile-grid">

                <a
                    class="profile-card"
                    href="?profile=honey"
                    target="_self"
                >
                    Honey
                </a>

                <a
                    class="profile-card"
                    href="?profile=eirene"
                    target="_self"
                >
                    Eirene
                </a>

                <a
                    class="profile-card"
                    href="?profile=tinney"
                    target="_self"
                >
                    Tinney
                </a>

                <a
                    class="profile-card"
                    href="?profile=lyris"
                    target="_self"
                >
                    Lyris
                </a>

            </div>

        </div>
        """,
        unsafe_allow_html=True
    )

    # --------------------------------------------------------
    # 首頁到此為止
    # --------------------------------------------------------

    st.stop()


# ============================================================
# 11. 進入 Profile 後
# ============================================================

profile_id = profile["id"]
profile_name = profile["name"]


# ============================================================
# 12. 切換 Profile 時清除暫存操作狀態
# ============================================================

if (
    st.session_state.get(
        "current_profile_slug"
    )
    != profile_slug
):

    st.session_state[
        "current_profile_slug"
    ] = profile_slug

    st.session_state[
        "editing_link_id"
    ] = None

    st.session_state[
        "delete_link_id"
    ] = None

    st.session_state[
        "editing_category_id"
    ] = None

    st.session_state[
        "delete_category_id"
    ] = None

    st.session_state[
        "flash_message"
    ] = None


# ============================================================
# 13. Session State
# ============================================================

if "editing_link_id" not in st.session_state:

    st.session_state[
        "editing_link_id"
    ] = None


if "delete_link_id" not in st.session_state:

    st.session_state[
        "delete_link_id"
    ] = None


if "editing_category_id" not in st.session_state:

    st.session_state[
        "editing_category_id"
    ] = None


if "delete_category_id" not in st.session_state:

    st.session_state[
        "delete_category_id"
    ] = None


if "flash_message" not in st.session_state:

    st.session_state[
        "flash_message"
    ] = None


# ============================================================
# 14. Profile 頂部
# ============================================================

header_left, header_right = (
    st.columns(
        [4, 1]
    )
)

with header_left:

    st.title(
        f"🔖 {profile_name}"
    )

with header_right:

    if st.button(
        "← 首頁",
        use_container_width=True,
        key=f"home_{profile_slug}"
    ):

        st.query_params.clear()

        st.rerun()


# ============================================================
# 15. 取得此人的分類
# ============================================================

categories = fetch_categories(
    profile_id
)

cat_dict = {
    category["name"]:
    category["id"]

    for category
    in categories
}

cat_names = list(
    cat_dict.keys()
)


# ============================================================
# 16. 頁面切換
# ============================================================

page_selector_key = (
    f"page_selector_"
    f"{profile_slug}"
)

active_page = st.segmented_control(
    "功能",
    options=[
        PAGE_ADD,
        PAGE_LIBRARY,
        PAGE_CATEGORIES
    ],
    default=PAGE_ADD,
    selection_mode="single",
    key=page_selector_key,
    label_visibility="collapsed"
)

if not active_page:
    active_page = PAGE_ADD


# ============================================================
# 17. 一次性訊息
# ============================================================

if st.session_state[
    "flash_message"
]:

    st.success(
        st.session_state[
            "flash_message"
        ]
    )

    st.session_state[
        "flash_message"
    ] = None


# ============================================================
# PAGE 1：新增收藏
# ============================================================

if active_page == PAGE_ADD:

    st.markdown(
        "### 快速收藏"
    )

    with st.form(
        f"add_link_form_{profile_slug}",
        clear_on_submit=True
    ):

        # ----------------------------------------------------
        # 標題
        # ----------------------------------------------------

        link_title = st.text_input(
            "標題",
            placeholder=(
                "可以不填，"
                "系統會自動取得"
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

        selected_cat_name = (
            st.selectbox(
                "分類",
                cat_names
            )
        )


        # ----------------------------------------------------
        # 儲存
        # ----------------------------------------------------

        submitted = (
            st.form_submit_button(
                "💾 儲存收藏",
                type="primary",
                use_container_width=True
            )
        )


        if submitted:

            url = normalize_url(
                link_url
            )

            if not url:

                st.error(
                    "請輸入網址。"
                )

            elif not is_valid_url(
                url
            ):

                st.error(
                    "網址格式似乎不正確。"
                )

            else:

                title = (
                    link_title.strip()
                )

                # --------------------------------------------
                # 沒標題 → 自動抓
                # --------------------------------------------

                if not title:

                    with st.spinner(
                        "正在取得網頁標題..."
                    ):

                        title = (
                            get_page_title(
                                url
                            )
                        )

                    if not title:

                        title = (
                            fallback_title(
                                url
                            )
                        )


                add_link(
                    profile_id=profile_id,
                    title=title,
                    url=url,
                    category_id=(
                        cat_dict[
                            selected_cat_name
                        ]
                    ),
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

    search_keyword = (
        st.text_input(
            "搜尋收藏",
            placeholder=(
                "🔍 搜尋標題、網址、分類..."
            ),
            key=(
                f"library_search_"
                f"{profile_slug}"
            )
        )
    )


    # --------------------------------------------------------
    # 分類
    # --------------------------------------------------------

    library_options = (
        ["全部"]
        + cat_names
    )

    library_filter_key = (
        f"library_filter_"
        f"{profile_slug}"
    )


    # 分類被刪除或重新命名時避免錯誤
    if (
        library_filter_key
        in st.session_state
    ):

        if (
            st.session_state[
                library_filter_key
            ]
            not in library_options
        ):

            st.session_state[
                library_filter_key
            ] = "全部"


    filter_cat = st.selectbox(
        "分類",
        library_options,
        key=library_filter_key
    )


    current_cat_id = (
        None

        if filter_cat == "全部"

        else cat_dict[
            filter_cat
        ]
    )


    links = fetch_links(
        profile_id=profile_id,
        category_id=current_cat_id,
        keyword=search_keyword
    )


    st.caption(
        f"目前共有 {len(links)} 筆收藏"
    )


    if not links:

        st.info(
            "目前沒有符合條件的收藏。"
        )


    # --------------------------------------------------------
    # 收藏卡片
    # --------------------------------------------------------

    for item in links:

        link_id = (
            item["id"]
        )


        with st.container(
            border=True
        ):


            # =================================================
            # 編輯模式
            # =================================================

            if (
                st.session_state[
                    "editing_link_id"
                ]
                == link_id
            ):

                st.markdown(
                    "#### ✏️ 編輯收藏"
                )


                edit_title = (
                    st.text_input(
                        "標題",
                        value=item[
                            "title"
                        ],
                        key=(
                            f"edit_title_"
                            f"{profile_slug}_"
                            f"{link_id}"
                        )
                    )
                )


                edit_url = (
                    st.text_input(
                        "Link",
                        value=item[
                            "url"
                        ],
                        key=(
                            f"edit_url_"
                            f"{profile_slug}_"
                            f"{link_id}"
                        )
                    )
                )


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


                edit_category = (
                    st.selectbox(
                        "分類",
                        cat_names,
                        index=current_index,
                        key=(
                            f"edit_category_"
                            f"{profile_slug}_"
                            f"{link_id}"
                        )
                    )
                )


                edit_note = (
                    st.text_area(
                        "備註",
                        value=(
                            item["note"]
                            or ""
                        ),
                        placeholder="可選填",
                        key=(
                            f"edit_note_"
                            f"{profile_slug}_"
                            f"{link_id}"
                        )
                    )
                )


                col_save, col_cancel = (
                    st.columns(2)
                )


                # --------------------------------------------
                # 儲存
                # --------------------------------------------

                with col_save:

                    if st.button(
                        "💾 儲存修改",
                        key=(
                            f"save_"
                            f"{profile_slug}_"
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
                                profile_id=profile_id,
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


                # --------------------------------------------
                # 取消
                # --------------------------------------------

                with col_cancel:

                    if st.button(
                        "取消",
                        key=(
                            f"cancel_edit_"
                            f"{profile_slug}_"
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


                if item["note"]:

                    st.write(
                        item["note"]
                    )


                (
                    col_open,
                    col_edit,
                    col_delete
                ) = st.columns(
                    [3, 1, 1]
                )


                # --------------------------------------------
                # 開啟
                # --------------------------------------------

                with col_open:

                    st.link_button(
                        "🔗 開啟連結",
                        item["url"],
                        use_container_width=True
                    )


                # --------------------------------------------
                # 編輯
                # --------------------------------------------

                with col_edit:

                    if st.button(
                        "✏️",
                        key=(
                            f"edit_"
                            f"{profile_slug}_"
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


                # --------------------------------------------
                # 刪除
                # --------------------------------------------

                with col_delete:

                    if st.button(
                        "🗑️",
                        key=(
                            f"delete_"
                            f"{profile_slug}_"
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
                # 刪除確認
                # =================================================

                if (
                    st.session_state[
                        "delete_link_id"
                    ]
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
                                f"{profile_slug}_"
                                f"{link_id}"
                            ),
                            type="primary",
                            use_container_width=True
                        ):

                            delete_link(
                                profile_id,
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


                    with cancel_col:

                        if st.button(
                            "取消",
                            key=(
                                f"cancel_delete_"
                                f"{profile_slug}_"
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


    with st.form(
        f"add_category_form_{profile_slug}",
        clear_on_submit=True
    ):

        new_cat_name = (
            st.text_input(
                "分類名稱",
                placeholder=(
                    "例如：料理、投資、AI 工具"
                )
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
                profile_id,
                category_name
            ):

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


    category_list = (
        fetch_categories_with_counts(
            profile_id
        )
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
            # 編輯模式
            # =================================================

            elif (
                st.session_state[
                    "editing_category_id"
                ]
                == category_id
            ):

                st.markdown(
                    f"**✏️ 編輯「{category_name}」**"
                )


                new_name = (
                    st.text_input(
                        "新的分類名稱",
                        value=category_name,
                        key=(
                            f"rename_category_"
                            f"{profile_slug}_"
                            f"{category_id}"
                        )
                    )
                )


                (
                    col_save,
                    col_cancel
                ) = st.columns(2)


                # --------------------------------------------
                # 儲存
                # --------------------------------------------

                with col_save:

                    if st.button(
                        "儲存",
                        type="primary",
                        use_container_width=True,
                        key=(
                            f"save_category_"
                            f"{profile_slug}_"
                            f"{category_id}"
                        )
                    ):

                        success, error = (
                            rename_category(
                                profile_id,
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


                # --------------------------------------------
                # 取消
                # --------------------------------------------

                with col_cancel:

                    if st.button(
                        "取消",
                        use_container_width=True,
                        key=(
                            f"cancel_category_edit_"
                            f"{profile_slug}_"
                            f"{category_id}"
                        )
                    ):

                        st.session_state[
                            "editing_category_id"
                        ] = None

                        st.rerun()


            # =================================================
            # 一般分類
            # =================================================

            else:

                (
                    col_info,
                    col_edit,
                    col_delete
                ) = st.columns(
                    [5, 1, 1]
                )


                with col_info:

                    st.markdown(
                        f"**🏷️ {category_name}**"
                    )

                    st.caption(
                        f"{link_count} 筆收藏"
                    )


                # --------------------------------------------
                # 編輯
                # --------------------------------------------

                with col_edit:

                    if st.button(
                        "✏️",
                        key=(
                            f"edit_category_"
                            f"{profile_slug}_"
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


                # --------------------------------------------
                # 刪除
                # --------------------------------------------

                with col_delete:

                    if st.button(
                        "🗑️",
                        key=(
                            f"delete_category_"
                            f"{profile_slug}_"
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
                # 刪除確認
                # =================================================

                if (
                    st.session_state[
                        "delete_category_id"
                    ]
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


                    # ----------------------------------------
                    # 確認刪除
                    # ----------------------------------------

                    with col_confirm:

                        if st.button(
                            "確定刪除",
                            type="primary",
                            use_container_width=True,
                            key=(
                                f"confirm_category_"
                                f"{profile_slug}_"
                                f"{category_id}"
                            )
                        ):

                            success = (
                                delete_category(
                                    profile_id,
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


                    # ----------------------------------------
                    # 取消
                    # ----------------------------------------

                    with col_cancel:

                        if st.button(
                            "取消",
                            use_container_width=True,
                            key=(
                                f"cancel_category_"
                                f"{profile_slug}_"
                                f"{category_id}"
                            )
                        ):

                            st.session_state[
                                "delete_category_id"
                            ] = None

                            st.rerun()
