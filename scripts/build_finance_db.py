"""从合成原始文件重建统一 SQLite 业务库。重建会清除本地演示工单。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finance_platform.database import build_database


if __name__ == "__main__":
    print(json.dumps(build_database(rebuild=True), ensure_ascii=False))
