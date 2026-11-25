"""常駐資料：使用者明說過的個人資料（目前只有 home_city 居住地），天氣預設先看它（計畫 docs/plans/completed/2026-11-24-weather-city-and-answer-timeout.md §3.1）"""
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade():
    op.execute("""CREATE TABLE profile (  -- 一個鍵一列，再說一次就覆寫；只記使用者明說的，不由統計或模型推出
  key TEXT PRIMARY KEY,     -- 目前只有 home_city
  value TEXT NOT NULL,      -- home_city：正規化後的縣市名（例如「臺北市」），與 weather_queries.city 同一套
  source TEXT NOT NULL,     -- said（使用者明說：我住 X）／cold_start_reply（冷啟動後回答的縣市）
  updated_at REAL NOT NULL)""")
    op.execute("PRAGMA user_version = 4")


def downgrade():
    op.execute("DROP TABLE profile")
    op.execute("PRAGMA user_version = 3")
