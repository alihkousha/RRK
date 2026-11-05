"""Ad texts stored compressed: zlib with a dictionary trained on the stored texts (3.6x on
a sample, plain zlib 2.5x: the notices share their legal phrasing).  A stored value is
the plain text (rows from before ``python -m database.compact``, or a database without a
dictionary yet) or a blob: one byte naming the ``text_dicts`` row, then the zlib stream.
SQL reads either through ``unzip(full_text)``, which every connection registers."""
import collections
import sqlite3
import zlib
from pathlib import Path

DICT_SIZE = 32 * 1024  # zlib's window: a longer dictionary is never seen


def train(texts: list[str]) -> bytes:
    """A dictionary of the 64-byte pieces most texts share, the commonest last (zlib
    reaches the end of a dictionary most cheaply)."""
    counts = collections.Counter(t[i:i + 64] for t in map(str.encode, texts) for i in range(0, len(t) - 64, 32))
    return b"".join(piece for piece, _ in counts.most_common(DICT_SIZE // 64))[::-1][:DICT_SIZE][::-1]


def pack(text: str | None, dict_id: int, zdict: bytes) -> bytes | str | None:
    if text is None:
        return None
    packer = zlib.compressobj(9, zdict=zdict)
    return bytes([dict_id]) + packer.compress(text.encode()) + packer.flush()


def load_dicts(conn: sqlite3.Connection, schema: str = "main") -> dict[int, bytes]:
    try:
        return dict(conn.execute(f"SELECT id, dict FROM {schema}.text_dicts"))
    except sqlite3.OperationalError:  # an older or read-only database without the table
        return {}


def register(conn: sqlite3.Connection, db: Path, schema: str = "main") -> dict[int, bytes]:
    """Define ``unzip()`` on *conn*, reading the dictionaries of *db*; returns them."""
    dicts = load_dicts(conn, schema)

    def unzip(value):
        if not isinstance(value, bytes):
            return value
        if value[0] not in dicts:  # trained after this connection opened
            with sqlite3.connect(f"{Path(db).resolve().as_uri()}?mode=ro", uri=True) as fresh:
                dicts.update(load_dicts(fresh))
        unpacker = zlib.decompressobj(zdict=dicts[value[0]])
        return (unpacker.decompress(value[1:]) + unpacker.flush()).decode()

    conn.create_function("unzip", 1, unzip, deterministic=True)
    return dicts
