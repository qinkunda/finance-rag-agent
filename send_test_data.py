import json, time, random
from kafka import KafkaProducer

producer = KafkaProducer(
    bootstrap_servers='localhost:9092',
    value_serializer=lambda v: json.dumps(v).encode('utf-8')
)

print("开始持续发送数据... 按 Ctrl+C 停止")
try:
    while True:
        msg = {
            "doc_id": f"doc_{random.randint(1,5):03d}",
            "query_time": time.strftime("%Y-%m-%dT%H:%M:%S")  # ✅ 必须用真实当前时间
        }
        producer.send('rag_queries', value=msg)
        print(f"已发送: {msg['doc_id']} @ {msg['query_time']}")
        time.sleep(2)  # 每2秒发一条，方便观察曲线波动
except KeyboardInterrupt:
    print("\n停止发送")
finally:
    producer.close()