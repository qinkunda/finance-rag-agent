import os
import time
from pyflink.table import EnvironmentSettings, TableEnvironment

# 1. 创建表环境
env_settings = EnvironmentSettings.in_streaming_mode()
t_env = TableEnvironment.create(env_settings)

t_env.get_config().set("rest.port", "8081")

# 2. 加载 Kafka Connector JAR
jar_relative_path = "./lib/flink-sql-connector-kafka-3.2.0-1.19.jar"
jar_path = os.path.abspath(jar_relative_path)

t_env.get_config().set("pipeline.jars", f"file:///{jar_path.replace(os.sep, '/')}")

# 3. 确保输出目录存在
output_dir = os.path.abspath("./output/rag_window_stats")
os.makedirs(output_dir, exist_ok=True)
output_path = f"file:///{output_dir.replace(os.sep, '/')}"

# 4. 定义 Kafka Source（去掉 WATERMARK 逻辑）
t_env.execute_sql("""
    CREATE TABLE rag_query_log (
        doc_id STRING,
        query_time TIMESTAMP(3)
    ) WITH (
        'connector' = 'kafka',
        'topic' = 'rag_queries',
        'properties.bootstrap.servers' = 'localhost:9092',
        'format' = 'json',
        'scan.startup.mode' = 'latest-offset'
    )
""")

# 在你的 flink_job.py 中，引入 redis 库
import redis

# 假设这是你 Flink 处理完数据后的结果（比如每分钟的统计量）
# 在实际的 Flink SQL 或 DataStream 代码中，这通常在 sink 阶段
def write_to_redis(result_data):
    try:
        # 连接我们刚才启动的 mock redis
        r = redis.Redis(host='localhost', port=6379, decode_responses=True)
        # 存入 Redis，key 叫 'flink_metrics'
        r.set('flink_metrics', json.dumps(result_data))
        print(f"✅ Flink 计算结果已写入 Redis: {result_data}")
    except Exception as e:
        print(f"❌ 写入 Redis 失败: {e}")

# 示例：在你的 Flink 逻辑中调用它
# 比如你算出了当前窗口的总数是 100
# write_to_redis({"window_count": 100, "timestamp": "2024-09-24 10:00:00"})

# 6. 执行最简单的聚合（直接 INSERT，不做窗口）
t_env.execute_sql("""
    INSERT INTO window_result
    SELECT 
        doc_id,
        query_time
    FROM rag_query_log
""")

print("=" * 50)
print("✅ Flink 任务已提交！")
print("🌐 请打开浏览器访问: http://localhost:8081")
print(f"📂 结果将写入: {output_dir}")
print("⏳ 按 Ctrl+C 停止任务和关闭 Web UI...")
print("=" * 50)

try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    print("\n🛑 收到停止信号，Flink 任务已终止。")