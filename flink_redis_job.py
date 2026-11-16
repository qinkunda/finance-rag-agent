import json
import time
import redis
from kafka import KafkaConsumer

print("=" * 50)
print("  Flink 计算任务启动中...")
print("=" * 50 + "\n")

# 1. 连接 Kafka
print("[1/3] 正在连接Kafka...")
try:
    consumer = KafkaConsumer(
        'rag_queries',
        bootstrap_servers=['localhost:9092'],
        auto_offset_reset='latest',
        value_deserializer=lambda v: json.loads(v.decode('utf-8')),
        session_timeout_ms=60000,
        request_timeout_ms=120000
    )
    print("[OK] Kafka连接成功!\n")
except Exception as e:
    print(f"[XX] Kafka连接失败: {e}")
    exit()

# 2. 连接 Redis (锁定 6380 端口)
print("[2/3] 正在连接Redis...")
try:
    # 注意：这里改成了 6380 端口
    r = redis.Redis(host='localhost', port=6380, decode_responses=True)
    r.ping()  # 发送 Ping 测试
    print("[OK] Redis连接成功!\n")
except Exception as e:
    print(f"[XX] Redis连接失败: {e}")
    print("提示：请先启动 mock_redis_server.py")
    exit()

# 3. 开始 Flink 模拟计算
print("[3/3] 启动计算引擎，准备接收数据...\n")

window_count = 0
last_window_time = time.time()
WINDOW_SIZE = 5

try:
    for message in consumer:
        window_count += 1
        current_time = time.time()

        if current_time - last_window_time > WINDOW_SIZE:
            if window_count > 0:
                result = {
                    "window_count": window_count,
                    "timestamp": time.strftime('%H:%M:%S', time.localtime(current_time))
                }
                # 将计算结果写入 6380 端口的 Redis
                r.set('flink_metrics', json.dumps(result))
                print(f"✅ [Flink计算] {WINDOW_SIZE}秒窗口结果已写入Redis: {result}")

            window_count = 0
            last_window_time = current_time

except KeyboardInterrupt:
    print("\n👋 Flink 任务已手动停止。")
except Exception as e:
    print(f"\n❌ 发生严重错误: {e}")
finally:
    consumer.close()
    print("🔒 Kafka 连接已关闭。")