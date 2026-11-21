"""${message}"""
from alembic import op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}


def upgrade():
    ${upgrades if upgrades else "pass"}
    # 結尾要寫 op.execute("PRAGMA user_version = N")（日後可移除 Alembic，見計畫 §4.4）


def downgrade():
    ${downgrades if downgrades else "pass"}
