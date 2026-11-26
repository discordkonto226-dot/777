from contextlib import contextmanager
import sqlite3
import time
from pathlib import Path
from typing import Iterator

DB_PATH = Path(__file__).resolve().parent / "kasyno.db"
STARTING_BALANCE = 1000
DAILY_REWARD = 500
DAILY_COOLDOWN_SECONDS = 24 * 60 * 60


@contextmanager
def _get_connection() -> Iterator[sqlite3.Connection]:
    """Zwraca połączenie z lokalną bazą danych SQLite i zamyka je po użyciu."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Tworzy tabelę graczy w bazie danych, jeśli jeszcze nie istnieje."""
    with _get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                balance INTEGER NOT NULL DEFAULT 1000,
                last_daily REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        conn.commit()


def ensure_player(guild_id: int, user_id: int) -> int:
    """Zapewnia istnienie konta gracza i zwraca jego aktualne saldo."""
    with _get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO players (guild_id, user_id, balance, last_daily)
            VALUES (?, ?, ?, 0)
            """,
            (guild_id, user_id, STARTING_BALANCE),
        )
        row = conn.execute(
            "SELECT balance FROM players WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        conn.commit()
        return int(row["balance"])


def get_balance(guild_id: int, user_id: int) -> int:
    """Zwraca aktualne saldo gracza (tworzy konto startowe, jeśli nie istnieje)."""
    return ensure_player(guild_id, user_id)


def try_deduct_bet(guild_id: int, user_id: int, amount: int) -> tuple[bool, int]:
    """
    Atomowo sprawdza saldo i odejmuje stawkę, jeśli gracz ma wystarczającą liczbę monet.
    Zwraca (czy_powodzenie, aktualne_saldo).
    """
    with _get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO players (guild_id, user_id, balance, last_daily)
            VALUES (?, ?, ?, 0)
            """,
            (guild_id, user_id, STARTING_BALANCE),
        )
        cursor = conn.execute(
            """
            UPDATE players
            SET balance = balance - ?
            WHERE guild_id = ? AND user_id = ? AND balance >= ?
            """,
            (amount, guild_id, user_id, amount),
        )
        row = conn.execute(
            "SELECT balance FROM players WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        conn.commit()
        return cursor.rowcount == 1, int(row["balance"])


def add_coins(guild_id: int, user_id: int, amount: int) -> int:
    """Dodaje monety do konta gracza i zwraca nowe saldo."""
    with _get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO players (guild_id, user_id, balance, last_daily)
            VALUES (?, ?, ?, 0)
            """,
            (guild_id, user_id, STARTING_BALANCE),
        )
        conn.execute(
            """
            UPDATE players
            SET balance = balance + ?
            WHERE guild_id = ? AND user_id = ?
            """,
            (amount, guild_id, user_id),
        )
        row = conn.execute(
            "SELECT balance FROM players WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        conn.commit()
        return int(row["balance"])


def claim_daily(guild_id: int, user_id: int) -> tuple[bool, int, int]:
    """
    Próbuje odebrać dzienną nagrodę (500 monet raz na 24h).
    Zwraca (czy_odebrano, nowe_saldo, pozostaly_czas_w_sekundach).
    """
    now = time.time()
    with _get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO players (guild_id, user_id, balance, last_daily)
            VALUES (?, ?, ?, 0)
            """,
            (guild_id, user_id, STARTING_BALANCE),
        )
        row = conn.execute(
            "SELECT balance, last_daily FROM players WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()

        elapsed = now - float(row["last_daily"])
        if elapsed < DAILY_COOLDOWN_SECONDS:
            remaining = int(DAILY_COOLDOWN_SECONDS - elapsed)
            return False, int(row["balance"]), remaining

        new_balance = int(row["balance"]) + DAILY_REWARD
        conn.execute(
            """
            UPDATE players
            SET balance = ?, last_daily = ?
            WHERE guild_id = ? AND user_id = ?
            """,
            (new_balance, now, guild_id, user_id),
        )
        conn.commit()
        return True, new_balance, 0


def get_top_players(guild_id: int, limit: int = 10) -> list[tuple[int, int]]:
    """Zwraca listę (user_id, balance) dla najlepszych graczy na danym serwerze."""
    with _get_connection() as conn:
        rows = conn.execute(
            """
            SELECT user_id, balance
            FROM players
            WHERE guild_id = ?
            ORDER BY balance DESC, user_id ASC
            LIMIT ?
            """,
            (guild_id, limit),
        ).fetchall()
        return [(int(row["user_id"]), int(row["balance"])) for row in rows]
