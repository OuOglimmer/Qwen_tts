"""前后端接口契约静态核对：前端调用的端点 vs 后端注册的路由。

不需要起服务、不需要模型，纯静态解析源码。用途：
  1. 升级 / 重构后确认没有「前端调了后端没实现」的断头路；
  2. 顺手列出后端实现了但前端没用到的端点（可能是废弃接口或漏接的入口）。

用法：
    .venv\\Scripts\\python.exe scripts\\api_contract_check.py
    .venv\\Scripts\\python.exe scripts\\api_contract_check.py --verbose

退出码：0 = 无断头路；1 = 存在前端调用但后端未实现的端点。
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "studio" / "api.py"
FRONTEND_DIRS = [ROOT / "frontend" / name for name in ("app", "components", "features", "lib")]

# @app.get("/api/voices")  /  @app.post('/api/projects/{project_id}/batch')
ROUTE_RE = re.compile(r"""@app\.(get|post|put|delete|patch)\(\s*["']([^"']+)["']""")

# apiGet<T>('/api/x')  apiPost<T>(`/api/projects/${id}/y`)  apiUpload(path, form)
CALL_RE = re.compile(
    r"""\bapi(Get|Post|Put|Delete|Upload|PostBlob)\s*(?:<[^>()]*>)?\s*\(\s*([`'"])(.+?)\2""",
    re.S,
)
METHOD_OF = {
    "Get": "GET",
    "Post": "POST",
    "Put": "PUT",
    "Delete": "DELETE",
    "Upload": "POST",
    "PostBlob": "POST",
}

TEMPLATE_EXPR = re.compile(r"\$\{[^}]*\}")

# 用于「后端有、前端未用」的判定：只要源码里出现过这个路径就算被引用过。
# 比 CALL_RE 宽松得多——前端会用 apiUpload(...) 跨行、模板拼接、fileUrl() 等方式，
# 严格匹配容易把「已用」误报成「未用」。断头路判定仍只认 CALL_RE。
LITERAL_RE = re.compile(r"""[`'"](/api/[^`'"]*)[`'"]""")


def normalize_variants(path: str) -> list[str]:
    """把前端拼出来的路径归一成若干个候选，任一命中后端即算通过。

    前端有三种写法会让"字面量"带上非路径成分：
      1. 查询串拼接：`/api/library/voices${query}`      → 末尾多一个 {}
      2. 嵌套模板：  `/api/tags${scope ? `?scope=..` : ''}` → 内层反引号把捕获截断，留下未闭合的 ${
      3. 常规动态段：`/api/projects/${id}/voices`        → {}

    所以候选集 = {原样} ∪ {去掉末尾 {}} ∪ {截断到未闭合 ${ 之前}。
    """
    raw = path.split("?")[0]
    # 未闭合的 ${ 说明捕获被嵌套模板截断了，只取它前面的部分
    cut = raw.find("${")
    if cut >= 0 and "}" not in raw[cut:]:
        raw = raw[:cut]
    base = re.sub(r"\$\{[^}]*\}", "{}", raw)
    base = re.sub(r"\{[^}/]*\}", "{}", base)
    base = re.sub(r"/+", "/", base).rstrip("/") or "/"

    variants = [base]
    # 末尾 {} 多半是拼的查询串（?a=1），去掉再试一次
    if base.endswith("{}") and len(base) > 2:
        variants.append(base[:-2].rstrip("/") or "/")
    return variants


def backend_routes() -> dict[str, set[str]]:
    source = BACKEND.read_text(encoding="utf-8", errors="ignore")
    routes: dict[str, set[str]] = defaultdict(set)
    for method, path in ROUTE_RE.findall(source):
        variants = normalize_variants(path)
        routes[variants[0]].add(method.upper())
    return routes


def frontend_calls() -> tuple[dict[tuple[str, ...], set[str]], list[tuple[str, int, str]], int]:
    """返回 (候选路径元组→方法集合, 无法静态解析的调用点, 已解析调用点数)。"""
    calls: dict[tuple[str, ...], set[str]] = defaultdict(set)
    unresolved: list[tuple[str, int, str]] = []
    resolved = 0
    for base in FRONTEND_DIRS:
        if not base.exists():
            continue
        for file in sorted(base.rglob("*")):
            if file.suffix not in {".ts", ".tsx"}:
                continue
            text = file.read_text(encoding="utf-8", errors="ignore")
            for match in CALL_RE.finditer(text):
                kind, _quote, raw = match.group(1), match.group(2), match.group(3)
                line = text.count("\n", 0, match.start()) + 1
                rel = file.relative_to(ROOT).as_posix()
                if not raw.startswith("/api/"):
                    unresolved.append((rel, line, raw))
                    continue
                calls[tuple(normalize_variants(raw))].add(METHOD_OF[kind])
                resolved += 1
    return calls, unresolved, resolved


def frontend_literals() -> set[str]:
    """所有出现过的 /api/ 路径字面量（归一化后），用于判定端点是否被前端触及。"""
    seen: set[str] = set()
    for base in FRONTEND_DIRS:
        if not base.exists():
            continue
        for file in sorted(base.rglob("*")):
            if file.suffix not in {".ts", ".tsx"}:
                continue
            text = file.read_text(encoding="utf-8", errors="ignore")
            for raw in LITERAL_RE.findall(text):
                for variant in normalize_variants(raw):
                    seen.add(variant)
    return seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="同时列出后端有、前端未用的端点")
    args = parser.parse_args()

    routes = backend_routes()
    calls, unresolved, resolved = frontend_calls()

    missing: list[str] = []
    method_mismatch: list[str] = []
    matched: set[str] = set()
    for variants, methods in sorted(calls.items()):
        hit = next((v for v in variants if v in routes), None)
        if hit is None:
            missing.append(f"{variants[0]}  (前端用 {'/'.join(sorted(methods))})")
            continue
        matched.add(hit)
        if not (methods & routes[hit]):
            method_mismatch.append(
                f"{hit}  前端 {'/'.join(sorted(methods))} / 后端 {'/'.join(sorted(routes[hit]))}"
            )

    print(f"后端路由      : {len(routes)} 个端点")
    print(f"前端调用      : {resolved} 处已解析，{len(calls)} 个唯一端点")
    if unresolved:
        print(f"未解析调用    : {len(unresolved)} 处（路径非字面量，需人工确认）")
        for rel, line, raw in unresolved[:20]:
            print(f"    {rel}:{line}  ->  {raw}")
        if len(unresolved) > 20:
            print(f"    ...另有 {len(unresolved) - 20} 处")

    print()
    if missing:
        print(f"❌ 前端调用但后端未实现（{len(missing)}）")
        for item in missing:
            print(f"    {item}")
    if method_mismatch:
        print(f"❌ 方法不匹配（{len(method_mismatch)}）")
        for item in method_mismatch:
            print(f"    {item}")
    if not missing and not method_mismatch:
        print("✅ 前端调用的每个端点，后端都有对应路由且方法一致")

    if args.verbose:
        literals = frontend_literals()
        unused = sorted(path for path in routes if path not in literals and path not in matched)
        print()
        print(f"后端有、前端源码里完全没出现（{len(unused)}，多为预留接口）")
        for path in unused:
            print(f"    {'/'.join(sorted(routes[path])):12} {path}")

    return 1 if (missing or method_mismatch) else 0


if __name__ == "__main__":
    sys.exit(main())
