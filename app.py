from datetime import datetime
from html.parser import HTMLParser
import time
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

PAGE_ADD = "🎯 新增收藏"
PAGE_LIBRARY = "📚 收藏倉庫"
PAGE_CATEGORIES = "🎞 分類管理"

PROFILE_SLUG = "eirene"
PROFILE_NAME = "Eirene 🎀"
PROFILE_ICON = "🎀"


# ============================================================
# 2. Supabase 連線
#
# Streamlit Cloud → App settings → Secrets：
#
# [supabase]
# url = "https://xxxx.supabase.co"
# key = "sb_secret_xxxxxxxxx"
# ============================================================

@st.cache_resource(show_spinner=False)
def get_db() -> Client:
    try:
        supabase_url = st.secrets["supabase"]["url"]
        supabase_key = st.secrets["supabase"]["key"]
    except Exception:
        st.error(
            "尚未設定 Supabase 連線。\n\n"
            "請到 Streamlit → App settings → Secrets 加入：\n\n"
            '[supabase]\n'
            'url = "https://你的專案.supabase.co"\n'
            'key = "sb_secret_..."'
        )
        st.stop()

    return create_client(supabase_url, supabase_key)


def execute_with_retry(operation, attempts=3):
    """Supabase 遇到短暫網路問題時，自動重試。"""
    last_error = None

    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:
            last_error = exc

            if attempt < attempts - 1:
                time.sleep(0.35 * (attempt + 1))

    raise last_error


def show_connection_error(exc):
    st.error(
        "Supabase 暫時連線失敗。程式已自動重試，但目前仍無法取得資料。"
    )
    st.caption(
        "這通常是暫時性的網路或服務連線問題，不代表收藏資料消失。"
    )

    with st.expander("技術資訊"):
        st.code(str(exc))

    if st.button("重新連線", type="primary", use_container_width=True):
        st.cache_resource.clear()
        st.cache_data.clear()
        st.rerun()

    st.stop()


# ============================================================
# 3. Eirene 初始化
#
# 只在 Streamlit 程序啟動時執行一次。
# 不會在每次按按鈕、切頁時重新查四個使用者。
# ============================================================

@st.cache_resource(show_spinner=False)
def ensure_eirene_profile():
    db = get_db()

    response = execute_with_retry(
        lambda: (
            db.table("profiles")
            .select("id,slug,name")
            .eq("slug", PROFILE_SLUG)
            .limit(1)
            .execute()
        )
    )

    rows = response.data or []

    if rows:
        profile = rows[0]
    else:
        response = execute_with_retry(
            lambda: (
                db.table("profiles")
                .insert(
                    {
                        "slug": PROFILE_SLUG,
                        "name": PROFILE_NAME,
                    }
                )
                .execute()
            )
        )

        rows = response.data or []

        if not rows:
            raise RuntimeError("無法建立 Eirene profile。")

        profile = rows[0]

    profile_id = profile["id"]

    # 確保 Eirene 至少有「未分類」
    response = execute_with_retry(
        lambda: (
            db.table("profile_categories")
            .select("id,name")
            .eq("profile_id", profile_id)
            .eq("name", "未分類")
            .limit(1)
            .execute()
        )
    )

    if not (response.data or []):
        execute_with_retry(
            lambda: (
                db.table("profile_categories")
                .insert(
                    {
                        "profile_id": profile_id,
                        "name": "未分類",
                    }
                )
                .execute()
            )
        )

    return {
        "id": profile_id,
        "slug": PROFILE_SLUG,
        "name": PROFILE_NAME,
    }


try:
    PROFILE = ensure_eirene_profile()
except Exception as exc:
    show_connection_error(exc)

PROFILE_ID = PROFILE["id"]


# ============================================================
# 4. Cache
#
# 搜尋、切頁、打字造成 Streamlit rerun 時，
# 不會一直重查 Supabase。
# ============================================================

