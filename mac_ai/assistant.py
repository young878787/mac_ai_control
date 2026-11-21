"""語音助手主流程：辨識事件 → 喚醒詞閘門 → 指令佇列 → 依序執行。"""
import functools
import json
import queue
import threading
import time
import traceback
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence

from mac_ai import config
from mac_ai.stt import latency_log
from mac_ai.stt.client import SttClient
from mac_ai.stt.completeness import classify
from mac_ai.stt.endpoint import EndpointDetector
from mac_ai.stt.latency_log import JsonlLog
from mac_ai.stt.vad import SileroVad
from mac_ai.ui_bus import UiBus
from mac_ai.wake import WakeGate, split_wake

_CLEAR_LINE = "\r\033[K"
MAINT_POLL_S = 60.0  # 點歌定時整理的計時器多久看一次有沒有到期（到期的間隔是 config.MUSIC_MAINT_INTERVAL_S）
WORKER_POLL_S = 0.2  # 指令執行緒等佇列時每隔多久檢查一次 stop（測試調小，避免停止時空等）
_FAILED_STEP_STATUS = ("exec_failed", "llm_unavailable", "llm_error")
_LOGGED_STEP_KEYS = ("kind", "text", "status", "ms", "llm_ms", "intent", "match", "route", "cloud", "cloud_fallback",
                     "vision_cloud", "memory_turns", "music")  # 寫進 commands.jsonl 的 step 欄位


def _ignore(event: dict) -> None:
    pass


class SpokenCommand(str):
    """語音指令的文字（行為與 str 完全相同），多帶兩個紀錄用的欄位：
    utt_id＝STT 這句 final 的編號（對應 latency.jsonl 同一句）；enqueued_at＝進佇列的 time.monotonic()（算排隊時間）。
    UI 打字的指令與清單點選沒有這兩個欄位"""
    utt_id: Optional[int] = None
    enqueued_at: float = 0.0


def result_event(command: str, outcome) -> dict:
    """把 process_voice_command 的回傳整理成給 UI 的 result 事件；ok＝沒有任何一步失敗"""
    steps, replies, total_ms = [], [], 0.0
    if isinstance(outcome, dict):
        steps = [{"kind": st.get("kind"), "text": st.get("text"), "status": st.get("status"),
                  "logs": [str(x) for x in st.get("logs", [])],
                  **({"route": st["route"]} if st.get("route") else {}),
                  **({"ms": round(st["ms"], 1)} if isinstance(st.get("ms"), (int, float)) else {})}
                 for st in outcome.get("steps", [])]
        replies = [str(r) for r in outcome.get("replies", [])]
        total_ms = outcome.get("total_ms", 0.0)
    event = {"type": "result", "command": command, "ok": all(st["status"] not in _FAILED_STEP_STATUS for st in steps),
             "steps": steps, "replies": replies, "total_ms": total_ms}
    # 多筆搜尋結果的選擇清單（或「清單已關閉」的通知）；完整路徑不會出現在這裡
    choices = next((st["choices"] for st in reversed(outcome.get("steps", [])) if st.get("choices")), None) if isinstance(outcome, dict) else None
    if choices:
        event["choices"] = choices
    if isinstance(outcome, dict) and outcome.get("expand"):  # 聊天室主動展開的原因（screen／chat／complex）；沒有就不帶這個欄位
        event["expand"] = outcome["expand"]
    return event


def command_record(event: dict, outcome) -> dict:
    """一個指令的路由與執行結果（寫進 commands.jsonl）：result 事件的內容，加上追查用的 stages 與各步 ms／intent／match。
    家目錄換成 ~、不寫 choices（同 UI「完整路徑不出本程序」的原則）"""
    raw_steps = outcome.get("steps", []) if isinstance(outcome, dict) else []
    steps = [{**{k: st[k] for k in _LOGGED_STEP_KEYS if st.get(k) is not None}, "logs": [str(x) for x in st.get("logs", [])]}
             for st in raw_steps]
    record = {"command": event["command"], "ok": event["ok"], "total_ms": event["total_ms"],
              "stages": outcome.get("stages", {}) if isinstance(outcome, dict) else {}, "steps": steps, "replies": event["replies"]}
    if event.get("expand"):
        record["expand"] = event["expand"]
    text = json.dumps(record, ensure_ascii=False, default=str).replace(str(Path.home()), "~")
    return json.loads(text)


