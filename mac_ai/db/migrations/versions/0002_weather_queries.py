"""天氣常問縣市：只追加的紀錄，沒說縣市時用統計決定（計畫 docs/plans/completed/2026-11-21-web-answer.md §4.7）"""
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    op.execute("""CREATE TABLE weather_queries (  -- 只追加、永久保存；city 是正規化後的縣市名（例如「臺北市」）
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, city TEXT NOT NULL)""")
    op.execute("CREATE INDEX weather_queries_ts ON weather_queries(ts)")
    op.execute("PRAGMA user_version = 2")


def downgrade():
    op.execute("DROP TABLE weather_queries")
    op.execute("PRAGMA user_version = 1")
