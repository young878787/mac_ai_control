// 與後端（mac_ai/ui_bus.py、config.py、router/pipeline.py）共用的合約。純資料，main 與畫面兩個行程都可以 import。
// 後端是來源；改後端時要一起改這裡，tests/unit/test_ui_contract.py 會比對（不一致就失敗）。

/** UI 能送給後端的訊息型別（ui_bus.UI_MESSAGE_TYPES） */
export const UI_MESSAGE_TYPES = Object.freeze(['command', 'wake', 'pause', 'quit', 'choose']);
/** 打字指令的最長字數（config.UI_COMMAND_MAX_CHARS） */
export const COMMAND_MAX_CHARS = 200;
/** 搜尋結果清單最多幾列，也是可選的最大序號（config.FIND_RESULTS_MAX） */
export const CHOICES_MAX = 5;
/** result 事件的 expand 欄位可能的值（pipeline.expand_reason 的回傳） */
export const EXPAND_REASONS = Object.freeze(['screen', 'chat', 'complex']);
