"""直接回答的工具：天氣（get_weather、remember_home）與長尾回答（web_answer、open_source）。"""
import datetime
import time
import urllib.parse
from typing import Optional

from mac_ai import config
from mac_ai.router import cloud_client, prefs_db, profile_store, search_client, sources, weather_client, weather_followup, weather_prefs
from mac_ai.router.browser_tools import web_search_or_open_url
from mac_ai.router.exec_context import _FAIL_PREFIXES, ExecContext, front_app, mark_page
from mac_ai.router.prefs_db import _db_call


# ---------- 天氣（get_weather，計畫 docs/plans/completed/2026-11-21-web-answer.md）----------
_TAIPEI = datetime.timezone(datetime.timedelta(hours=8))  # 台灣沒有日光節約時間，不需要 zoneinfo


def today_taipei() -> str:
    """台北時間的今天（YYYY-MM-DD）；長尾回答的規劃與回答兩次呼叫都用它（模型不知道今天幾號）"""
    return datetime.datetime.fromtimestamp(prefs_db._now(), _TAIPEI).strftime("%Y-%m-%d")


def get_weather(city: str = "", day: str = "today", part: str = "", set_home: str = "", ctx: Optional[ExecContext] = None) -> str:
    """天氣回答（只讀資料：不開網頁、不呼叫 LLM）。city 空字串＝用預設：居住地（profile.home_city）→ 資料庫統計的常問縣市 →
    都沒有就請使用者說縣市（冷啟動：不猜、不定位，並記下這次的 day、part，下一句只說縣市就接著回答）。
    句子明說的縣市記進資料庫（不管查不查得到）；set_home（said＝「我住 X＋天氣」、cold_start_reply＝冷啟動後回答的縣市）時同時設為居住地。
    有 CWA_API_KEY 先查氣象署，沒有金鑰或失敗改查 Open-Meteo，兩個都失敗回 ❌ 查不到。
    資料庫還沒準備好（conn() 是 None）：明說縣市照常回答、只是不記；沒說縣市當冷啟動。結果文字放進 ctx.replies（泡泡）"""
    t0 = time.perf_counter()
    info: dict = ctx.step.setdefault("weather", {}) if ctx is not None and ctx.step is not None else {}
    now = prefs_db._now()
    conn = prefs_db.MUSIC_DB.conn()
    explicit = bool(city)
    if explicit:
        if conn is not None:
            _db_call(info, weather_prefs.record_city, conn, city, now)
            if set_home:
                _db_call(info, profile_store.set, conn, profile_store.HOME_CITY, city, set_home, now)
                info["set_home"] = set_home
    else:
        home = _db_call(info, profile_store.get, conn, profile_store.HOME_CITY) if conn is not None else None
        city = home or (_db_call(info, weather_prefs.default_city, conn, now) if conn is not None else None) or ""
        if not city:
            info.update(city="", cold_start=True)
            weather_followup.STORE.put(day, part)
            return weather_prefs.COLD_START_MESSAGE
        info["default"] = "home" if home else "stats"
    res = weather_client.lookup(city, day, part, datetime.datetime.fromtimestamp(now, _TAIPEI).replace(tzinfo=None),
                                config.env_value("CWA_API_KEY").strip())
    info.update(city=city, explicit=explicit, source=res.source, tried=res.tried, ms=(time.perf_counter() - t0) * 1000)
    if res.text is None:
        return f"❌ 嗚…我查不到{city}的天氣耶，氣象服務好像沒有回應，等一下再問我一次好嗎？"
    if ctx is not None:
        ctx.replies.append(res.text)
    return f"✅ {res.text}"


def home_reply(city: str) -> str:
    """「我住 X」聊天這一步模型沒給文字或失敗時的固定回覆"""
    return f"好～記住你住{city}了，之後問天氣就查{city}喔"


def remember_home(city: str, ctx: Optional[ExecContext] = None) -> str:
    """「我住 X」（C2）：在背景記下居住地，並照舊記一筆明說的縣市統計；結果只進 log、不出泡泡。
    同一步接著聊天（ctx.escalate kind＝chat，由 pipeline 轉交：只採用文字回覆、不執行任何工具）。
    資料庫還沒準備好或寫入失敗：回 ⚠️，聊天的提示改成「沒記下來」，回覆不會謊稱已經記住"""
    info: dict = ctx.step.setdefault("weather", {}) if ctx is not None and ctx.step is not None else {}
    now = prefs_db._now()
    conn = prefs_db.MUSIC_DB.conn()
    if conn is not None:
        _db_call(info, profile_store.set, conn, profile_store.HOME_CITY, city, "said", now)
        if "db_error" not in info:
            _db_call(info, weather_prefs.record_city, conn, city, now)
    saved = conn is not None and "db_error" not in info
    info.update(city=city, set_home="said" if saved else "")
    if ctx is not None:
        ctx.escalate = ({"kind": "chat", "note": f"已在背景記下使用者的居住地：{city}", "fallback": home_reply(city)} if saved else
                        {"kind": "chat", "note": f"使用者說居住地是{city}，但這次沒能記下來；回覆不得說已經記住。",
                         "fallback": f"我聽到你住{city}了，不過這次沒記下來，等一下再跟我說一次好嗎？"})
    return f"✅ 已在背景記下居住地：{city}" if saved else f"⚠️ 偏好資料庫無法使用，沒有記下居住地：{city}"


# ---------- 長尾回答（搜尋 → 雲端依摘錄回答）----------
_WHY = {"search": "搜尋服務沒有回應", "cloud": "AI 沒有回應", "no_answer": "搜尋結果裡沒有答案", "truncated": "AI 的回答不完整",
        "no_search_key": "沒有設定搜尋金鑰"}


