"""語音助手啟動入口：啟動 LLM 與 STT 服務，收音 → 喚醒詞 → 執行指令。

    python main.py                  啟動全部服務並開始收音（Ctrl+C 結束）
    python main.py --no-stt         只啟動 LLM 服務（例如跑 pytest -m live），不收音
    python main.py --text "打開小算盤"  直接送入文字指令後結束（不啟動任何服務、不檢查喚醒詞）
    python main.py --ui             同時顯示桌面角色（Electron）；此模式預設啟用喚醒詞
"""
import argparse
import secrets
import signal
import sys
import threading
from typing import List, Optional

import build_vocab
from mac_ai import config
from mac_ai.assistant import run_voice_loop
from mac_ai.router import LEXICON, process_voice_command, warmup_llm
from mac_ai.services import ServiceError, check_ui_ready, llm_service, stop_all, stt_service, ui_service
from mac_ai.stt.mic import MicSource
from mac_ai.ui_bus import UiBus
from mac_ai.wake import WakeGate


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Mac 語音助手：說喚醒詞加上指令即可控制電腦")
    p.add_argument("--text", nargs="+", metavar="文字",
                   help="直接送入文字指令後結束（不啟動任何服務、不檢查喚醒詞；LLM 需已在執行才會走 Slow Path）")
    p.add_argument("--no-stt", action="store_true", help="只啟動 LLM 服務，不啟動 STT、不收音")
    p.add_argument("--device", type=int, default=None, help="麥克風輸入裝置編號（預設系統預設麥克風）")
    p.add_argument("--ui", action="store_true", help="顯示桌面角色（Electron）；此模式預設啟用喚醒詞")
    return p.parse_args(argv)


def report(icon: str, error: ServiceError, note: str = "") -> None:
    """第一行放摘要與提示；服務 log 的尾巴（若有）接在後面"""
    head, _, detail = str(error).partition("\n")
    print(f"{icon} {head}{note}")
    if detail:
        print(detail)


def wait_forever() -> None:
    threading.Event().wait()


def watch_ui(ui, stop: threading.Event) -> None:
    """桌面角色意外結束時警告一次；語音助手繼續執行，不自動重啟 UI"""
    while not stop.wait(1.0):
        if not ui.alive() and not stop.is_set():
            print("\n⚠️ 桌面角色介面已結束，語音助手繼續執行（Ctrl+C 結束）")
            return


def run(args: argparse.Namespace) -> int:
    """啟動服務並進入主迴圈，直到 Ctrl+C（KeyboardInterrupt）。回傳結束碼；服務一律在離開前收掉"""
    if args.ui:
        problem = check_ui_ready()
        if problem:
            print(f"❌ {problem}")
            return 1
    build_vocab.build()  # 確保 STT 詞表與 lexicon.json 同步
    started = []
    mic = None
    bus = None
    stop = threading.Event()
    try:
        print("🚀 啟動服務中…")
        llm = llm_service()
        started.append(llm)
        llm_ok = True
        try:
            llm.start()
        except ServiceError as e:
            report("⚠️", e, "（Fast Path 仍可使用）")
            llm_ok = False
        stt = None
        if not args.no_stt:
            try:
                stt = stt_service()
                started.append(stt)
                stt.start()
            except ServiceError as e:
                report("❌", e)
                return 1

        if llm_ok:
            try:
                llm.wait_ready(config.LLM_START_TIMEOUT_S)
                print("✅ LLM 就緒")
            except ServiceError as e:
                report("⚠️", e, "（Fast Path 仍可使用）")
                llm_ok = False
        if stt is not None:
            try:
                stt.wait_ready(config.STT_START_TIMEOUT_S)
                print("✅ STT 就緒")
            except ServiceError as e:
                report("❌", e)
                return 1
        if llm_ok:
            warm = warmup_llm()
            if warm is not None:
                print(f"✅ LLM 暖機完成：{warm:.2f}s")

        if stt is None:
            print("（--no-stt）LLM 服務執行中，Ctrl+C 結束")
            wait_forever()
            return 0

        voice_args = {}
        if args.ui:
            bus = UiBus(token=secrets.token_urlsafe(32))
            try:
                bus.start()
            except OSError as e:
                print(f"❌ {e}")
                return 1
            ui = ui_service(bus.url, bus.health_url)
            started.append(ui)
            try:
                ui.start()
                ui.wait_ready(config.UI_START_TIMEOUT_S)
                print("✅ 桌面角色就緒")
                threading.Thread(target=watch_ui, args=(ui, stop), daemon=True).start()
            except ServiceError as e:
                report("⚠️", e, "（沒有桌面角色，語音仍可使用）")
            voice_args = {"gate": WakeGate(enabled=True), "bus": bus}

        mic = MicSource(args.device)
        try:
            mic.start()
        except OSError as e:
            print(f"❌ {e}（請確認終端機已授權麥克風）")
            return 1
        if config.WAKE_WORD_ENABLED or args.ui:
            names = "、".join(LEXICON["wake_words"]["names"])
            print(f"🎤 開始收音：說「{names}」加上指令，Ctrl+C 結束")
        else:
            print("🎤 開始收音：喚醒詞已關閉，直接說出指令，Ctrl+C 結束")
        hints = config.STT_HINTS_PATH.read_text(encoding="utf-8").splitlines()
        show_partial = (lambda t: print(f"\r\033[K👂 {t}", end="", flush=True)) if sys.stdout.isatty() else None
        run_voice_loop(mic, process_voice_command, stop, hints=hints, show_partial=show_partial, **voice_args)
        return 0
    finally:
        stop.set()
        if mic is not None:
            mic.close()
        print("\n🛑 關閉服務中…")
        stop_all(started)
        if bus is not None:
            bus.stop()


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.text:
        process_voice_command(" ".join(args.text))
        return 0
    previous = signal.signal(signal.SIGTERM, signal.default_int_handler)  # kill 也走 KeyboardInterrupt 的收尾流程
    try:
        return run(args)
    except KeyboardInterrupt:  # Ctrl+C：服務已在 run() 的 finally 收掉
        return 0
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    sys.exit(main())
