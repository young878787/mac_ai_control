"""查／找／搜尋的常搜名詞：只追加的紀錄，聯合搜尋依它決定先列哪一邊、要不要直接用（計畫 docs/plans/active/2026-11-23-query-routing.md §4.4）"""
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade():
    op.execute("""CREATE TABLE search_terms (  -- 只追加；term 是 _compact 後的查詢字；side＝local／web；signal＝explicit／picked
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, term TEXT NOT NULL, side TEXT NOT NULL, signal TEXT NOT NULL,
  path TEXT)  -- 只有本機項目有完整路徑；網路一律 NULL（不存網址，Brave 條款）""")
    op.execute("CREATE INDEX search_terms_term ON search_terms(term, ts)")
    op.execute("PRAGMA user_version = 3")


def downgrade():
    op.execute("DROP TABLE search_terms")
    op.execute("PRAGMA user_version = 2")
