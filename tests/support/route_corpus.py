"""分流語料（端雲協同計畫 §9.2，R 表）：會走到 Slow Path 的句子，標註應該走本機（L）還是雲端（C）。

草稿（2026-11-15）：標註待使用者逐句確認。每句都必須 Fast Path 未命中、不被 G1 擋下（tests/flow/test_route_corpus.py 檢查）。
欄位：(語句, 標註, 類別, 是否含錯字)。類別：ctrl 控制、screen 看畫面、chat 閒聊／知識、screen_c 看畫面＋解釋／除錯／翻譯／總結、mixed 同一步有動作＋聊天"""

L, C = "L", "C"
LABEL = {"ctrl": L, "screen": L, "chat": C, "screen_c": C, "mixed": C}
MIN_COUNT = {"ctrl": 40, "screen": 10, "chat": 40, "screen_c": 10, "mixed": 15}

_CTRL = [
    ("音量條到三十", True), ("打開瀏覽氣查台北天氣", True), ("開申色模式", True), ("幫我鎖定瑩幕", True),
    ("把螢幕跳暗一點", True), ("把聲因關掉", True), ("幫我開瀏覽氣看 YouTube", True), ("打開備望錄", True),
    ("螢幕太亮了眼睛好痛", False), ("聲音有點大聲", False), ("可以幫我開小算盤嗎", False),
    ("麻煩把 Spotify 收起來", False), ("電腦先幫我鎖起來", False), ("我要用暗色主題", False),
    ("幫我把 volume 調小聲一點", False), ("幫我上 YouTube 找 lofi music", False), ("幫我把 Terminal 開起來", False),
    ("音量給我調到最大", False), ("把喇叭靜音", False), ("亮度拉滿", False), ("開一下 VS Code 寫程式", False),
    ("把整個聲音關小一點啦", False), ("幫我開瀏覽器看一下新聞", False), ("我想聽音樂幫我開 Spotify", False),
    ("螢幕暗一點比較好", False), ("把電腦螢幕鎖上", False), ("幫我開那個記事本 app", False),
    ("有點吵幫我小聲一點", False), ("太暗了看不清楚", False), ("幫我開個新的瀏覽器查 MLX 文件", False),
    ("讓螢幕亮一些", False), ("把畫面亮度調回正常", False), ("叫 Safari 出來", False), ("幫我 lock 一下電腦", False),
    ("打開 visual studio code 那個編輯器", False), ("把 dark mode 打開", False), ("我要離開座位一下幫我鎖螢幕", False),
    ("把音量降到二十", False), ("幫我開那個寫 code 的軟體", False), ("讓電腦安靜一點", False),
    ("幫我把畫面弄成黑色主題", False), ("開一下 Finder 那個檔案總管", False), ("音量小聲點好嗎", False),
]
_SCREEN = [
    ("截圖看一下畫面", False), ("看一下這個頁面寫了啥", False), ("read 一下螢幕上的字", False),
    ("幫我看一下 screen 上面有什麼", False), ("畫面上那個視窗寫了什麼", False), ("幫我瞄一下螢幕", False),
    ("現在網頁上寫什麼", False), ("幫我讀螢幕上的字", False), ("我現在畫面上是什麼", False),
    ("screen 上寫了什麼", False), ("這個網頁的內容幫我念出來", False), ("幫我辨識一下圖片", False),
    ("瞄一下網頁在寫啥", False), ("幫我瞄一下瑩幕", True),
]
_CHAT = [
    ("講個效話給我聽", True), ("陪我聊聊添", True),
    ("Python 是什麼", False), ("今天心情不錯", False), ("你覺得 React 跟 Vue 哪個比較適合新手", False),
    ("recursion 跟 iteration 差在哪", False), ("我今天好累喔", False), ("你叫什麼名字", False),
    ("推薦我一本好看的小說", False), ("怎麼煮出好吃的蛋炒飯", False), ("為什麼天空是藍色的", False),
    ("Docker 跟 VM 有什麼不同", False), ("幫我想一個專題的名字", False), ("什麼是 machine learning", False),
    ("你覺得我該先學 Rust 還是 Go", False), ("用一句話解釋什麼是遞迴", False), ("我明天要報告好緊張", False),
    ("教我寫一個 Python 的 for 迴圈", False), ("HTTP 跟 HTTPS 差在哪裡", False), ("晚餐吃什麼好", False),
    ("量子電腦是什麼原理", False), ("幫我翻譯 good morning", False), ("寫一首關於秋天的短詩", False),
    ("git rebase 跟 merge 怎麼選", False), ("我最近睡不好怎麼辦", False), ("你會說英文嗎", False),
    ("解釋一下 REST API", False), ("台灣最高的山是哪座", False), ("給我一些讀書的建議", False),
    ("幫我想三個 Instagram 的 caption", False), ("SQL 的 JOIN 有哪幾種", False), ("貓咪為什麼喜歡紙箱", False),
    ("你覺得 AI 會取代工程師嗎", False), ("怎麼跟教授寄信比較有禮貌", False), ("幫我解釋 async await", False),
    ("今天是星期幾", False), ("你好呀", False), ("好無聊喔", False), ("說點鼓勵我的話", False),
    ("LLM 的 token 是什麼", False), ("幫我算一下 23 乘以 47", False), ("我想學吉他要從哪開始", False),
]
_SCREEN_C = [
    ("幫我看一下這個 error 是什麼意思", False), ("把畫面上的英文翻成中文", False), ("幫我總結一下這個網頁", False),
    ("看一下螢幕上的程式哪裡寫錯", False), ("把這頁的重點整理成表格", False), ("幫我 debug 一下畫面上的錯誤", False),
    ("解釋一下螢幕上這張圖表", False), ("這個網頁幫我翻譯成中文", False), ("幫我看畫面上的 code 為什麼跑不動", False),
    ("把螢幕上的會議紀錄整理一下", False), ("螢幕上這段 traceback 在說什麼", False), ("幫我總潔一下這個網頁", True),
]
_MIXED = [
    ("把聲因關掉然後說晚安", True), ("打開瀏覽氣查台北天氣再告訴我要穿什麼", True),
    ("開深色模式再講個笑話", False), ("把螢幕調到最亮再跟我聊聊天", False), ("音量調大一點然後講個冷笑話", False),
    ("幫我開 Spotify 再推薦幾首 lofi", False), ("把喇叭靜音，然後陪我聊一下", False), ("調暗螢幕再說個睡前故事", False),
    ("open Terminal 然後跟我說 git status 怎麼看", False), ("聲音有點大聲，順便講個笑話", False),
    ("螢幕太亮了，然後跟我說怎麼保護眼睛", False), ("開一下 VS Code 寫程式再教我 Python 裝飾器", False),
    ("把喇叭靜音再陪我聊聊", False), ("幫我把 Terminal 開起來再解釋 grep", False),
    ("我要用暗色主題順便推薦幾個 VS Code 主題", False), ("電腦先幫我鎖起來再說聲掰掰", False),
    ("音量給我調到最大再推薦一首搖滾樂", False), ("幫我上 YouTube 找 lofi music 然後說說 lofi 是什麼", False),
    ("亮度拉滿然後跟我說為什麼螢幕會傷眼", False),
]

CORPUS = [(text, LABEL[cat], cat, typo)
          for cat, rows in (("ctrl", _CTRL), ("screen", _SCREEN), ("chat", _CHAT), ("screen_c", _SCREEN_C), ("mixed", _MIXED))
          for text, typo in rows]
