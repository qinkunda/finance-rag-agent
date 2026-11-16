import time
import json
import threading
import sys
import os
import subprocess
from flask import Flask, render_template_string
from flask_socketio import SocketIO, emit
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError
import redis

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# ============================================================
# 前端HTML模板（修复了JavaScript bug：log-container不存在）
# ============================================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>企业级实时数据监控面板</title>
    <script src="https://cdn.socket.io/4.0.0/socket.io.min.js"></script>
    <style>
        :root { --bg-color: #121212; --card-bg: #1e1e1e; --text-color: #e0e0e0; --accent: #4caf50; }
        body { margin: 0; background: var(--bg-color); color: var(--text-color); font-family: \'Segoe UI\', sans-serif; padding: 20px; display: flex; flex-direction: column; height: 100vh; box-sizing: border-box; }
        header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 20px; border-bottom: 1px solid #333; }
        h1 { font-size: 24px; margin: 0; }
        .metrics { display: flex; gap: 20px; }
        .metric-box { background: var(--card-bg); padding: 10px 20px; border-radius: 8px; display: flex; flex-direction: column; align-items: center; }
        .metric-val { font-size: 24px; font-weight: bold; color: var(--accent); }
        .container { display: flex; gap: 20px; flex: 1; margin-top: 20px; overflow: hidden; }
        .card { background: var(--card-bg); border-radius: 8px; padding: 15px; display: flex; flex-direction: column; }
        .card-1 { flex: 1.5; }
        .card-2 { flex: 1; }
        .card-3 { flex: 2; }
        .stream-area { flex: 1; overflow-y: auto; margin-top: 10px; }
        .stream-item { padding: 8px; border-bottom: 1px solid #333; font-size: 13px; opacity: 0.8; transition: background 0.3s; }
        .stream-item:first-child { background: rgba(76, 175, 80, 0.1); opacity: 1; }
        button { background: var(--accent); color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-size: 14px; }
        .kafka-bar { height: 10px; background: #333; border-radius: 5px; margin-top: 10px; position: relative; }
        .kafka-fill { height: 100%; background: #2196f3; border-radius: 5px; width: 0%; transition: width 0.5s; }
    </style>
</head>
<body>
    <header>
        <h1>实时数据全景监控仪表盘</h1>
        <div class="metrics">
            <div class="metric-box"><span>今日总数据量</span><span class="metric-val" id="total-count">0</span></div>
            <div class="metric-box"><span>实时处理速率 (QPS)</span><span class="metric-val" id="qps">0.0</span></div>
        </div>
    </header>

    <div class="container">
        <div class="card card-1">
            <h3>环节一：数据生产 (Producer)</h3>
            <p>模拟后端系统导入原始数据</p>
            <button onclick="fetch(\'/inject/10\')">发送 10 条测试数据</button>
            <button onclick="fetch(\'/inject/50\')">发送 50 条批量数据</button>
            <button onclick="fetch(\'/inject/100\')">发送 100 条突发数据</button>
        </div>

        <div class="card card-2">
            <h3>环节二：Kafka 消息队列</h3>
            <p>当前堆积等待处理的数据量：<span id="kafka-lag" style="font-weight:bold;">0</span></p>
            <div class="kafka-bar"><div class="kafka-fill" id="kafka-fill"></div></div>
            <p style="margin-top:10px; font-size:12px; color:#888;">Topic: rag_queries</p>
        </div>

        <div class="card card-3">
            <h3>Flink 实时计算结果</h3>
            <div style="display: flex; gap: 10px; margin-bottom: 15px;">
                <div class="metric-box" style="flex: 1;">
                    <span>Flink 窗口计数</span>
                    <span class="metric-val" id="flink-count">0</span>
                </div>
                <div class="metric-box" style="flex: 1;">
                    <span>计算时间戳</span>
                    <span class="metric-val" id="flink-time" style="font-size: 14px;">--</span>
                </div>
            </div>
            <div class="stream-area" id="flink-log-container" style="height: 200px;"></div>
        </div>
    </div>

    <script>
        var socket = io();
        var count = 0;
        var lastTime = Date.now();
        // BUG修复1: 原代码getElementById(\'log-container\')但该ID在HTML中不存在，导致JS报错崩溃
        var streamContainer = document.getElementById(\'data-log-container\') || document.createElement(\'div\');
        if (!document.getElementById(\'data-log-container\')) {
            // 如果HTML中没有这个容器，创建一个（兼容处理）
            var tempDiv = document.createElement(\'div\');
            tempDiv.id = \'data-log-container\';
            tempDiv.className = \'stream-area\';
            document.querySelector(\'.card-1\').appendChild(tempDiv);
            streamContainer = tempDiv;
        }
        var flinkLogContainer = document.getElementById(\'flink-log-container\');

        socket.on(\'new_data\', function(data) {
            count += 1;
            document.getElementById(\'total-count\').innerText = count.toLocaleString();

            var now = Date.now();
            var qps = (1000 / (now - lastTime)).toFixed(1);
            if(qps > 100) qps = 100;
            document.getElementById(\'qps\').innerText = qps;
            lastTime = now;

            var div = document.createElement(\'div\');
            div.className = \'stream-item\';
            div.innerText = \'[\' + new Date().toLocaleTimeString() + ']\' + JSON.stringify(data);
            streamContainer.prepend(div);
            if(streamContainer.children.length > 50) streamContainer.lastChild.remove();
        });

        socket.on(\'flink_update\', function(data) {
            document.getElementById(\'flink-count\').innerText = data.window_count || 0;
            document.getElementById(\'flink-time\').innerText = data.timestamp || \'--\';

            var div = document.createElement(\'div\');
            div.className = \'stream-item\';
            div.style.color = \'#4caf50\';
            div.innerText = \'[Flink计算]\' + JSON.stringify(data);
            flinkLogContainer.prepend(div);
            if(flinkLogContainer.children.length > 20) flinkLogContainer.lastChild.remove();
        });

        setInterval(() => {
            fetch(\'/kafka-status\').then(r => r.json()).then(data => {
                document.getElementById(\'kafka-lag\').innerText = data.lag;
                document.getElementById(\'kafka-fill\').style.width = Math.min(data.lag, 100) + \'%\';
            });
        }, 1000);
    </script>
</body>
</html>
"""


# ============================================================
# 环境检查函数
# ============================================================
def check_redis():
    """检查Redis是否可用"""
    try:
        r = redis.Redis(host='localhost', port=6380, decode_responses=True)
        r.ping()
        print("[OK] Redis 连接正常")
        return True
    except redis.ConnectionError:
        print("[ERROR] 无法连接Redis! 请确保Redis服务已启动")
        print("    Windows: 确保 redis-server 正在运行，或安装真正的Redis")
        return False
    except Exception as e:
        print(f"[ERROR] Redis检查失败: {e}")
        return False


def check_kafka():
    """检查Kafka是否可用"""
    try:
        producer = KafkaProducer(
            bootstrap_servers=['localhost:9092'],
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            request_timeout_ms=10000
        )
        producer.close()
        print("[OK] Kafka 连接正常")
        return True
    except Exception as e:
        print(f"[ERROR] 无法连接Kafka! {e}")
        print("    请确保 Kafka 和 Zookeeper 正在运行")
        return False


# ============================================================
# Kafka消费者（BUG修复2: 添加超时配置 + 自动重连 + 改为earliest）
# ============================================================
def start_kafka_consumer():
    """Kafka数据消费者 - 带自动重连和超时保护"""
    while True:
        try:
            consumer = KafkaConsumer(
                'rag_queries',
                bootstrap_servers=['localhost:9092'],
                auto_offset_reset='earliest',  # BUG修复: 改为earliest确保能读到所有消息
                value_deserializer=lambda v: json.loads(v.decode('utf-8')),
                session_timeout_ms=60000,  # BUG修复: 会话超时60秒
                request_timeout_ms=120000,  # BUG修复: 请求超时120秒
                group_id='dashboard_consumer'
            )
            print("[OK] Kafka消费者已启动，等待数据...")
            for message in consumer:
                socketio.emit('new_data', message.value)
        except Exception as e:
            print(f"[WARN] Kafka消费者异常: {e}，5秒后自动重连...")
            time.sleep(5)


# ============================================================
# Flink结果消费者（BUG修复3: Redis连接检查 + 去重发送）
# ============================================================
def start_flink_result_consumer():
    """Flink计算结果消费者 - 轮询Redis"""
    while True:
        try:
            r = redis.Redis(host='localhost', port=6380, decode_responses=True)
            r.ping()  # 验证连接
            print("[OK] Redis连接成功，开始监听Flink结果...")
            last_count = None
            last_timestamp = None
            while True:
                result = r.get('flink_metrics')
                if result:
                    data = json.loads(result)
                    # 只在数据真正变化时才发送，避免重复广播
                    current_count = data.get('window_count')
                    current_ts = data.get('timestamp')
                    if current_count != last_count or current_ts != last_timestamp:
                        socketio.emit('flink_update', data)
                        last_count = current_count
                        last_timestamp = current_ts
                time.sleep(1)
        except Exception as e:
            print(f"[WARN] Redis读取异常: {e}，5秒后重连...")
            time.sleep(5)


# ============================================================
# Flask路由
# ============================================================
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/inject/<int:num>')
def inject_data(num):
    """发送测试数据到Kafka（BUG修复4: 正确传递num参数 + 降级策略）"""
    try:
        # 方法1: 尝试调用本地的kafka_producer.py并传递数量参数
        script_dir = os.path.dirname(os.path.abspath(__file__))
        producer_path = os.path.join(script_dir, 'kafka_producer.py')

        if os.path.exists(producer_path):
            try:
                proc = subprocess.run(
                    [sys.executable, producer_path, str(num)],
                    capture_output=True, text=True, timeout=30
                )
                if proc.returncode == 0:
                    print(f"[INFO] 通过kafka_producer.py发送了 {num} 条数据")
                    if proc.stdout:
                        print(f"    输出: {proc.stdout.strip()}")
                    return json.dumps({"status": "success", "msg": f"已通过producer脚本发送 {num} 条数据"})
                else:
                    print(f"[WARN] kafka_producer.py执行失败: {proc.stderr}")
                    print("[INFO] 降级为直接发送模式...")
            except subprocess.TimeoutExpired:
                print("[WARN] kafka_producer.py超时，降级为直接发送模式...")
            except Exception as e:
                print(f"[WARN] 调用kafka_producer.py失败: {e}，降级为直接发送模式...")

        # 方法2: 降级方案 - 直接用KafkaProducer发送（不依赖外部脚本）
        producer = KafkaProducer(
            bootstrap_servers=['localhost:9092'],
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            request_timeout_ms=10000
        )
        sent_count = 0
        for i in range(num):
            data = {
                "id": i,
                "timestamp": time.time(),
                "message": f"测试数据第{i + 1}条",
                "source": "dashboard_inject"
            }
            try:
                future = producer.send('rag_queries', value=data)
                future.get(timeout=5)
                sent_count += 1
            except Exception as e:
                print(f"[ERROR] 发送第{i + 1}条数据失败: {e}")
                break
        producer.flush()
        producer.close()
        print(f"[OK] 直接发送模式: 成功发送 {sent_count}/{num} 条数据到Kafka")
        return json.dumps({"status": "success", "msg": f"已发送 {sent_count} 条数据到Kafka", "sent": sent_count})

    except Exception as e:
        print(f"[ERROR] 发送数据失败: {e}")
        return json.dumps({"status": "error", "msg": f"发送失败: {str(e)}"})


@app.route('/kafka-status')
def kafka_status():
    """检查Kafka队列状态（BUG修复5: 实际查询Kafka而非硬编码0）"""
    try:
        consumer = KafkaConsumer(
            'rag_queries',
            bootstrap_servers=['localhost:9092'],
            group_id='status_checker',
            auto_offset_reset='earliest',
            enable_auto_commit=False,
            request_timeout_ms=5000
        )
        # 获取topic的末端offset（即最新消息位置）
        from kafka import TopicPartition
        tp = TopicPartition('rag_queries', 0)
        end_offset = consumer.end_offsets([tp])[tp].offset
        begin_offset = consumer.begin_offsets([tp])[tp].offset
        consumer.close()
        lag = max(0, end_offset - begin_offset)
        return json.dumps({"lag": lag})
    except Exception as e:
        print(f"[WARN] Kafka状态检查失败: {e}")
        return json.dumps({"lag": -1, "error": "无法连接Kafka"})


# ============================================================
# 主入口
# ============================================================
if __name__ == '__main__':
    print("=" * 60)
    print("  正在启动监控面板服务器...")
    print("=" * 60)

    # 先检查依赖服务是否可用
    print("\n--- 环境检查 ---")
    kafka_ok = check_kafka()
    redis_ok = check_redis()

    if not kafka_ok:
        print("\n[CRITICAL] Kafka不可用！请先启动Kafka和Zookeeper服务")
        print("  在Kafka目录下运行:")
        print("  Windows: .\\bin\\windows\\zookeeper-server-start.bat .\\config\\zookeeper.properties")
        print("  Windows: .\\bin\\windows\\kafka-server-start.bat .\\config\\server.properties")
    if not redis_ok:
        print("\n[CRITICAL] Redis不可用！Flink结果将无法显示")
        print("  请确保Redis服务正在运行")

    print("\n--- 启动后台服务 ---")
    # 启动Kafka消费者线程
    threading.Thread(target=start_kafka_consumer, daemon=True).start()
    # 启动Flink结果监听线程
    threading.Thread(target=start_flink_result_consumer, daemon=True).start()

    print("\n" + "=" * 60)
    print("  服务器已启动! 请在浏览器访问: http://localhost:5000")
    print("=" * 60)

    socketio.run(app, port=5000, host='0.0.0.0', debug=False)