def _web_fallback(query: str, ctx: Optional[ExecContext], why: str) -> str:
    """搜尋、雲端摘要任何一步失敗或沒有答案：開 Google 搜尋頁（與沒有這個功能時一樣）並回 ⚠️，不謊稱查到（W4、紅線 9、11）"""
    browser = (ctx.last_browser or front_app(ctx, "browser")) if ctx is not None else None
    res = web_search_or_open_url(query, browser=browser)
    if ctx is not None:
        mark_page(ctx, res)
    if res.startswith(_FAIL_PREFIXES):
        return f"❌ 沒辦法直接回答（{_WHY[why]}），開啟瀏覽器也失敗了：{query}"
    return f"⚠️ 沒辦法直接回答（{_WHY[why]}），已經幫你開 Google 搜尋「{query}」"


def web_answer(query: str, ctx: Optional[ExecContext] = None) -> str:
    """長尾問題：Brave 搜尋（只送 query）→ 雲端依摘錄回答 1～2 句（不帶工具）→ 放進 ctx.replies，並記下第一筆來源供「要打開來源嗎？」。
    Fast Path 也會直接送來（會變的事實、查／找＋問句，計畫 docs/plans/completed/2026-11-23-query-routing.md U12）：
    沒有雲端 → ctx.escalate 交給本機 Slow Path；有雲端、沒有搜尋金鑰 → 開搜尋頁並記 step["web_fallback"]（U13）。
    搜尋失敗、雲端失敗、摘錄裡沒有答案都退回開搜尋頁（⚠️）。摘錄只活在這個函式裡：不寫紀錄、不進對話記憶（Brave 條款）。
    原句有時間性時只搜近期網頁（search_client.freshness_for）；近期沒有結果就不過濾再搜一次。
    各階段記在 ctx.step["web"]：search_ms、results、freshness、fresh_retry、answer_ms、cloud（雲端呼叫的 meta）、error（分類）"""
    if cloud_client.settings() is None:  # 沒有雲端（Fast Path 直接送來的才會遇到）：交給本機 AI，與沒有雲端時相同（U13）
        if ctx is None:
            return "⚠️ 沒有雲端，沒辦法直接回答"
        ctx.escalate = {"kind": "slow"}
        return "🔎 沒有雲端，交給本機 AI"
    info: dict = ctx.step.setdefault("web", {}) if ctx is not None and ctx.step is not None else {}
    if not search_client.available():  # 有雲端、沒有搜尋金鑰：不讓模型憑記憶回答，開搜尋頁並在後端提醒（U13）
        info["error"] = "no_search_key"
        if ctx is not None and ctx.step is not None:
            ctx.step["web_fallback"] = "no_search_key"
        print("⚠️ [沒有搜尋金鑰（BRAVE_API_KEY），已退回開搜尋頁]")
        return _web_fallback(query, ctx, "no_search_key")
    utterance = (ctx.utterance if ctx is not None else "") or query
    fresh = search_client.freshness_for(utterance)
    if fresh:
        info["freshness"] = fresh
    t = time.perf_counter()
    try:
        try:
            results = search_client.search(query, freshness=fresh)
        except search_client.SearchError as e:
            if not (fresh and e.kind == "no_results"):
                raise
            info["fresh_retry"] = True
            results = search_client.search(query)
    except search_client.SearchError as e:
        info.update(search_ms=(time.perf_counter() - t) * 1000, error=f"search:{e.kind}")
        return _web_fallback(query, ctx, "search")
    info.update(search_ms=(time.perf_counter() - t) * 1000, results=len(results))
    today = today_taipei()
    meta: dict = {}
    t = time.perf_counter()
    try:
        message = cloud_client.chat(cloud_client.web_answer_messages(utterance, results, today), tools=None,
                                    timeout=config.WEB_ANSWER_TIMEOUT_S, max_tokens=config.WEB_ANSWER_MAX_TOKENS, meta=meta,
                                    trip_on_timeout=False)  # 回答那次逾時不熔斷：後面的句子照常走雲端規劃
    except cloud_client.CloudError as e:
        info.update(error=f"cloud:{e}")
        return _web_fallback(query, ctx, "cloud")
    finally:
        info.update(answer_ms=(time.perf_counter() - t) * 1000, cloud=meta)
    answer = message.get("content") or ""
    if meta.get("finish_reason") == "length":  # 碰到上限：內容可能只有半句，不當成答案
        info["error"] = "truncated"
        return _web_fallback(query, ctx, "truncated")
    if cloud_client.is_no_answer(answer):
        info["error"] = "no_answer"
        return _web_fallback(query, ctx, "no_answer")
    sources.STORE.put(results[0].url)
    if ctx is not None:
        ctx.replies.append(f"{answer} {sources.FOLLOWUP}")
        ctx.ai_reply = True
    return f"✅ {answer}"


def open_source(url: str, ctx: Optional[ExecContext] = None) -> str:
    """「要打開來源嗎？」說好：開 web_answer 記下的那筆來源。網址來自搜尋服務、不是模型編的，仍照紅線 5 只收 http／https"""
    if not url.startswith(("http://", "https://")):
        return "❌ 來源網址不合法，沒有開啟"
    browser = (ctx.last_browser or front_app(ctx, "browser")) if ctx is not None else None
    res = web_search_or_open_url(url, browser=browser)
    if ctx is not None:
        mark_page(ctx, res)
    host = urllib.parse.urlsplit(url).hostname or ""
    return f"❌ 開啟來源失敗（{host}）" if res.startswith(_FAIL_PREFIXES) else f"✅ 已打開來源：{host}"
