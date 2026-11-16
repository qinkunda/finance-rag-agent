import sys
import json
import time
from kafka import KafkaProducer

# 从命令行参数获取发送数量，默认为10
try:
    num_messages = int(sys.argv[1]) if len(sys.argv) > 1 else 10
except ValueError:
    num_messages = 10

print(f"正在向Kafka发送 {num_messages} 条测试数据...")

# 创建Kafka生产者
producer = KafkaProducer(
    bootstrap_servers=['localhost:9092'],
    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
    request_timeout_ms=10000
)

sent_count = 0
try:
    for i in range(num_messages):
        data = {
            "id": i,
            "timestamp": time.time(),
            "message": f"测试数据第{i + 1}条",
            "source": "kafka_producer"
        }
        # 发送到rag_queries主题
        future = producer.send('rag_queries', value=data)
        # 等待发送确认
        record_metadata = future.get(timeout=10)
        sent_count += 1

        # 每发送10条打印一次进度
        if (i + 1) % 10 == 0 or i == 0:
            print(f"  已发送 {i + 1}/{num_messages} 条...")

    # 确保所有消息都发送完成
    producer.flush()
    print(f"\n[SUCCESS] 成功发送 {sent_count} 条数据到Kafka topic 'rag_queries'")

except Exception as e:
    print(f"[ERROR] 发送数据失败: {e}")
    print("    可能原因:")
    print("    1. Kafka服务未启动 (localhost:9092)")
    print("    2. 网络连接问题")
    sys.exit(1)
finally:
    producer.close()
    print("[INFO] Kafka生产者已关闭")