
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
环境诊断工具 - 一键检查所有依赖服务是否正常
运行方式: python check_env.py
"""
import sys
import socket
import subprocess

print("=" * 60)
print("  实时数据系统 - 环境诊断工具")
print("=" * 60)

results = []

# 1. 检查Python环境
print("\n--- [1/5] Python环境 ---")
print(f"Python版本: {sys.version}")
results.append(("Python", "OK", sys.version))

# 2. 检查Python包
print("\n--- [2/5] Python包检查 ---")
required_packages = {
    'flask': 'Web框架',
    'flask_socketio': 'WebSocket支持',
    'kafka': 'Kafka客户端',
    'redis': 'Redis客户端'
}

for pkg, desc in required_packages.items():
    try:
        __import__(pkg)
        print(f"  [OK] {pkg} ({desc})")
        results.append((pkg, "OK", f"已安装"))
    except ImportError:
        print(f"  [缺失] {pkg} ({desc}) - 请运行: pip install {pkg}")
        results.append((pkg, "MISSING", f"未安装"))

# 3. 检查Kafka连接
print("\n--- [3/5] Kafka连接检查 ---")
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3)
    result = sock.connect_ex(('localhost', 9092))
    sock.close()
    if result == 0:
        print("  [OK] Kafka (localhost:9092) 可连接")
        results.append(("Kafka端口", "OK", "9092端口开放"))
    else:
        print("  [失败] Kafka (localhost:9092) 无法连接")
        print("         请确保Kafka服务已启动")
        results.append(("Kafka端口", "FAILED", "9092端口不可达"))
except Exception as e:
    print(f"  [错误] {e}")
    results.append(("Kafka端口", "ERROR", str(e)))

# 4. 检查Zookeeper连接
print("\n--- [4/5] Zookeeper连接检查 ---")
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3)
    result = sock.connect_ex(('localhost', 2181))
    sock.close()
    if result == 0:
        print("  [OK] Zookeeper (localhost:2181) 可连接")
        results.append(("Zookeeper端口", "OK", "2181端口开放"))
    else:
        print("  [失败] Zookeeper (localhost:2181) 无法连接")
        print("         请确保Zookeeper服务已启动")
        results.append(("Zookeeper端口", "FAILED", "2181端口不可达"))
except Exception as e:
    print(f"  [错误] {e}")
    results.append(("Zookeeper端口", "ERROR", str(e)))

# 5. 检查Redis连接
print("\n--- [5/5] Redis连接检查 ---")
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3)
    result = sock.connect_ex(('localhost', 6379))
    sock.close()
    if result == 0:
        print("  [OK] Redis (localhost:6379) 可连接")
        results.append(("Redis端口", "OK", "6379端口开放"))
    else:
        print("  [失败] Redis (localhost:6379) 无法连接")
        print("         请确保Redis服务已启动")
        print("         Windows安装方法:")
        print("         1. 下载: https://github.com/tporadowski/redis/releases")
        print("         2. 安装 Redis-x64-5.0.14.1.msi")
        print("         3. 安装后Redis会自动作为Windows服务运行")
        results.append(("Redis端口", "FAILED", "6379端口不可达"))
except Exception as e:
    print(f"  [错误] {e}")
    results.append(("Redis端口", "ERROR", str(e)))

# 汇总结果
print("\n" + "=" * 60)
print("  诊断结果汇总")
print("=" * 60)

ok_count = sum(1 for r in results if r[1] == "OK")
fail_count = sum(1 for r in results if r[1] in ("FAILED", "MISSING"))

for name, status, detail in results:
    icon = "OK" if status == "OK" else ("XX" if status == "FAILED" else "!!")
    print(f"  [{icon}] {name}: {detail}")

print(f"\n总计: {ok_count}/{len(results)} 项通过")

if fail_count > 0:
    print(f"\n[警告] 有 {fail_count} 项未通过，请根据上方提示修复后再运行系统")
    sys.exit(1)
else:
    print("\n[恭喜] 所有检查项通过! 系统环境正常")
    print("\n启动顺序:")
    print("  1. 确保Kafka和Zookeeper正在运行")
    print("  2. 确保Redis服务正在运行")
    print("  3. 运行: python flink_redis_job.py")
    print("  4. 运行: python dashboard_server.py")
    print("  5. 浏览器访问: http://localhost:5000")
    sys.exit(0)