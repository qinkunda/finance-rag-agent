import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent

# 加载 .env 文件
load_dotenv(BASE_DIR / ".env")

# DASHSCOPE API 配置
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

# 知识库路径
KNOWLEDGE_DIR = str(BASE_DIR / "knowledge")
CHROMA_PERSIST_DIR = str(BASE_DIR / "chroma_db")

# 审计日志路径
AUDIT_LOG_DIR = BASE_DIR / "logs"
AUDIT_LOG_DIR.mkdir(exist_ok=True)

# ==========================================
# 本地模型路径配置（相对路径，跨机器可移植）
# ==========================================
MODELS_DIR = str(BASE_DIR / "models")
RERANK_MODEL_NAME = "BAAI--bge-reranker-v2-m3"
# 使用相对路径拼接，不再硬编码本机绝对路径
# 使用前请先从 ModelScope/HuggingFace 下载模型到该目录
LOCAL_RERANK_PATH = str(Path(MODELS_DIR) / RERANK_MODEL_NAME / "snapshots" / "master")
