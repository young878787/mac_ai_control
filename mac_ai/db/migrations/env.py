"""Alembic 環境（以程式組設定，不放 alembic.ini）。連線由 ensure_schema 放進 config.attributes["connection"]。
render_as_batch：SQLite 的 ALTER 限制用 batch 處理。"""
from alembic import context

config = context.config
connection = config.attributes.get("connection")
if connection is None:
    raise RuntimeError("請透過 mac_ai.db.ensure_schema 執行 migration")

context.configure(connection=connection, target_metadata=None, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
