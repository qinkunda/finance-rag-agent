
import json
from datetime import datetime
from config import AUDIT_LOG_DIR


class QueryTimer:
    """查询计时器，用作上下文管理器"""

    def __enter__(self):
        import time
        self._start = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        import time
        self._latency_ms = (time.time() - self._start) * 1000

    @property
    def latency_ms(self):
        return getattr(self, '_latency_ms', 0)


def log_query(
    user: str,                    # ← 新增（放第一参数）
    question: str,
    answer: str,
    retrieved_chunks: list,
    latency_ms: float | int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
):
    """将一次查询记录写入 JSONL 日志文件"""
    log_entry = {
        "user": user,             # ← 新增
        "timestamp": datetime.now().isoformat(),
        "question": question,
        "answer": answer,
        "retrieved_chunks": retrieved_chunks,
        "latency_ms": round(latency_ms, 2),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    # ... 下面不变

    log_file = AUDIT_LOG_DIR / "query_log.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    print(
        f"  [审计] 已记录 | 耗时 {latency_ms:.1f}ms | "
        f"prompt {prompt_tokens}tok | completion {completion_tokens}tok"
    )