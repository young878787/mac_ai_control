"""music 偏好資料庫初版（計畫 §4.4）"""
from alembic import op

revision = "0001"
down_revision = None

_TABLES = """
CREATE TABLE events (               -- 只追加、永久保存；terms 與統計都能從這裡重算
  id INTEGER PRIMARY KEY, ts REAL NOT NULL,
  kind TEXT NOT NULL,               -- request|play|replace|next|prev|pause|resume|like|dislike|fallback|correct
  utterance TEXT, query TEXT, corrected TEXT,   -- 原句（給矯正分析用）、清理後、矯正後
  video_id TEXT, source TEXT,       -- cache|mv|ytm|web|page|ax|mediakey|app
  latency_ms REAL, ok INTEGER NOT NULL)
"""


def upgrade():
    op.execute(_TABLES)
    op.execute("CREATE INDEX events_ts ON events(ts)")
    op.execute("""CREATE TABLE videos (video_id TEXT PRIMARY KEY, title TEXT, channel TEXT, channel_id TEXT,
  first_seen REAL, last_seen REAL)""")
    op.execute("""CREATE TABLE queries (query TEXT PRIMARY KEY, video_id TEXT NOT NULL, results TEXT,  -- results：候選 id（JSON，最多 6 筆）
  updated REAL NOT NULL, pinned INTEGER NOT NULL DEFAULT 0)""")  # pinned：使用者糾正過
    op.execute("""CREATE TABLE terms (term TEXT PRIMARY KEY, kind TEXT NOT NULL,   -- artist|title|query
  pinyin TEXT NOT NULL, weight REAL NOT NULL, plays INTEGER NOT NULL,
  liked INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL)""")
    op.execute("CREATE INDEX terms_pinyin ON terms(pinyin)")
    op.execute("CREATE TABLE maintenance (key TEXT PRIMARY KEY, value TEXT)")  # last_run、web／ytm 的 clientVersion、需要更新的旗標
    op.execute("PRAGMA user_version = 1")


def downgrade():
    for t in ("maintenance", "terms", "queries", "videos", "events"):
        op.execute(f"DROP TABLE {t}")
    op.execute("PRAGMA user_version = 0")
