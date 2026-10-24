"""Распаковывает архив датасета в data/raw: companies.csv, all_operations.csv, statements/Cxxx.csv.

Запуск из backend/:  uv run python scripts/unpack_dataset.py ../cashflow_dataset_participants.zip ../data/raw
Если в архиве один верхний каталог, он отбрасывается. Пути внутри архива проверяются (zip-slip).
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path, PurePosixPath


def unpack(archive: Path, target: Path) -> int:
    target = target.resolve()
    with zipfile.ZipFile(archive) as zf:
        members = [m for m in zf.infolist() if not m.is_dir() and not m.filename.startswith("__MACOSX")]
        tops = {PurePosixPath(m.filename).parts[0] for m in members if len(PurePosixPath(m.filename).parts) > 1}
        roots = {PurePosixPath(m.filename).parts[0] for m in members}
        strip = 1 if len(roots) == 1 and tops == roots else 0
        count = 0
        for member in members:
            parts = PurePosixPath(member.filename).parts[strip:]
            if not parts:
                continue
            dest = (target.joinpath(*parts)).resolve()
            if target not in dest.parents:
                raise ValueError(f"Небезопасный путь в архиве: {member.filename}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as out:
                out.write(src.read())
            count += 1
    return count


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("Использование: unpack_dataset.py <архив.zip> <каталог назначения>")
    archive, target = Path(sys.argv[1]), Path(sys.argv[2])
    if not archive.exists():
        sys.exit(f"Архив не найден: {archive}. Положите cashflow_dataset_participants.zip в корень репозитория "
                 "или передайте путь: make data DATASET=путь/к/архиву.zip")
    n = unpack(archive, target)
    print(f"Распаковано файлов: {n} → {target}")
    for name in ("companies.csv", "all_operations.csv"):
        if not (target / name).exists():
            print(f"Внимание: {name} не найден в архиве — проверьте содержимое")


if __name__ == "__main__":
    main()