@st.cache_data(ttl=300, show_spinner=False)
def fetch_categories_cached(profile_id):
    db = get_db()

    response = execute_with_retry(
        lambda: (
            db.table("profile_categories")
            .select("id,profile_id,name")
            .eq("profile_id", profile_id)
            .execute()
        )
    )

    rows = [dict(row) for row in (response.data or [])]

    rows.sort(
        key=lambda row: (
            1 if row.get("name") == "未分類" else 0,
            str(row.get("name", "")).casefold(),
        )
    )

    return rows


@st.cache_data(ttl=120, show_spinner=False)
def fetch_all_links_cached(profile_id):
    db = get_db()

    response = execute_with_retry(
        lambda: (
            db.table("profile_links")
            .select("id,profile_id,title,url,category_id,note,created_at")
            .eq("profile_id", profile_id)
            .order("id", desc=True)
            .execute()
        )
    )

    return [dict(row) for row in (response.data or [])]


def clear_category_cache():
    fetch_categories_cached.clear()


def clear_link_cache():
    fetch_all_links_cached.clear()


def clear_all_data_cache():
    clear_category_cache()
    clear_link_cache()


# ============================================================
# 5. 分類功能
# ============================================================


def fetch_categories(profile_id):
    return fetch_categories_cached(profile_id)


def find_category_by_name(profile_id, name):
    wanted = name.strip().casefold()

    for category in fetch_categories(profile_id):
        if str(category.get("name", "")).strip().casefold() == wanted:
            return category

    return None


def fetch_categories_with_counts(profile_id):
    categories = fetch_categories(profile_id)
    links = fetch_all_links_cached(profile_id)

    counts = {}

    for link in links:
        category_id = link.get("category_id")
        counts[category_id] = counts.get(category_id, 0) + 1

    return [
        {
            "id": category["id"],
            "name": category["name"],
            "link_count": counts.get(category["id"], 0),
        }
        for category in categories
    ]


def add_category(profile_id, name):
    name = name.strip()

    if not name:
        return False

    if find_category_by_name(profile_id, name):
        return False

    db = get_db()

    try:
        execute_with_retry(
            lambda: (
                db.table("profile_categories")
                .insert(
                    {
                        "profile_id": profile_id,
                        "name": name,
                    }
                )
                .execute()
            )
        )
        clear_category_cache()
        return True
    except Exception:
        return False


def rename_category(profile_id, category_id, new_name):
    new_name = new_name.strip()

    if not new_name:
        return False, "分類名稱不能為空白。"

    categories = fetch_categories(profile_id)

    category = next(
        (
            item
            for item in categories
            if item["id"] == category_id
        ),
        None,
    )

    if not category:
        return False, "找不到這個分類。"

    if category["name"] == "未分類":
        return False, "「未分類」不能重新命名。"

    duplicate = next(
        (
            item
            for item in categories
            if item["id"] != category_id
            and str(item["name"]).casefold() == new_name.casefold()
        ),
        None,
    )

    if duplicate:
        return False, "這個分類名稱已經存在。"

    db = get_db()

    try:
        execute_with_retry(
            lambda: (
                db.table("profile_categories")
                .update({"name": new_name})
                .eq("id", category_id)
                .eq("profile_id", profile_id)
                .execute()
            )
        )
        clear_category_cache()
        return True, None
    except Exception as exc:
        return False, f"分類更新失敗：{exc}"


def delete_category(profile_id, category_id):
    categories = fetch_categories(profile_id)

    uncategorized = next(
        (
            item
            for item in categories
            if item["name"] == "未分類"
        ),
        None,
    )

    if not uncategorized:
        return False

    uncategorized_id = uncategorized["id"]

    if category_id == uncategorized_id:
        return False

    db = get_db()

    execute_with_retry(
        lambda: (
            db.table("profile_links")
            .update({"category_id": uncategorized_id})
            .eq("profile_id", profile_id)
            .eq("category_id", category_id)
            .execute()
        )
    )

    execute_with_retry(
        lambda: (
            db.table("profile_categories")
            .delete()
            .eq("id", category_id)
            .eq("profile_id", profile_id)
            .execute()
        )
    )

    clear_all_data_cache()
    return True


