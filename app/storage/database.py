"""SQLite 连接管理 + 建表。"""

import sqlite3
from pathlib import Path
from typing import Optional


def get_db_path(db_path: Optional[str] = None) -> str:
    """获取数据库文件路径。

    Args:
        db_path: 自定义路径，默认 data/stock_cache.db

    Returns:
        数据库文件的绝对路径
    """
    if db_path is None:
        db_path = "data/stock_cache.db"
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p.resolve())


def create_connection(db_path: str) -> sqlite3.Connection:
    """创建 SQLite 连接，启用 WAL 模式。

    Args:
        db_path: 数据库文件路径

    Returns:
        SQLite 连接对象
    """
    # 确保父目录存在
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """初始化数据库表结构（幂等）。

    Args:
        conn: SQLite 连接对象
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_cache (
            cache_key   TEXT PRIMARY KEY,
            data_type   TEXT NOT NULL,
            stock_code  TEXT NOT NULL,
            data_json   TEXT NOT NULL,
            source      TEXT NOT NULL,
            fetched_at  TEXT NOT NULL,
            expires_at  TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cache_stock
        ON stock_cache(stock_code, data_type)
    """)
    conn.commit()