def _write_command_log(record: dict) -> None:
    try:
        latency_log.JsonlLog(config.COMMAND_LOG).write(record)
    except (OSError, TypeError, ValueError):
        pass  # 紀錄寫入失敗不影響指令流程


class CommandRouter:
    """STT 事件的處理器：utterance／final 先過喚醒詞閘門，通過的指令放進佇列；partial 只顯示，不執行"""

    def __init__(self, gate: WakeGate, commands: "queue.Queue[str]", say: Callable[[str], None] = print,
                 show_partial: Optional[Callable[[str], None]] = None,
                 publish: Optional[Callable[[dict], None]] = None, on_quit: Optional[Callable[[], None]] = None):
        self.gate = gate
        self.commands = commands
        self._say = say
        self._show_partial = show_partial
        self._publish = publish or _ignore  # 給桌面角色介面的事件；沒有 UI 時什麼都不做
        self._on_quit = on_quit
        self.paused = False  # UI 要求暫停聽指令：辨識結果一律丟棄
        self._lock = threading.Lock()  # gate 會被 STT 執行緒與 UI 訊息執行緒同時用到

    def say(self, message: str) -> None:
        # 顯示 partial 時游標停在同一行，先清掉再印，才不會接在 partial 後面
        self._say((_CLEAR_LINE if self._show_partial else "") + message)

    def __call__(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "partial":
            if self._show_partial and event.get("text"):
                self._show_partial(event["text"])
            if event.get("text") and not self.paused:
                self._publish({"type": "partial", "text": event["text"],
                               "wake": split_wake(event["text"], self.gate.words, self.gate.greetings) is not None})
        elif kind in ("utterance", "final"):
            text = (event.get("text") or "").strip()
            if text:
                self._on_text(text, event.get("utt_id"))
        elif kind == "connected":
            self.say("🎧 已連上 STT，開始收音")
            self._publish({"type": "stt", "state": "connected", "message": ""})
        elif kind == "disconnected":
            self.say(f"⚠️ STT 連線中斷（{event.get('message')}），重新連線中…")
            self._publish({"type": "stt", "state": "disconnected", "message": str(event.get("message"))})
        elif kind == "error":
            self.say(f"⚠️ STT 錯誤：{event.get('code')} {event.get('message')}")
            self._publish({"type": "stt", "state": "error", "message": f"{event.get('code')} {event.get('message')}"})

    def _on_text(self, text: str, utt_id: Optional[int] = None) -> None:
        if self.paused:
            return
        with self._lock:
            result = self.gate.feed(text)
            remaining = self.gate.session_remaining()
        self._publish({"type": "heard", "text": text, "status": result.status, "command": result.command})
        if result.status in ("command", "armed"):
            self._publish({"type": "session", "remaining_s": remaining})
        if result.status == "command":
            command = SpokenCommand(result.command)
            command.utt_id, command.enqueued_at = utt_id, time.monotonic()
            self.commands.put(command)
        elif result.status == "armed":
            self.say(f"👂 已喚醒，{self.gate.session_s:g} 秒內不用再說喚醒詞（每句重新計時）")
        elif self.gate.enabled:
            self.say(f"\033[90m（未喚醒）{text}\033[0m")

    # ---------- 桌面角色介面 ----------
    def hello_state(self) -> dict:
        return {"wake_enabled": self.gate.enabled, "session_s": self.gate.session_s, "paused": self.paused,
                "remaining_s": self.gate.session_remaining()}

    def on_ui_message(self, msg: dict) -> None:
        """處理 UI 送來、已驗證過的訊息（在 UI 匯流排的執行緒上呼叫）"""
        kind = msg["type"]
        if kind == "command":  # 直接執行，不經喚醒詞閘門、不影響會話計時（同 --text）
            self.commands.put(msg["text"])
        elif kind == "choose":  # 點選清單：和語音指令排同一個佇列，依序執行
            self.commands.put(msg)
        elif kind == "wake":
            if self.gate.enabled and not self.paused:
                with self._lock:
                    self.gate.start_session()
                    remaining = self.gate.session_remaining()
                self._publish({"type": "session", "remaining_s": remaining})
        elif kind == "pause":
            self.paused = msg["value"]
            if self.paused:
                with self._lock:
                    self.gate.end_session()
            self._publish({"type": "paused", "value": self.paused})
            if self.paused:
                self._publish({"type": "session", "remaining_s": 0.0})
        elif kind == "quit" and self._on_quit:
            self._on_quit()


@dataclass
class QueuedJob:
    """排進指令佇列、在 command_worker 執行緒上執行的內部工作（例如點歌定時整理）。不是使用者指令：
    不發 executing／result 事件、不寫 commands.jsonl；出錯只印出錯誤。existing 的字串（指令）與 dict（點選清單）行為不變"""
    name: str
    run: Callable[[], None]


class MusicMaintenance:
    """點歌定時整理（計畫 §4.5）：啟動時跑一次，之後每 schedule.interval 一次。計時器執行緒只負責把 QueuedJob 排進指令佇列，
    資料庫的讀寫（maintain、store_versions）都在 command_worker 上做（唯一的寫入者）；網路（fetch_versions）在一條短命的背景執行緒，
    抓到結果再排回佇列由 worker 寫入。三個函式由呼叫端注入（main.py），這裡不 import router。
    maintain(conn, now)；fetch_versions()→{"web","ytm"}；store_versions(conn, d)；schedule：music_prefs.MaintenanceSchedule；clock：秒"""

    def __init__(self, db, *, maintain: Callable[[Any, float], object], fetch_versions: Callable[[], Dict[str, str]],
                 store_versions: Callable[[Any, Dict[str, str]], object], schedule, clock: Callable[[], float] = time.time,
                 poll_s: float = MAINT_POLL_S, ready_poll_s: float = 0.5):
        self.db = db
        self._maintain, self._fetch, self._store = maintain, fetch_versions, store_versions
        self.schedule, self.clock = schedule, clock
        self.poll_s, self.ready_poll_s = poll_s, ready_poll_s
        self._commands: Optional["queue.Queue[object]"] = None
        self._lock = threading.Lock()
        self._pending = False  # 已經排進佇列、還沒做完

    def start(self, commands: "queue.Queue[object]", stop: threading.Event) -> threading.Thread:
        self._commands = commands
        t = threading.Thread(target=self._loop, args=(stop,), name="music-maintenance-timer", daemon=True)
        t.start()
        return t

    def _loop(self, stop: threading.Event) -> None:
        while not stop.is_set() and not self.db.wait_ready(self.ready_poll_s):  # 資料庫還在背景準備：準備好就立刻跑第一次
            pass
        while not stop.is_set():
            self.tick()
            stop.wait(self.poll_s)

    def tick(self) -> bool:
        """（計時器執行緒）到期、前一個工作做完了、資料庫可用，才排一個整理工作進佇列。回傳有沒有排"""
        with self._lock:
            if self._pending or self._commands is None or not self.db.ready or not self.schedule.due(self.clock()):
                return False
            self._pending = True
        self._commands.put(QueuedJob("music-maintenance", self._run_maintenance))
        return True

    def _run_maintenance(self) -> None:
        """（command_worker）terms 衰減與刪除；成功才記下這次時間，然後在背景抓 clientVersion"""
        try:
            conn = self.db.conn()
            if conn is None:
                return
            now = self.clock()
            self._maintain(conn, now)
            with self._lock:
                self.schedule.mark(now)
        finally:
            with self._lock:
                self._pending = False
        threading.Thread(target=self._fetch_versions, name="music-client-version", daemon=True).start()

    def _fetch_versions(self) -> None:
        """（背景執行緒）只做網路，不碰資料庫；結果排回佇列"""
        try:
            versions = self._fetch()
        except Exception:  # noqa: BLE001  讀不到就等下一輪
            return
        if versions and self._commands is not None:
            self._commands.put(QueuedJob("music-client-versions", lambda: self._store_versions(versions)))

    def _store_versions(self, versions: Dict[str, str]) -> None:
        """（command_worker）"""
        conn = self.db.conn()
        if conn is not None:
            self._store(conn, versions)


def command_worker(commands: "queue.Queue[object]", execute: Callable[[str], object], stop: threading.Event,
                   publish: Optional[Callable[[dict], None]] = None,
                   choose: Optional[Callable[[dict], object]] = None,
                   command_log: Optional[Callable[[dict], None]] = None) -> None:
    """依序執行佇列裡的指令（字串＝語音／打字指令；dict＝UI 點選清單；QueuedJob＝內部工作）；單一指令出錯只印出錯誤，不讓整個助手停下。
    command_log：每個指令執行完收到一筆 command_record（出錯時是 {command, ok: False, error}）；
    語音指令另帶 utt_id（對應 latency.jsonl 同一句）與 queue_ms（進佇列到開始執行的等待）"""
    publish = publish or _ignore
    command_log = command_log or _ignore
    while not stop.is_set():
        try:
            item = commands.get(timeout=WORKER_POLL_S)
        except queue.Empty:
            continue
        if isinstance(item, QueuedJob):  # 內部工作：不發 UI 事件、不寫指令紀錄
            try:
                item.run()
            except Exception:
                traceback.print_exc()
            continue
        is_choice = isinstance(item, dict)
        command = f"選擇第 {item['n']} 筆" if is_choice else item
        utt_id = getattr(item, "utt_id", None)
        link = {} if utt_id is None else {"utt_id": utt_id, "queue_ms": (time.monotonic() - item.enqueued_at) * 1000}
        publish({"type": "executing", "command": command})
        try:
            if is_choice:
                if choose is None:
                    raise RuntimeError("沒有設定清單選擇的處理函式")
                outcome = choose(item)
            else:
                outcome = execute(item)
        except Exception as e:
            traceback.print_exc()
            publish({"type": "result", "command": command, "ok": False, "steps": [],
                     "replies": ["執行時發生錯誤"], "total_ms": 0.0})
            command_log({**link, "command": command, "ok": False, "error": f"{type(e).__name__}: {e}"})
        else:
            event = result_event(command, outcome)
            publish(event)
            command_log({**link, **command_record(event, outcome)})


def run_voice_loop(audio, execute: Callable[[str], object], stop: threading.Event, *,
                   url: str = config.STT_WS_URL, hints: Sequence[str] = (), gate: Optional[WakeGate] = None,
                   say: Callable[[str], None] = print, show_partial: Optional[Callable[[str], None]] = None,
                   reconnect_min_s: float = config.STT_RECONNECT_MIN_S,
                   endpoint_mode: str = config.ENDPOINT_MODE, bus: Optional[UiBus] = None,
                   choose: Optional[Callable[[dict], object]] = None,
                   maintenance: Optional[MusicMaintenance] = None) -> None:
    """收音 → STT → 喚醒詞 → 執行，直到 stop 被設定。指令在另一條執行緒依序執行，不擋住收音。
    endpoint_mode="client"：本端神經網路 VAD＋語意判斷說完了就要求 server 收尾，並把每句的計時寫進 logs/runtime/latency.jsonl；每個指令的執行結果寫進 logs/runtime/commands.jsonl。
    bus：桌面角色介面的事件匯流排（已啟動）；有的話把狀態事件廣播出去，並處理 UI 送回來的指令"""
    commands: "queue.Queue[object]" = queue.Queue()
    gate = gate or WakeGate()
    publish = bus.publish if bus is not None else None
    router = CommandRouter(gate, commands, say, show_partial, publish=publish, on_quit=stop.set)
    if bus is not None:
        bus.bind(router.on_ui_message, router.hello_state)
    worker = threading.Thread(target=command_worker, args=(commands, execute, stop, publish, choose, _write_command_log), daemon=True)
    worker.start()
    if maintenance is not None:  # 點歌定時整理：計時器把工作排進同一個佇列，由 worker 執行
        maintenance.start(commands, stop)
    try:
        endpoint_args = {}
        if endpoint_mode == "client":
            # 端點判斷對「只說喚醒詞」的認定要與閘門一致
            endpoint = EndpointDetector(classifier=functools.partial(classify, wake_enabled=gate.enabled))
            endpoint_args = dict(vad=SileroVad(), endpoint=endpoint, flush=True,
                                 vad_flush_s=config.EP_SERVER_BACKUP_FLUSH_S, pre_delay_s=config.EP_FLUSH_PRE_DELAY_S,
                                 latency_sink=JsonlLog().write, send_s=config.EP_SEND_S)
        SttClient(audio, router, url=url, hints=hints, reconnect_min_s=reconnect_min_s, **endpoint_args).run(stop)
    finally:
        stop.set()  # 例外離開（如 Ctrl+C）時也讓指令執行緒結束
        worker.join(timeout=2)
