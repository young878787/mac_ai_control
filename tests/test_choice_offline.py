"""多筆結果的選擇清單（A5）：過期、序號、清單編號、預設動作、取代、失敗時保留。時鐘由測試注入。"""
import mac_ai.router as r
from mac_ai.router import choices
from mac_ai.router import local_actions as la
from mac_ai.router.actions import ExecContext

import pytest


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(choices, "STORE", choices.ChoiceStore(clock=lambda: now[0], ttl_s=60))
    return now


@pytest.fixture
def three(find_env):
    paths = [find_env.touch(f"docs/報告{c}.pdf") for c in "ABC"]
    return paths


def put(paths, action="reveal", query="報告"):
    return choices.STORE.put(query, action, [str(p) for p in paths], len(paths))


def test_list_expires_after_ttl(clock, three):
    put(three)
    clock[0] += 59
    assert choices.STORE.get() is not None  # 第 59 秒還在
    clock[0] += 2
    assert choices.STORE.get() is None  # 第 61 秒失效
    assert la.pick_choice(2) == "⚠️ 沒有可選的清單（已過期或尚未搜尋）"


def test_pick_at_59s_works_and_at_61s_reports_expired(clock, three, find_env):
    put(three)
    clock[0] += 59
    assert la.pick_choice(2).startswith("✅ 已在 Finder 中顯示：報告B.pdf")
    put(three)
    clock[0] += 61
    assert la.pick_choice(2).startswith("⚠️ 沒有可選的清單")
    assert len(find_env.opens()) == 1


def test_pick_uses_default_action_and_can_override(clock, three, find_env):
    put(three, action="open")
    la.pick_choice(1)
    assert find_env.opens() == [["open", str(three[0])]]
    put(three, action="open")
    la.pick_choice(2, action="reveal")
    assert find_env.opens()[-1] == ["open", "-R", str(three[1])]
    put(three, action="reveal")
    la.pick_choice(3, action="open")
    assert find_env.opens()[-1] == ["open", str(three[2])]


def test_pick_last(clock, three, find_env):
    put(three)
    la.pick_choice(-1)
    assert find_env.opens() == [["open", "-R", str(three[2])]]


def test_pick_dangerous_file_with_open_only_reveals(clock, find_env):
    script = find_env.touch("tools/a.command", mode=0o755)
    put([script, find_env.touch("tools/b.txt")], action="open")
    assert "可執行檔" in la.pick_choice(1)
    assert find_env.opens() == [["open", "-R", str(script)]]


def test_pick_out_of_range_reports_and_keeps_list(clock, three, find_env):
    put(three)
    assert la.pick_choice(4) == "⚠️ 清單只有 3 筆"
    assert la.pick_choice(0) == "⚠️ 清單只有 3 筆"
    assert find_env.opens() == [] and choices.STORE.get() is not None


def test_stale_list_id_is_rejected(clock, three, find_env):
    old = put(three)
    new = put(three[:2])  # 新搜尋取代舊清單
    assert new.list_id != old.list_id
    assert la.pick_choice(1, list_id=old.list_id) == "⚠️ 清單已更新，請重選"
    assert find_env.opens() == []
    assert la.pick_choice(1, list_id=new.list_id).startswith("✅")


def test_new_search_replaces_old_list(clock, three):
    put(three, query="舊")
    put(three[:2], query="新")
    lst = choices.STORE.get()
    assert lst.query == "新" and len(lst.paths) == 2
    assert la.pick_choice(3) == "⚠️ 清單只有 2 筆"


def test_successful_pick_clears_the_list(clock, three):
    put(three)
    ctx = ExecContext()
    ctx.step = {}
    la.pick_choice(1, ctx=ctx)
    assert choices.STORE.get() is None and ctx.step["choices"]["closed"] is True
    assert la.pick_choice(1).startswith("⚠️ 沒有可選的清單")


def test_failed_pick_keeps_the_list_so_user_can_retry(clock, three, monkeypatch):
    put(three)
    monkeypatch.setattr(la, "_run", lambda cmd, timeout=None: (False, "boom"))
    assert la.pick_choice(1).startswith("❌")
    assert choices.STORE.get() is not None


def test_payload_never_contains_full_paths(clock, three, find_env):
    lst = put(three)
    payload = choices.STORE.payload(lst)
    assert payload["expires_in_s"] == 60 and payload["total"] == 3
    assert payload["items"][0] == {"n": 1, "name": "報告A.pdf", "dir": "~/docs", "kind": "pdf"}
    clock[0] += 20
    assert choices.STORE.payload(lst)["expires_in_s"] == 40
    assert str(find_env.home) not in repr(payload)
    assert choices.short_dir("/opt/x/y.txt") == "/opt/x"
    assert choices.short_dir(str(find_env.home / "y.txt")) == "~"


def test_voice_pick_goes_through_pipeline(clock, three, find_env):
    put(three, action="open")
    out = r.process_voice_command("在 Finder 顯示第三個")
    assert find_env.opens() == [["open", "-R", str(three[2])]]
    assert out["steps"][0]["intent"]["domain"] == "choice" and out["steps"][0]["status"] == "ok"
    out = r.process_voice_command("第一個")  # 上一步選成功後清單已清空
    assert out["steps"][0]["status"] == "exec_failed"


def test_process_choice_for_ui_clicks(clock, three, find_env):
    lst = put(three)
    out = r.process_choice({"list_id": lst.list_id, "n": 2, "action": "open"})
    assert find_env.opens() == [["open", str(three[1])]]
    assert out["steps"][0]["status"] == "ok" and out["total_ms"] >= 0 and set(out) == {"stages", "steps", "replies", "total_ms"}
    lst = put(three)
    out = r.process_choice({"list_id": lst.list_id + 5, "n": 1, "action": "reveal"})
    assert out["steps"][0]["status"] == "exec_failed" and out["steps"][0]["logs"] == ["⚠️ 清單已更新，請重選"]
    assert len(find_env.opens()) == 1
