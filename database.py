import aiosqlite
from config import DB_PATH


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                sku_id TEXT DEFAULT NULL,
                title TEXT DEFAULT 'Uzum Mahsuloti',
                photo_url TEXT DEFAULT NULL,
                target_price INTEGER NOT NULL,
                last_price INTEGER DEFAULT NULL,
                available_amount INTEGER DEFAULT 0,
                mode TEXT NOT NULL DEFAULT 'MODE_A',
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP DEFAULT NULL
            )
        ''')
        # Agar eski bazada sku_id ustuni bo'lmasa, qo'shish
        try:
            await db.execute("ALTER TABLE products ADD COLUMN sku_id TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE products ADD COLUMN photo_url TEXT DEFAULT NULL")
        except Exception:
            pass
        await db.commit()


async def add_product(user_id: int, url: str, target_price: int, mode: str, sku_id: str = None, title: str = "Uzum Mahsuloti", photo_url: str = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            '''
            INSERT INTO products (user_id, url, sku_id, title, photo_url, target_price, mode, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
            ''',
            (user_id, url, sku_id, title, photo_url, target_price, mode)
        )
        await db.commit()
        return cursor.lastrowid


async def get_active_products() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM products WHERE status = 'ACTIVE' ORDER BY id ASC"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_user_products(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM products WHERE user_id = ? ORDER BY id DESC LIMIT 20",
            (user_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_product_by_id(product_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM products WHERE id = ?",
            (product_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def update_product_metrics(product_id: int, title: str, last_price: int, available_amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            '''
            UPDATE products
            SET title = ?, last_price = ?, available_amount = ?
            WHERE id = ?
            ''',
            (title, last_price, available_amount, product_id)
        )
        await db.commit()


async def acquire_safety_lock(product_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            '''
            UPDATE products
            SET status = 'PROCESSING'
            WHERE id = ? AND status IN ('ACTIVE', 'NOTIFIED')
            ''',
            (product_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def mark_status(product_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        if status == 'COMPLETED':
            await db.execute(
                '''
                UPDATE products
                SET status = 'COMPLETED', completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''',
                (product_id,)
            )
        else:
            await db.execute(
                "UPDATE products SET status = ? WHERE id = ?",
                (status, product_id)
            )
        await db.commit()


async def delete_product(product_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM products WHERE id = ? AND user_id = ?",
            (product_id, user_id)
        )
        await db.commit()
        return cursor.rowcount > 0
