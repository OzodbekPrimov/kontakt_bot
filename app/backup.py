"""Manual consistent SQLite backup, safe while the polling process is running."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def create_backup(source_path: Path) -> Path:
    source_path = source_path.resolve()
    if not source_path.is_file():
        raise FileNotFoundError("Baza topilmadi. Buyruqni loyiha papkasida bajaring.")
    directory = source_path.parent / "backups"
    directory.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    destination = directory / f"bot-{stamp}-{uuid4().hex[:8]}.sqlite3"
    with closing(sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(destination)) as target:
            source.backup(target)
    return destination


def main() -> None:
    try:
        destination = create_backup(Path("data/bot.sqlite3"))
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from None
    print(f"Backup yaratildi: {destination}")


if __name__ == "__main__":
    main()
