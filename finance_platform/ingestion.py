"""原始文档 → 清洗文本 → 条款标签 → 检索 Chunk → 血缘清单。

仅处理 catalog.json 显式登记的文件，避免误扫本机目录。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "catalog.json"
CURATED_DIR = ROOT / "data" / "curated"
CLAUSE_RE = re.compile(r"(?=第[零一二三四五六七八九十百千万0-9]+条)")
ALLOWED_ACCESS = {"public", "internal", "confidential"}
REQUIRED_FIELDS = {
    "document_id", "path", "title", "scenario", "document_type", "version",
    "effective_date", "status", "access_level", "source_type",
}


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def extract_text(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        raw = path.read_bytes()
        for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
            try:
                return raw.decode(encoding), encoding
            except UnicodeDecodeError:
                continue
        raise ValueError(f"无法识别文本编码：{path}")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("解析 PDF 需要安装 pypdf") from exc
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return text, "pypdf"
    raise ValueError(f"不支持的文档格式：{path.name}")


def clean_text(text: str) -> str:
    """做可解释的轻量清洗，不用正则误删条款正文。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    lines = []
    for raw_line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if not line or re.fullmatch(r"第\s*\d+\s*页", line):
            continue
        lines.append(line)
    return "\n".join(lines)


def clause_labels(text: str) -> list[str]:
    labels = []
    rules = [
        ("approval", ("审批", "加签", "工单")),
        ("constraint", ("不得", "严禁", "必须", "只能", "应当")),
        ("calculation", ("金额", "比例", "预算", "税额", "百分之")),
        ("permission", ("权限", "角色", "密级")),
        ("effective", ("生效", "失效", "版本")),
    ]
    for label, keywords in rules:
        if any(word in text for word in keywords):
            labels.append(label)
    return labels or ["general"]


def split_clauses(text: str, title: str, max_chars: int = 700) -> list[str]:
    pieces = [p.strip() for p in CLAUSE_RE.split(text) if p.strip()]
    if not pieces:
        pieces = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    for piece in pieces:
        # PDF 标题和声明位于第一条之前，并入第一条；每条都保留文档名便于引用。
        if len(piece) <= max_chars:
            chunks.append(f"{title}\n{piece}" if not piece.startswith(title) else piece)
        else:
            for start in range(0, len(piece), max_chars):
                chunks.append(f"{title}\n{piece[start:start + max_chars]}")
    return [c for c in chunks if len(c) > len(title) + 5]


def load_catalog(path: Path = CATALOG_PATH) -> list[dict[str, Any]]:
    catalog = json.loads(path.read_text(encoding="utf-8"))
    seen = set()
    for item in catalog:
        missing = REQUIRED_FIELDS - set(item)
        if missing:
            raise ValueError(f"目录缺少字段 {sorted(missing)}：{item.get('document_id')}")
        if item["document_id"] in seen:
            raise ValueError("重复 document_id：{}".format(item["document_id"]))
        if item["access_level"] not in ALLOWED_ACCESS:
            raise ValueError("未知权限级别：{}".format(item["access_level"]))
        if item["source_type"] not in {"synthetic", "public_official"}:
            raise ValueError("演示项目仅允许 synthetic 或 public_official 来源")
        seen.add(item["document_id"])
    return catalog


def _catalog_file(root: Path, source_path: str) -> Path:
    """登记路径必须位于项目根目录内，防止绝对路径或 ../ 越界读取。"""
    root = root.resolve()
    path = (root / source_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"catalog 路径越出项目目录：{source_path}") from exc
    return path


def build_corpus(root: Path = ROOT, output_dir: Path | None = None) -> dict[str, Any]:
    output_dir = output_dir or root / "data" / "curated"
    catalog = load_catalog(root / "data" / "catalog.json")
    parsed_rows, cleaned_rows, chunk_rows, lineage = [], [], [], []
    for entry in catalog:
        path = _catalog_file(root, entry["path"])
        if not path.is_file():
            raise FileNotFoundError(f"目录登记的文件不存在：{path}")
        raw = path.read_bytes()
        text, parser = extract_text(path)
        cleaned = clean_text(text)
        if len(cleaned) < 30:
            raise ValueError(f"解析后文本过短：{path.name}")
        base = {
            key: entry[key] for key in (
                "document_id", "title", "scenario", "document_type", "version",
                "effective_date", "status", "access_level", "source_type",
            )
        }
        parsed_rows.append({**base, "text": text, "parser": parser})
        cleaned_rows.append({**base, "text": cleaned})
        chunks = split_clauses(cleaned, entry["title"])
        for index, chunk in enumerate(chunks, 1):
            chunk_rows.append({
                **base,
                "chunk_id": "{}-{:03d}".format(entry["document_id"], index),
                "chunk_index": index,
                "text": chunk,
                "labels": clause_labels(chunk),
                "source_path": entry["path"],
                "text_sha256": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
            })
        lineage.append({
            **base,
            "source_path": entry["path"],
            "file_sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "parser": parser,
            "cleaner": "clean_text:v1",
            "chunker": "split_clauses:v1",
            "chunk_count": len(chunks),
        })
    if not chunk_rows:
        raise ValueError("没有生成任何知识 Chunk")
    _write_jsonl(output_dir / "parsed.jsonl", parsed_rows)
    _write_jsonl(output_dir / "cleaned.jsonl", cleaned_rows)
    _write_jsonl(output_dir / "chunks.jsonl", chunk_rows)
    manifest = {
        "pipeline_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_declaration": "演示知识工程产物；来源类型按 catalog 逐文档记录，未接入真实企业内部数据。",
        "document_count": len(catalog),
        "chunk_count": len(chunk_rows),
        "documents": lineage,
    }
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            current_stable = {key: value for key, value in manifest.items() if key != "generated_at"}
            previous_stable = {key: value for key, value in previous.items() if key != "generated_at"}
            if previous_stable == current_stable:
                # 重复构建且内容未变时保留原时间戳，避免测试和重建产生无意义 diff。
                manifest["generated_at"] = previous["generated_at"]
        except (OSError, ValueError, KeyError, TypeError):
            pass  # 旧清单不可读或结构不完整时，用本次结果替换。
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def get_document_lineage(document_id: str, root: Path = ROOT) -> dict[str, Any]:
    manifest_path = root / "data" / "curated" / "manifest.json"
    if not manifest_path.exists():
        build_corpus(root)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest["documents"]:
        if item["document_id"] == document_id:
            return item
    raise KeyError(f"未找到文档血缘：{document_id}")


if __name__ == "__main__":
    result = build_corpus()
    print(json.dumps({k: result[k] for k in ("document_count", "chunk_count")}, ensure_ascii=False))
