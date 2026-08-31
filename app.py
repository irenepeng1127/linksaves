import sqlite3
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import streamlit as st
from supabase import Client, create_client


# ============================================================
# 1. Streamlit 基本設定
# ============================================================

st.set_page_config(
    page_title="Link Vault",
    page_icon="🔗",
    layout="centered",
    initial_sidebar_state="collapsed",
)

PAGE_ADD = "➕ 新增收藏"
PAGE_LIBRARY = "📚 收藏庫"
PAGE_CATEGORIES = "🏷️ 分類"


# ============================================================
# 2. 家庭成員
# ============================================================

PROFILES = [
    {"slug": "honey", "name": "Honey"},
    {"slug": "eirene", "name": "Eirene"},
    {"slug": "tinney", "name": "Tinney"},
    {"slug": "lyris", "name": "Lyris"},
]


# ============================================================
# 3. Supabase 連線
#
# Streamlit Cloud → App settings → Secrets：
#
# [supabase]
# url = "https://xxxx.supabase.co"
# key = "sb_secret_xxxxxxxxx"
#
# 不要把 Secret Key 直接寫進程式或 GitHub。
# ============================================================

@st.cache_resource
def get_db() -> Client:

    try:
        supabase_url = st.secrets["supabase"]["url"]
        supabase_key = st.secrets["supabase"]["key"]

    except Exception:

        st.error(
            "尚未設定 Supabase 連線。\n\n"
            "請到 Streamlit App settings → Secrets 加入：\n\n"
            '[supabase]\n'
            'url = "https://你的專案.supabase.co"\n'
            'key = "sb_secret_..."'
        )

        st.stop()

    return create_client(
        supabase_url,
        supabase_key
    )


# ============================================================
# 4. Supabase 小工具
# ============================================================

