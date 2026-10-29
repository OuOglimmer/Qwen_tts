"""路径重基脚本：把旧库里的绝对路径前缀改写到新数据目录。

背景：旧库（schema v1）把绝对路径写进 8 个列，前缀是旧安装根 `D:\\QwenTTS\\`。
旧目录被改名/移动成 `D:\\asset`，现在要迁到新数据目录（如 `D:\\QwenTTS_Data`），
必须在 migrate_v5 之前把路径改对，否则 migrate_v5 读文件回填 x_vector_only /
file_size / output_hash 时会静默跳过（留 NULL，不报错）。

两类前缀（都基于「旧根 D:\\QwenTTS 已变成新 DATA_DIR」这一事实）：
  1. `D:\\QwenTTS\\data\\...`  → `<NEW_DATA_DIR>\\...`   数据文件（voices/projects 下的）
  2. `D:\\QwenTTS\\outputs\\...` → `<NEW_DATA_DIR>\\outputs\\...`  导出目录（app_settings.batch_export_dir）

用法：
    python scripts/rebase_paths.py --db D:\\QwenTTS_Data\\app.db --new-root D:\\QwenTTS_Data --dry-run
    python scripts/rebase_paths.py --db D:\\QwenTTS_Data\\app.db --new-root D:\\QwenTTS_Data

安全：
  * 全程单事务，改完输出命中率报告；dry-run 不落库；
  * 只改「以 D:\\QwenTTS\\ 开头」的值，其它值（如坏数据 x.wav）原样保留并记入报告；
  * 改完后逐列校验「路径指向的文件在磁盘上是否存在」。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# (表, 列) —— 库内所有存路径的列（实测枚举，2026-10-29）
PATH_COLUMNS = [
    ("voices", "reference_wav"),
    ("voices", "preview_mp3"),
    ("voices", "prompt_path"),
    ("candidates", "wav_path"),
    ("candidates", "preview_path"),
    ("batch_items", "output_mp3"),
    ("voice_requests", "direct_output_mp3"),
]

# app_settings 里存路径的键前缀
EXPORT_DIR_PREFIX = "batch_export_dir:"

OLD_DATA_PREFIX = r"D:\QwenTTS\data"
OLD_EXPORT_PREFIX = r"D:\QwenTTS\outputs"
OLD_ROOT_PREFIX = r"D:\QwenTTS"


def normalize_prefix(s: str) -> str:
    """把任意盘符大小写/正反斜杠归一到大写正斜杠，便于比较。"""
    return s.replace("\\", "/").upper()


def rebase_one(value: str | None, new_root: str) -> tuple[str | None, str]:
    """返回 (新值, 动作)。动作 ∈ {data, export, other, empty}。"""
    if not value:
        return value, "empty"
    v = value.strip()
    nv = normalize_prefix(v)
    nr = normalize_prefix(new_root)
    if nv.startswith(normalize_prefix(OLD_DATA_PREFIX) + "/"):
        tail = v[len(OLD_DATA_PREFIX):]  # 去掉 "D:\QwenTTS\data"，保留 "\projects\..."
        return new_root + tail, "data"
    if nv.startswith(normalize_prefix(OLD_EXPORT_PREFIX) + "/") or nv == normalize_prefix(OLD_EXPORT_PREFIX):
        tail = v[len(OLD_EXPORT_PREFIX):]
        return new_root + "\\outputs" + tail, "export"
    if nv.startswith(normalize_prefix(OLD_ROOT_PREFIX)):
        # 其它 D:\QwenTTS\ 开头但不符合上面两种（理论不该有），记下来不改
        return value, "other"
    return value, "other"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="目标 app.db 路径")
    ap.add_argument("--new-root", required=True, help="新数据目录根（绝对路径）")
    ap.add_argument("--dry-run", action="store_true", help="只报告不写库")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[ERR] 库不存在: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    report: list[str] = []
    changed = 0
    untouched = 0

    # 收集所有要改的行
    updates: list[tuple[str, str, str, object]] = []  # (表, 主键列, 主键值, 新值) —— 用 rowid 更稳
    # 实际上用 rowid 最稳
    row_updates: list[tuple[str, int, str, object]] = []  # (表, rowid, 列, 新值)

    for table, col in PATH_COLUMNS:
        for r in conn.execute(f'SELECT rowid, "{col}" FROM "{table}"'):
            rowid, value = r[0], r[1]
            new_val, action = rebase_one(value, args.new_root)
            if action in ("data", "export"):
                row_updates.append((table, rowid, col, new_val))
                changed += 1
                report.append(f"  {table}.{col} [rowid {rowid}]: {value}  ->  {new_val}")
            else:
                untouched += 1
                if action == "other" and value:
                    report.append(f"  [保留] {table}.{col} [rowid {rowid}]: {value}")

    # app_settings 里的 batch_export_dir:*
    for r in conn.execute("SELECT rowid, key, value FROM app_settings WHERE key LIKE ?", (EXPORT_DIR_PREFIX + "%",)):
        rowid, key, value = r[0], r[1], r[2]
        new_val, action = rebase_one(value, args.new_root)
        if action in ("data", "export"):
            row_updates.append(("app_settings", rowid, "value", new_val))
            changed += 1
            report.append(f"  app_settings.value [{key}]: {value}  ->  {new_val}")
        else:
            untouched += 1
            if action == "other" and value:
                report.append(f"  [保留] app_settings.value [{key}]: {value}")

    print(f"=== 路径重基 {'DRY-RUN（不落库）' if args.dry_run else '（写库）'} ===")
    print(f"库: {db_path}")
    print(f"新根: {args.new_root}")
    print(f"待改写: {changed}  保留不动: {untouched}")
    print()
    for line in report[:200]:
        print(line)
    if len(report) > 200:
        print(f"  ... 其余 {len(report)-200} 条省略")

    if args.dry_run:
        print(f"\n[dry-run] 未写库。确认无误后去掉 --dry-run 重跑。")
        return 0

    # 真正写库：单事务
    conn.execute("BEGIN")
    for table, rowid, col, new_val in row_updates:
        conn.execute(f'UPDATE "{table}" SET "{col}" = ? WHERE rowid = ?', (new_val, rowid))
    conn.commit()

    # 改后校验：逐列统计文件存在率
    print("\n=== 改后文件存在率校验 ===")
    all_ok = True
    for table, col in PATH_COLUMNS:
        total = miss = bad = 0
        for r in conn.execute(f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL AND "{col}" != \'\''):
            v = r[0]
            total += 1
            if v and (Path(v).is_file()):
                pass
            elif v and normalize_prefix(v).startswith(normalize_prefix(args.new_root)):
                miss += 1
            else:
                bad += 1
        if total:
            hit = total - miss - bad
            status = "OK" if miss == 0 else "!!有缺失"
            if miss:
                all_ok = False
            print(f"  {table:16} {col:20} 共{total} 命中{hit} 缺{miss} 非新前缀{bad}  {status}")

    conn.close()
    print(f"\n{'✅ 全部命中' if all_ok else '⚠️ 存在缺失文件，见上'}。写入完成。")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