# ============================================================
# 6. 收藏功能
# ============================================================


def add_link(profile_id, title, url, category_id, note=""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    db = get_db()

    execute_with_retry(
        lambda: (
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
    )

    clear_link_cache()


def update_link(profile_id, link_id, title, url, category_id, note=""):
    db = get_db()

    execute_with_retry(
        lambda: (
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
    )

    clear_link_cache()


def delete_link(profile_id, link_id):
    db = get_db()

    execute_with_retry(
        lambda: (
            db.table("profile_links")
            .delete()
            .eq("id", link_id)
            .eq("profile_id", profile_id)
            .execute()
        )
    )

    clear_link_cache()


def fetch_links(profile_id, category_id=None, keyword=""):
    """
    所有收藏只從 Supabase 抓一次並快取。
    分類篩選與文字搜尋全部在 Streamlit 端完成，
    所以搜尋框每打一個字不會重新打 Supabase API。
    """

    links = fetch_all_links_cached(profile_id)
    categories = fetch_categories(profile_id)

    category_names = {
        category["id"]: category["name"]
        for category in categories
    }

    keyword_normalized = keyword.strip().casefold()
    result = []

    for row in links:
        if category_id is not None and row.get("category_id") != category_id:
            continue

        item = dict(row)
        category_name = category_names.get(
            item.get("category_id"),
            "未分類",
        )
        item["category_name"] = category_name

        if keyword_normalized:
            haystack = " ".join(
                [
                    str(item.get("title", "") or ""),
                    str(item.get("url", "") or ""),
                    str(item.get("note", "") or ""),
                    str(category_name or ""),
                ]
            ).casefold()

            if keyword_normalized not in haystack:
                continue

        result.append(item)

    return result


# ============================================================
# 7. URL 功能
# ============================================================


def normalize_url(url):
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
# 8. 自動取得網頁標題
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
            },
        )

        with urlopen(request, timeout=5) as response:
            html = response.read(300_000)
            encoding = response.headers.get_content_charset() or "utf-8"

            try:
                text = html.decode(encoding, errors="ignore")
            except Exception:
                text = html.decode("utf-8", errors="ignore")

        parser = TitleParser()
        parser.feed(text)

        title = " ".join(parser.title.strip().split())

        if title:
            return title

    except Exception:
        pass

    return None


def fallback_title(url):
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
# 9. Session State
# ============================================================

DEFAULT_STATE = {
    "editing_link_id": None,
    "delete_link_id": None,
    "editing_category_id": None,
    "delete_category_id": None,
    "flash_message": None,
}

for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# 10. Eirene 單人版 Theme
# ============================================================

APP_CSS = """
<style>

.stApp {
    background-color: #212121 !important;
    color: #F5F5F5 !important;
}

/* Streamlit 自帶的上方工具列全部隱藏 */
header[data-testid="stHeader"],
[data-testid="stHeader"],
[data-testid="stToolbar"],
[data-testid="stAppToolbar"],
[data-testid="stDecoration"],
[data-testid="stStatusWidget"],
[data-testid="stAppDeployButton"] {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
    min-height: 0 !important;
}

#MainMenu {
    display: none !important;
    visibility: hidden !important;
}

footer,
[data-testid="stFooter"] {
    display: none !important;
    visibility: hidden !important;
}

/* Community Cloud 的底部 Hosted with Streamlit badge */
div[class*="viewerBadge_container__"],
div[class*="viewerBadge_link__"] {
    display: none !important;
}

[data-testid="stMainBlockContainer"] {
    width: min(100%, 720px) !important;
    max-width: 720px !important;
    margin-left: auto !important;
    margin-right: auto !important;
    padding-top: 0.8rem !important;
    padding-bottom: 2rem !important;
    padding-left: 0 !important;
    padding-right: 0 !important;
}

h1, h2, h3, h4, h5, h6 {
    color: #FFFFFF !important;
}

.stApp p,
.stApp label {
    color: #F2F2F2;
}

[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] p {
    color: #AFAFAF !important;
}

/* Input */
[data-baseweb="input"] > div,
[data-baseweb="textarea"],
[data-baseweb="select"] > div {
    background-color: #292930 !important;
    border-color: #474750 !important;
    border-radius: 12px !important;
}

[data-baseweb="input"] input,
[data-baseweb="textarea"] textarea {
    color: #FFFFFF !important;
    background-color: transparent !important;
}

[data-baseweb="select"] span {
    color: #FFFFFF !important;
}

input::placeholder,
textarea::placeholder {
    color: #9A9AA5 !important;
    opacity: 1 !important;
}

/* Form */
[data-testid="stForm"] {
    border-color: #44444D !important;
    border-radius: 14px !important;
}

/* 收藏 / 分類卡片 */
[data-testid="stVerticalBlockBorderWrapper"] {
    background-color: #28282D !important;
    border-color: #424249 !important;
    border-radius: 16px !important;
}

/* 一般按鈕 */
.stButton > button {
    background-color: #2B2B31 !important;
    color: #FFFFFF !important;
    border: 1px solid #50505A !important;
    border-radius: 11px !important;
}

.stButton > button:hover {
    background-color: #36363E !important;
    color: #FFFFFF !important;
    border-color: #B196E4 !important;
}

/* 紫色 Primary / Form Submit：紫底黑字 */
button[kind="primary"],
button[data-testid^="stBaseButton-primary"],
[data-testid="stFormSubmitButton"] button {
    background-color: #B196E4 !important;
    color: #212121 !important;
    border: 1px solid #B196E4 !important;
    border-radius: 11px !important;
    font-weight: 700 !important;
}

/* Streamlit 會在 button 裡再包 MarkdownContainer / p / span，
   所以要把內層文字也強制設成黑色 */
button[kind="primary"] *,
button[data-testid^="stBaseButton-primary"] *,
[data-testid="stFormSubmitButton"] button *,
[data-testid="stFormSubmitButton"] [data-testid="stMarkdownContainer"],
[data-testid="stFormSubmitButton"] [data-testid="stMarkdownContainer"] p,
[data-testid="stFormSubmitButton"] [data-testid="stMarkdownContainer"] span {
    color: #212121 !important;
    -webkit-text-fill-color: #212121 !important;
}

button[kind="primary"]:hover,
button[data-testid^="stBaseButton-primary"]:hover,
[data-testid="stFormSubmitButton"] button:hover {
    background-color: #BDA6E8 !important;
    color: #212121 !important;
    border-color: #BDA6E8 !important;
}

button[kind="primary"]:hover *,
button[data-testid^="stBaseButton-primary"]:hover *,
[data-testid="stFormSubmitButton"] button:hover * {
    color: #212121 !important;
    -webkit-text-fill-color: #212121 !important;
}

button[kind="primary"]:focus,
button[kind="primary"]:active,
button[data-testid^="stBaseButton-primary"]:focus,
button[data-testid^="stBaseButton-primary"]:active,
[data-testid="stFormSubmitButton"] button:focus,
[data-testid="stFormSubmitButton"] button:active {
    background-color: #B196E4 !important;
    color: #212121 !important;
    border-color: #B196E4 !important;
}

button[kind="primary"]:focus *,
button[kind="primary"]:active *,
button[data-testid^="stBaseButton-primary"]:focus *,
button[data-testid^="stBaseButton-primary"]:active *,
[data-testid="stFormSubmitButton"] button:focus *,
[data-testid="stFormSubmitButton"] button:active * {
    color: #212121 !important;
    -webkit-text-fill-color: #212121 !important;
}

/* 開啟連結 */
[data-testid="stLinkButton"] a {
    background-color: #B196E4 !important;
    color: #212121 !important;
    border: 1px solid #B196E4 !important;
    border-radius: 11px !important;
    font-weight: 700 !important;
}

/* 強制按鈕內所有文字也是黑色 */
[data-testid="stLinkButton"] a *,
[data-testid="stLinkButton"] a p,
[data-testid="stLinkButton"] a span {
    color: #212121 !important;
    -webkit-text-fill-color: #212121 !important;
}

/* 滑鼠移上去 */
[data-testid="stLinkButton"] a:hover {
    background-color: #BDA6E8 !important;
    color: #212121 !important;
    border-color: #BDA6E8 !important;
}

[data-testid="stLinkButton"] a:hover *,
[data-testid="stLinkButton"] a:hover p,
[data-testid="stLinkButton"] a:hover span {
    color: #212121 !important;
    -webkit-text-fill-color: #212121 !important;
}
/* Segmented control：桌機與手機都固定三格完全等寬 */
[data-testid="stSegmentedControl"] {
    width: 100% !important;
    max-width: 100% !important;
}

[data-testid="stSegmentedControl"] > div {
    width: 100% !important;
    max-width: 100% !important;
}

/* 不讓文字長度影響三格寬度 */
[data-testid="stSegmentedControl"] [role="radiogroup"] {
    display: grid !important;
    grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
    width: 100% !important;
    max-width: 100% !important;
    gap: 0 !important;
}

/* 某些 Streamlit 版本會在 button 外再包一層 */
[data-testid="stSegmentedControl"] [role="radiogroup"] > div {
    width: 100% !important;
    min-width: 0 !important;
    max-width: 100% !important;
}

[data-testid="stSegmentedControl"] button {
    width: 100% !important;
    min-width: 0 !important;
    max-width: none !important;
    justify-content: center !important;
    text-align: center !important;
    white-space: nowrap !important;
    color: #E5E5E5 !important;
    border-color: #4B4B55 !important;
}

[data-testid="stSegmentedControl"] button * {
    justify-content: center !important;
    text-align: center !important;
}

[data-testid="stSegmentedControl"] button[aria-pressed="true"] {
    background-color: #B196E4 !important;
    color: #212121 !important;
    border-color: #B196E4 !important;
    font-weight: 700 !important;
}

[data-testid="stSegmentedControl"] button[aria-pressed="true"] * {
    color: #212121 !important;
    -webkit-text-fill-color: #212121 !important;
}

hr {
    border-color: #3D3D45 !important;
}

[data-testid="stAlert"] {
    border-radius: 13px !important;
}

@media (max-width: 760px) {
    [data-testid="stMainBlockContainer"] {
        width: 100% !important;
        max-width: 100% !important;
        padding-left: 14px !important;
        padding-right: 14px !important;
        padding-top: 0.65rem !important;
    }
}

@media (max-width: 480px) {
    [data-testid="stSegmentedControl"] button {
        font-size: 0.88rem !important;
        padding-left: 0.4rem !important;
        padding-right: 0.4rem !important;
    }
}

</style>
"""

st.html(APP_CSS)


# ============================================================
# 11. Header
# ============================================================

st.title(f"{PROFILE_ICON} {PROFILE_NAME}")


# ============================================================
# 12. 取得分類
#
# 第一次會讀 Supabase；之後 rerun 使用 cache。
# ============================================================

try:
    categories = fetch_categories(PROFILE_ID)
except Exception as exc:
    show_connection_error(exc)

cat_dict = {
    category["name"]: category["id"]
    for category in categories
}

cat_names = list(cat_dict.keys())


# ============================================================
# 13. 功能切換
# ============================================================

active_page = st.segmented_control(
    "功能",
    options=[
        PAGE_ADD,
        PAGE_LIBRARY,
        PAGE_CATEGORIES,
    ],
    default=PAGE_ADD,
    selection_mode="single",
    key="page_selector_eirene",
    label_visibility="collapsed",
    width="stretch",
)

if not active_page:
    active_page = PAGE_ADD


# ============================================================
# 14. 一次性訊息
# ============================================================

if st.session_state["flash_message"]:
    st.success(st.session_state["flash_message"])
    st.session_state["flash_message"] = None


# ============================================================
# PAGE 1：新增收藏
# ============================================================

if active_page == PAGE_ADD:
    st.markdown("### 🎯 快速收藏")

    with st.form(
        "add_link_form_eirene",
        clear_on_submit=True,
    ):
        link_title = st.text_input(
            "標題",
            placeholder="可以不填，系統會自動取得",
        )

        link_url = st.text_input(
            "Link",
            placeholder="https://...",
        )

        selected_cat_name = st.selectbox(
            "分類",
            cat_names,
        )

        submitted = st.form_submit_button(
            "💾 儲存收藏",
            type="primary",
            use_container_width=True,
        )

        if submitted:
            url = normalize_url(link_url)

            if not url:
                st.error("請輸入網址。")

            elif not is_valid_url(url):
                st.error("網址格式似乎不正確。")

            else:
                title = link_title.strip()

                if not title:
                    with st.spinner("正在取得網頁標題..."):
                        title = get_page_title(url)

                    if not title:
                        title = fallback_title(url)

                try:
                    add_link(
                        profile_id=PROFILE_ID,
                        title=title,
                        url=url,
                        category_id=cat_dict[selected_cat_name],
                        note="",
                    )

                    st.success(f"✅ 已收藏：{title}")

                except Exception as exc:
                    st.error("收藏儲存失敗，請稍後再試。")
                    with st.expander("技術資訊"):
                        st.code(str(exc))


# ============================================================
# PAGE 2：收藏庫
# ============================================================

elif active_page == PAGE_LIBRARY:
    st.markdown("### 📚 收藏庫")

    search_keyword = st.text_input(
        "搜尋收藏",
        placeholder="🔍 搜尋標題、網址、分類...",
        key="library_search_eirene",
    )

    library_options = ["全部"] + cat_names
    library_filter_key = "library_filter_eirene"

    if library_filter_key in st.session_state:
        if st.session_state[library_filter_key] not in library_options:
            st.session_state[library_filter_key] = "全部"

    filter_cat = st.selectbox(
        "分類",
        library_options,
        key=library_filter_key,
    )

    current_cat_id = (
        None
        if filter_cat == "全部"
        else cat_dict[filter_cat]
    )

    try:
        links = fetch_links(
            profile_id=PROFILE_ID,
            category_id=current_cat_id,
            keyword=search_keyword,
        )
    except Exception as exc:
        show_connection_error(exc)

    st.caption(f"目前共有 {len(links)} 筆收藏")

    if not links:
        st.info("目前沒有符合條件的收藏。")

    for item in links:
        link_id = item["id"]

        with st.container(border=True):
            # ------------------------------------------------
            # 編輯模式
            # ------------------------------------------------
            if st.session_state["editing_link_id"] == link_id:
                st.markdown("#### ✏️ 編輯收藏")

                edit_title = st.text_input(
                    "標題",
                    value=item["title"],
                    key=f"edit_title_{link_id}",
                )

                edit_url = st.text_input(
                    "Link",
                    value=item["url"],
                    key=f"edit_url_{link_id}",
                )

                try:
                    current_index = cat_names.index(item["category_name"])
                except (ValueError, TypeError):
                    current_index = (
                        cat_names.index("未分類")
                        if "未分類" in cat_names
                        else 0
                    )

                edit_category = st.selectbox(
                    "分類",
                    cat_names,
                    index=current_index,
                    key=f"edit_category_{link_id}",
                )

                edit_note = st.text_area(
                    "備註",
                    value=item.get("note") or "",
                    placeholder="可選填",
                    key=f"edit_note_{link_id}",
                )

                col_save, col_cancel = st.columns(2)

                with col_save:
                    if st.button(
                        "💾 儲存修改",
                        key=f"save_{link_id}",
                        type="primary",
                        use_container_width=True,
                    ):
                        new_url = normalize_url(edit_url)

                        if not edit_title.strip():
                            st.error("標題不能為空白。")

                        elif not is_valid_url(new_url):
                            st.error("網址格式不正確。")

                        else:
                            try:
                                update_link(
                                    profile_id=PROFILE_ID,
                                    link_id=link_id,
                                    title=edit_title,
                                    url=new_url,
                                    category_id=cat_dict[edit_category],
                                    note=edit_note,
                                )

                                st.session_state["editing_link_id"] = None
                                st.session_state["flash_message"] = "✅ 收藏已更新。"
                                st.rerun()

                            except Exception as exc:
                                st.error("更新失敗，請稍後再試。")
                                with st.expander("技術資訊"):
                                    st.code(str(exc))

                with col_cancel:
                    if st.button(
                        "取消",
                        key=f"cancel_edit_{link_id}",
                        use_container_width=True,
                    ):
                        st.session_state["editing_link_id"] = None
                        st.rerun()

            # ------------------------------------------------
            # 一般模式
            # ------------------------------------------------
            else:
                st.markdown(f"### {item['title']}")

                category_name = item.get("category_name") or "未分類"

                st.caption(
                    f"🏷️ {category_name}　·　🕒 {item.get('created_at', '')}"
                )

                if item.get("note"):
                    st.write(item["note"])

                col_open, col_edit, col_delete = st.columns([3, 1, 1])

                with col_open:
                    st.link_button(
                        "🔗 開啟連結",
                        item["url"],
                        use_container_width=True,
                    )

                with col_edit:
                    if st.button(
                        "✏️",
                        key=f"edit_{link_id}",
                        help="編輯收藏",
                        use_container_width=True,
                    ):
                        st.session_state["editing_link_id"] = link_id
                        st.session_state["delete_link_id"] = None
                        st.rerun()

                with col_delete:
                    if st.button(
                        "🗑️",
                        key=f"delete_{link_id}",
                        help="刪除收藏",
                        use_container_width=True,
                    ):
                        st.session_state["delete_link_id"] = link_id
                        st.session_state["editing_link_id"] = None
                        st.rerun()

                if st.session_state["delete_link_id"] == link_id:
                    st.warning(f"確定要刪除「{item['title']}」嗎？")

                    confirm_col, cancel_col = st.columns(2)

                    with confirm_col:
                        if st.button(
                            "確定刪除",
                            key=f"confirm_delete_{link_id}",
                            type="primary",
                            use_container_width=True,
                        ):
                            try:
                                delete_link(PROFILE_ID, link_id)
                                st.session_state["delete_link_id"] = None
                                st.session_state["flash_message"] = "🗑️ 收藏已刪除。"
                                st.rerun()

                            except Exception as exc:
                                st.error("刪除失敗，請稍後再試。")
                                with st.expander("技術資訊"):
                                    st.code(str(exc))

                    with cancel_col:
                        if st.button(
                            "取消",
                            key=f"cancel_delete_{link_id}",
                            use_container_width=True,
                        ):
                            st.session_state["delete_link_id"] = None
                            st.rerun()


# ============================================================
# PAGE 3：分類管理
# ============================================================

elif active_page == PAGE_CATEGORIES:
    st.markdown("### 🎞 分類管理")
    st.caption("新增、重新命名或刪除你的收藏分類。")

    st.markdown("#### ＋ 新增分類")

    with st.form(
        "add_category_form_eirene",
        clear_on_submit=True,
    ):
        new_cat_name = st.text_input(
            "分類名稱",
            placeholder="例如：料理、投資、AI 工具",
        )

        add_category_submitted = st.form_submit_button(
            "＋ 新增分類",
            type="primary",
            use_container_width=True,
        )

        if add_category_submitted:
            category_name = new_cat_name.strip()

            if not category_name:
                st.warning("請輸入分類名稱。")

            elif add_category(PROFILE_ID, category_name):
                st.session_state["flash_message"] = (
                    f"✅ 已新增分類「{category_name}」。"
                )
                st.rerun()

            else:
                st.warning("這個分類已經存在，或新增失敗。")

    st.divider()
    st.markdown("#### 我的分類")

    try:
        category_list = fetch_categories_with_counts(PROFILE_ID)
    except Exception as exc:
        show_connection_error(exc)

    for category in category_list:
        category_id = category["id"]
        category_name = category["name"]
        link_count = category["link_count"]

        with st.container(border=True):
            # ------------------------------------------------
            # 未分類
            # ------------------------------------------------
            if category_name == "未分類":
                col_info, col_lock = st.columns([5, 1])

                with col_info:
                    st.markdown(f"**📦 {category_name}**")
                    st.caption(f"{link_count} 筆收藏")

                with col_lock:
                    st.markdown("🔒")

            # ------------------------------------------------
            # 編輯分類
            # ------------------------------------------------
            elif st.session_state["editing_category_id"] == category_id:
                st.markdown(f"**✏️ 編輯「{category_name}」**")

                new_name = st.text_input(
                    "新的分類名稱",
                    value=category_name,
                    key=f"rename_category_{category_id}",
                )

                col_save, col_cancel = st.columns(2)

                with col_save:
                    if st.button(
                        "儲存",
                        type="primary",
                        use_container_width=True,
                        key=f"save_category_{category_id}",
                    ):
                        success, error = rename_category(
                            PROFILE_ID,
                            category_id,
                            new_name,
                        )

                        if success:
                            st.session_state["editing_category_id"] = None
                            st.session_state["flash_message"] = "✅ 分類名稱已更新。"
                            st.rerun()
                        else:
                            st.error(error)

                with col_cancel:
                    if st.button(
                        "取消",
                        use_container_width=True,
                        key=f"cancel_category_edit_{category_id}",
                    ):
                        st.session_state["editing_category_id"] = None
                        st.rerun()

            # ------------------------------------------------
            # 一般分類
            # ------------------------------------------------
            else:
                col_info, col_edit, col_delete = st.columns([5, 1, 1])

                with col_info:
                    st.markdown(f"**🏷️ {category_name}**")
                    st.caption(f"{link_count} 筆收藏")

                with col_edit:
                    if st.button(
                        "✏️",
                        key=f"edit_category_{category_id}",
                        help="重新命名",
                        use_container_width=True,
                    ):
                        st.session_state["editing_category_id"] = category_id
                        st.session_state["delete_category_id"] = None
                        st.rerun()

                with col_delete:
                    if st.button(
                        "🗑️",
                        key=f"delete_category_{category_id}",
                        help="刪除分類",
                        use_container_width=True,
                    ):
                        st.session_state["delete_category_id"] = category_id
                        st.session_state["editing_category_id"] = None
                        st.rerun()

                if st.session_state["delete_category_id"] == category_id:
                    if link_count > 0:
                        st.warning(
                            f"確定要刪除「{category_name}」嗎？\n\n"
                            f"其中的 {link_count} 筆收藏會移到「未分類」。"
                        )
                    else:
                        st.warning(f"確定要刪除「{category_name}」嗎？")

                    col_confirm, col_cancel = st.columns(2)

                    with col_confirm:
                        if st.button(
                            "確定刪除",
                            type="primary",
                            use_container_width=True,
                            key=f"confirm_category_{category_id}",
                        ):
                            try:
                                success = delete_category(
                                    PROFILE_ID,
                                    category_id,
                                )

                                st.session_state["delete_category_id"] = None

                                if success:
                                    st.session_state["flash_message"] = (
                                        f"🗑️ 已刪除分類「{category_name}」。"
                                    )

                                st.rerun()

                            except Exception as exc:
                                st.error("刪除分類失敗，請稍後再試。")
                                with st.expander("技術資訊"):
                                    st.code(str(exc))

                    with col_cancel:
                        if st.button(
                            "取消",
                            use_container_width=True,
                            key=f"cancel_category_{category_id}",
                        ):
                            st.session_state["delete_category_id"] = None
                            st.rerun()