def get_meta(key):

    db = get_db()

    response = (
        db.table("app_meta")
        .select("value")
        .eq("key", key)
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if rows:
        return rows[0]["value"]

    return None


def set_meta(
    key,
    value
):

    db = get_db()

    (
        db.table("app_meta")
        .upsert(
            {
                "key": key,
                "value": value,
            },
            on_conflict="key",
        )
        .execute()
    )


def find_profile_by_slug(slug):

    if not slug:
        return None

    db = get_db()

    response = (
        db.table("profiles")
        .select("*")
        .eq("slug", slug.strip().lower())
        .limit(1)
        .execute()
    )

    rows = response.data or []

    return rows[0] if rows else None


def get_profile_by_slug(slug):

    return find_profile_by_slug(slug)


def _find_category_by_name(
    profile_id,
    name
):

    db = get_db()

    response = (
        db.table("profile_categories")
        .select("*")
        .eq("profile_id", profile_id)
        .execute()
    )

    wanted = name.strip().casefold()

    for row in (response.data or []):

        if (
            str(row.get("name", ""))
            .strip()
            .casefold()
            == wanted
        ):
            return row

    return None


def _ensure_category(
    profile_id,
    name
):

    existing = _find_category_by_name(
        profile_id,
        name
    )

    if existing:
        return existing["id"]

    db = get_db()

    response = (
        db.table("profile_categories")
        .insert(
            {
                "profile_id": profile_id,
                "name": name.strip(),
            }
        )
        .execute()
    )

    rows = response.data or []

    if rows:
        return rows[0]["id"]

    # 極少數情況下，如果另一個 request 同時建立，
    # 再讀一次即可。
    existing = _find_category_by_name(
        profile_id,
        name
    )

    if existing:
        return existing["id"]

    raise RuntimeError(
        f"無法建立分類：{name}"
    )


# ============================================================
# 5. 初始化 Supabase 基本資料
#
# 資料表本身請先執行我附上的 supabase_setup.sql。
# 這裡只負責建立固定四位 Profile 與各自的「未分類」。
# ============================================================

def init_db():

    db = get_db()

    try:

        for profile in PROFILES:

            (
                db.table("profiles")
                .upsert(
                    {
                        "slug": profile["slug"],
                        "name": profile["name"],
                    },
                    on_conflict="slug",
                )
                .execute()
            )

        response = (
            db.table("profiles")
            .select("id,slug,name")
            .execute()
        )

        for profile in (response.data or []):

            if profile["slug"] in {
                "honey",
                "eirene",
                "tinney",
                "lyris",
            }:

                _ensure_category(
                    profile["id"],
                    "未分類"
                )

    except Exception as exc:

        st.error(
            "Supabase 已連線，但資料表尚未建立或權限設定不完整。\n\n"
            "請先到 Supabase → SQL Editor 執行我附上的 "
            "`supabase_setup.sql`。"
        )

        st.code(
            str(exc)
        )

        st.stop()


init_db()


# ============================================================
# 6. 一次性：如果目前環境還找得到舊 link_vault.db，
#    自動把 SQLite 資料搬到 Supabase。
#
# 注意：
# - 這只是「搶救／搬家」功能。
# - 搬完後，所有新增／修改／刪除都只寫 Supabase。
# - 舊單人版 categories / links 會歸到 Eirene。
# ============================================================

LOCAL_SQLITE_PATH = Path("link_vault.db")


def sqlite_table_exists(
    conn,
    table_name
):

    row = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        AND name = ?
        """,
        (table_name,)
    ).fetchone()

    return row is not None


def _remote_link_exists(
    profile_id,
    title,
    url,
    created_at
):

    db = get_db()

    query = (
        db.table("profile_links")
        .select("id")
        .eq("profile_id", profile_id)
        .eq("title", title)
        .eq("url", url)
        .eq("created_at", created_at or "")
        .limit(1)
    )

    response = query.execute()

    return bool(
        response.data
    )


def _insert_remote_link_if_missing(
    profile_id,
    title,
    url,
    category_id,
    note,
    created_at
):

    created_at = (
        created_at
        or ""
    )

    if _remote_link_exists(
        profile_id,
        title,
        url,
        created_at
    ):
        return

    db = get_db()

    (
        db.table("profile_links")
        .insert(
            {
                "profile_id": profile_id,
                "title": title or "未命名收藏",
                "url": url or "",
                "category_id": category_id,
                "note": note or "",
                "created_at": created_at,
            }
        )
        .execute()
    )


def migrate_local_sqlite_to_supabase():

    # 已搬過就不再重複
    if get_meta(
        "sqlite_import_v1"
    ) == "1":
        return

    # 目前環境沒有舊 SQLite，就先略過。
    # 不寫入完成標記，避免日後有 DB 檔時失去搬家機會。
    if not LOCAL_SQLITE_PATH.exists():
        return

    try:

        local = sqlite3.connect(
            LOCAL_SQLITE_PATH
        )

        local.row_factory = sqlite3.Row

        # ----------------------------------------------------
        # A. 新版多 Profile SQLite
        # ----------------------------------------------------

        if (
            sqlite_table_exists(
                local,
                "profiles"
            )
            and sqlite_table_exists(
                local,
                "profile_categories"
            )
        ):

            local_profiles = local.execute(
                """
                SELECT *
                FROM profiles
                """
            ).fetchall()

            profile_id_map = {}

            for local_profile in local_profiles:

                slug = (
                    local_profile["slug"]
                    or ""
                ).strip().lower()

                name = (
                    local_profile["name"]
                    or slug
                    or "User"
                )

                if not slug:
                    continue

                db = get_db()

                (
                    db.table("profiles")
                    .upsert(
                        {
                            "slug": slug,
                            "name": name,
                        },
                        on_conflict="slug",
                    )
                    .execute()
                )

                remote_profile = (
                    find_profile_by_slug(
                        slug
                    )
                )

                if remote_profile:

                    profile_id_map[
                        local_profile["id"]
                    ] = remote_profile["id"]

            category_id_map = {}

            local_categories = local.execute(
                """
                SELECT *
                FROM profile_categories
                ORDER BY id
                """
            ).fetchall()

            for local_category in local_categories:

                remote_profile_id = (
                    profile_id_map.get(
                        local_category["profile_id"]
                    )
                )

                if not remote_profile_id:
                    continue

                remote_category_id = (
                    _ensure_category(
                        remote_profile_id,
                        local_category["name"]
                        or "未分類"
                    )
                )

                category_id_map[
                    local_category["id"]
                ] = remote_category_id

            if sqlite_table_exists(
                local,
                "profile_links"
            ):

                local_links = local.execute(
                    """
                    SELECT *
                    FROM profile_links
                    ORDER BY id
                    """
                ).fetchall()

                for local_link in local_links:

                    remote_profile_id = (
                        profile_id_map.get(
                            local_link["profile_id"]
                        )
                    )

                    if not remote_profile_id:
                        continue

                    category_id = (
                        category_id_map.get(
                            local_link["category_id"]
                        )
                    )

                    if not category_id:

                        category_id = (
                            _ensure_category(
                                remote_profile_id,
                                "未分類"
                            )
                        )

                    _insert_remote_link_if_missing(
                        profile_id=remote_profile_id,
                        title=(
                            local_link["title"]
                            or "未命名收藏"
                        ),
                        url=(
                            local_link["url"]
                            or ""
                        ),
                        category_id=category_id,
                        note=(
                            local_link["note"]
                            or ""
                        ),
                        created_at=(
                            local_link["created_at"]
                            or ""
                        ),
                    )

        # ----------------------------------------------------
        # B. 更舊的單人 SQLite
        #    統一匯入 Eirene
        # ----------------------------------------------------

        if sqlite_table_exists(
            local,
            "links"
        ):

            eirene = find_profile_by_slug(
                "eirene"
            )

            if eirene:

                eirene_id = eirene["id"]

                legacy_category_map = {}

                if sqlite_table_exists(
                    local,
                    "categories"
                ):

                    legacy_categories = (
                        local.execute(
                            """
                            SELECT *
                            FROM categories
                            ORDER BY id
                            """
                        ).fetchall()
                    )

                    for category in legacy_categories:

                        remote_category_id = (
                            _ensure_category(
                                eirene_id,
                                category["name"]
                                or "未分類"
                            )
                        )

                        legacy_category_map[
                            category["id"]
                        ] = remote_category_id

                legacy_links = local.execute(
                    """
                    SELECT *
                    FROM links
                    ORDER BY id
                    """
                ).fetchall()

                for link in legacy_links:

                    category_id = (
                        legacy_category_map.get(
                            link["category_id"]
                        )
                    )

                    if not category_id:

                        category_id = (
                            _ensure_category(
                                eirene_id,
                                "未分類"
                            )
                        )

                    _insert_remote_link_if_missing(
                        profile_id=eirene_id,
                        title=(
                            link["title"]
                            or "未命名收藏"
                        ),
                        url=(
                            link["url"]
                            or ""
                        ),
                        category_id=category_id,
                        note=(
                            link["note"]
                            or ""
                        ),
                        created_at=(
                            link["created_at"]
                            or ""
                        ),
                    )

        local.close()

        set_meta(
            "sqlite_import_v1",
            "1"
        )

    except Exception as exc:

        # 不讓搬家失敗影響正常使用。
        # App 仍然會使用 Supabase 永久儲存。
        print(
            "SQLite → Supabase migration skipped:",
            exc
        )


migrate_local_sqlite_to_supabase()


# ============================================================
# 7. 分類功能
# ============================================================

def fetch_categories(
    profile_id
):

    db = get_db()

    response = (
        db.table("profile_categories")
        .select("*")
        .eq("profile_id", profile_id)
        .execute()
    )

    rows = response.data or []

    rows.sort(
        key=lambda row: (
            1
            if row["name"] == "未分類"
            else 0,
            str(row["name"]).casefold(),
        )
    )

    return rows


def fetch_categories_with_counts(
    profile_id
):

    categories = fetch_categories(
        profile_id
    )

    db = get_db()

    response = (
        db.table("profile_links")
        .select("id,category_id")
        .eq("profile_id", profile_id)
        .execute()
    )

    counts = {}

    for link in (response.data or []):

        category_id = (
            link.get("category_id")
        )

        counts[category_id] = (
            counts.get(
                category_id,
                0
            )
            + 1
        )

    result = []

    for category in categories:

        result.append(
            {
                "id": category["id"],
                "name": category["name"],
                "link_count": counts.get(
                    category["id"],
                    0
                ),
            }
        )

    return result


def add_category(
    profile_id,
    name
):

    name = name.strip()

    if not name:
        return False

    if _find_category_by_name(
        profile_id,
        name
    ):
        return False

    db = get_db()

    try:

        (
            db.table("profile_categories")
            .insert(
                {
                    "profile_id": profile_id,
                    "name": name,
                }
            )
            .execute()
        )

        return True

    except Exception:

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

    db = get_db()

    response = (
        db.table("profile_categories")
        .select("*")
        .eq("id", category_id)
        .eq("profile_id", profile_id)
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if not rows:

        return (
            False,
            "找不到這個分類。"
        )

    category = rows[0]

    if category["name"] == "未分類":

        return (
            False,
            "「未分類」不能重新命名。"
        )

    existing = _find_category_by_name(
        profile_id,
        new_name
    )

    if (
        existing
        and existing["id"] != category_id
    ):

        return (
            False,
            "這個分類名稱已經存在。"
        )

    try:

        (
            db.table("profile_categories")
            .update(
                {
                    "name": new_name,
                }
            )
            .eq("id", category_id)
            .eq("profile_id", profile_id)
            .execute()
        )

        return (
            True,
            None
        )

    except Exception:

        return (
            False,
            "分類名稱更新失敗。"
        )


def delete_category(
    profile_id,
    category_id
):

    uncategorized = (
        _find_category_by_name(
            profile_id,
            "未分類"
        )
    )

    if not uncategorized:
        return False

    uncategorized_id = (
        uncategorized["id"]
    )

    if category_id == uncategorized_id:
        return False

    db = get_db()

    # 收藏先移到未分類
    (
        db.table("profile_links")
        .update(
            {
                "category_id": uncategorized_id,
            }
        )
        .eq("profile_id", profile_id)
        .eq("category_id", category_id)
        .execute()
    )

    # 再刪分類
    (
        db.table("profile_categories")
        .delete()
        .eq("id", category_id)
        .eq("profile_id", profile_id)
        .execute()
    )

    return True


# ============================================================
# 8. 收藏功能
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

    db = get_db()

    (
        db.table("profile_links")
        .insert(
            {
                "profile_id": profile_id,
                "title": title.strip(),
                "url": url.strip(),
                "category_id": category_id,
                "note": note.strip(),
                "created_at": now,
            }
        )
        .execute()
    )


def update_link(
    profile_id,
    link_id,
    title,
    url,
    category_id,
    note=""
):

    db = get_db()

    (
        db.table("profile_links")
        .update(
            {
                "title": title.strip(),
                "url": url.strip(),
                "category_id": category_id,
                "note": note.strip(),
            }
        )
        .eq("id", link_id)
        .eq("profile_id", profile_id)
        .execute()
    )


def delete_link(
    profile_id,
    link_id
):

    db = get_db()

    (
        db.table("profile_links")
        .delete()
        .eq("id", link_id)
        .eq("profile_id", profile_id)
        .execute()
    )


def fetch_links(
    profile_id,
    category_id=None,
    keyword=""
):

    db = get_db()

    query = (
        db.table("profile_links")
        .select("*")
        .eq("profile_id", profile_id)
    )

    if category_id is not None:

        query = query.eq(
            "category_id",
            category_id
        )

    response = query.execute()

    rows = response.data or []

    # --------------------------------------------------------
    # 分類名稱 Join
    #
    # 家庭收藏量很小，這樣做最簡單、穩定，
    # 不依賴 PostgREST relation alias。
    # --------------------------------------------------------

    categories = fetch_categories(
        profile_id
    )

    category_names = {
        category["id"]: category["name"]
        for category in categories
    }

    result = []

    keyword_normalized = (
        keyword.strip().casefold()
    )

    for row in rows:

        item = dict(row)

        category_name = (
            category_names.get(
                item.get("category_id"),
                "未分類"
            )
        )

        item["category_name"] = (
            category_name
        )

        if keyword_normalized:

            haystack = " ".join(
                [
                    str(
                        item.get(
                            "title",
                            ""
                        )
                        or ""
                    ),
                    str(
                        item.get(
                            "url",
                            ""
                        )
                        or ""
                    ),
                    str(
                        item.get(
                            "note",
                            ""
                        )
                        or ""
                    ),
                    str(
                        category_name
                        or ""
                    ),
                ]
            ).casefold()

            if (
                keyword_normalized
                not in haystack
            ):
                continue

        result.append(
            item
        )

    result.sort(
        key=lambda item: (
            item.get("id")
            or 0
        ),
        reverse=True
    )

    return result


# ============================================================
# 9. URL
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
# 10. 自動取得網頁標題
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
                    "(iPhone; CPU iPhone OS 17_0 like Mac OS X) "
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
# 11. URL 中取得 Profile
# ============================================================

profile_slug = st.query_params.get(
    "profile",
    ""
)

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
# 12. 首頁
#
# 2 × 2
# 縮小版卡片
# ============================================================

if not profile:

    home_html = """
    <style>

    /* =======================================================
       背景
       ======================================================= */

    .stApp {
        background: #212121 !important;
    }


    /* =======================================================
       隱藏 Streamlit UI
       ======================================================= */

    [data-testid="stHeader"] {
        display: none !important;
    }

    [data-testid="stToolbar"] {
        display: none !important;
    }

    [data-testid="stDecoration"] {
        display: none !important;
    }

    #MainMenu {
        display: none !important;
    }

    footer {
        display: none !important;
    }


    /* =======================================================
       Streamlit 主容器
       ======================================================= */

    [data-testid="stMainBlockContainer"] {

        padding-top: 0 !important;
        padding-bottom: 0 !important;

        padding-left: 20px !important;
        padding-right: 20px !important;

        max-width: 760px !important;
    }


    /* =======================================================
       首頁
       ======================================================= */

    .profile-wrapper {

        width: 100%;

        min-height: 100vh;

        display: flex;

        align-items: center;
        justify-content: center;

        box-sizing: border-box;
    }


    /* =======================================================
       2 × 2
       ======================================================= */

    .profile-grid {

        width: 100%;

        display: grid;

        grid-template-columns:
            repeat(
                2,
                minmax(0, 1fr)
            );

        gap: 14px;
    }


    /* =======================================================
       卡片
       ======================================================= */

    .profile-card {

        height: 170px;

        background: #B196E4;

        border-radius: 22px;

        display: flex;

        align-items: center;
        justify-content: center;

        color: #212121 !important;

        font-size: 27px;
        font-weight: 700;

        letter-spacing: 0.2px;

        text-decoration: none !important;

        box-shadow:
            0 8px 22px
            rgba(0, 0, 0, 0.25);

        transition:
            transform 0.16s ease,
            background 0.16s ease,
            box-shadow 0.16s ease;

        -webkit-tap-highlight-color:
            transparent;

        user-select: none;
    }


    .profile-card:hover {

        background: #BDA6E8;

        transform:
            translateY(-3px);

        box-shadow:
            0 12px 28px
            rgba(0, 0, 0, 0.32);
    }


    .profile-card:active {

        transform:
            scale(0.97);
    }


    /* =======================================================
       手機
       ======================================================= */

    @media (
        max-width: 600px
    ) {

        [data-testid="stMainBlockContainer"] {

            padding-left: 14px !important;
            padding-right: 14px !important;
        }


        .profile-grid {

            gap: 10px;
        }


        .profile-card {

            height: 135px;

            border-radius: 18px;

            font-size: 20px;
        }

    }

    </style>


    <div class="profile-wrapper">

        <div class="profile-grid">

            <a
                class="profile-card"
                href="?profile=honey"
                target="_self"
            >Honey</a>

            <a
                class="profile-card"
                href="?profile=eirene"
                target="_self"
            >Eirene</a>

            <a
                class="profile-card"
                href="?profile=tinney"
                target="_self"
            >Tinney</a>

            <a
                class="profile-card"
                href="?profile=lyris"
                target="_self"
            >Lyris</a>

        </div>

    </div>
    """

    st.html(
        home_html
    )

    st.stop()


# ============================================================
# 13. 個人空間
# ============================================================

profile_id = profile["id"]
profile_name = profile["name"]


# ============================================================
# 14. 切換使用者時清除暫時狀態
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
# 15. Session State
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
# 16. 個人頁 Header
# ============================================================

header_left, header_right = st.columns(
    [4, 1]
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
# 17. 個人分類
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
# 18. 功能切換
# ============================================================

page_selector_key = (
    f"page_selector_{profile_slug}"
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
# 19. 一次性訊息
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

        link_title = st.text_input(
            "標題",
            placeholder=(
                "可以不填，系統會自動取得"
            )
        )

        link_url = st.text_input(
            "Link",
            placeholder="https://..."
        )

        selected_cat_name = st.selectbox(
            "分類",
            cat_names
        )

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
                # 沒標題 → 自動取得
                # --------------------------------------------

                if not title:

                    with st.spinner(
                        "正在取得網頁標題..."
                    ):

                        title = get_page_title(
                            url
                        )

                    if not title:

                        title = fallback_title(
                            url
                        )

                add_link(
                    profile_id=profile_id,
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
        key=(
            f"library_search_{profile_slug}"
        )
    )

    # --------------------------------------------------------
    # 分類篩選
    # --------------------------------------------------------

    library_options = (
        ["全部"]
        + cat_names
    )

    library_filter_key = (
        f"library_filter_{profile_slug}"
    )

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

        link_id = item["id"]

        with st.container(
            border=True
        ):

            # =================================================
            # 編輯收藏
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

                edit_title = st.text_input(
                    "標題",
                    value=item["title"],
                    key=(
                        f"edit_title_"
                        f"{profile_slug}_"
                        f"{link_id}"
                    )
                )

                edit_url = st.text_input(
                    "Link",
                    value=item["url"],
                    key=(
                        f"edit_url_"
                        f"{profile_slug}_"
                        f"{link_id}"
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

                edit_category = st.selectbox(
                    "分類",
                    cat_names,
                    index=current_index,
                    key=(
                        f"edit_category_"
                        f"{profile_slug}_"
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
                        f"{profile_slug}_"
                        f"{link_id}"
                    )
                )

                (
                    col_save,
                    col_cancel
                ) = st.columns(2)

                # --------------------------------------------
                # 儲存修改
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
                                profile_id=profile_id,
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

                            st.session_state[
                                "flash_message"
                            ] = "✅ 收藏已更新。"

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
            # 一般收藏
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
                            ] = "🗑️ 收藏已刪除。"

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
# PAGE 3：分類
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
            # 編輯分類
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

                new_name = st.text_input(
                    "新的分類名稱",
                    value=category_name,
                    key=(
                        f"rename_category_"
                        f"{profile_slug}_"
                        f"{category_id}"
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
                # 刪除分類確認
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
                    # 確定刪除
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

                            success = delete_category(
                                profile_id,
                                category_id
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
