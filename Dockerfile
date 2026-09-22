FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 交互式 CLI 入口；首次运行自动构建知识库
# 模型目录通过 -v 挂载本机 models/，避免镜像膨胀
CMD ["python", "main.py"]
