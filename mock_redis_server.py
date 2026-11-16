import socket
import threading
import time
import json

# 使用6380端口避免与可能存在的Redis冲突
REDIS_PORT = 6380
REDIS_HOST = "localhost"

print(f"\n{'=' * 50}")
print(f"  模拟 Redis 服务器启动中...")
print(f"  监听地址: {REDIS_HOST}:{REDIS_PORT}")
print(f"{'=' * 50}\n")

# 模拟Redis数据存储
redis_store = {}


def handle_client(client_socket):
    """处理客户端连接"""
    try:
        while True:
            data = client_socket.recv(1024).decode('utf-8')
            if not data:
                break

            command = data.strip().split()
            if not command:
                continue

            cmd = command[0].upper()

            if cmd == "PING":
                response = "+PONG\r\n"
                client_socket.sendall(response.encode('utf-8'))

            elif cmd == "SET":
                if len(command) >= 3:
                    key = command[1]
                    value = command[2]
                    expire = None
                    if "EX" in command:
                        ex_idx = command.index("EX")
                        if ex_idx + 1 < len(command):
                            expire = int(command[ex_idx + 1])
                    redis_store[key] = {"value": value, "expire": expire, "set_time": time.time()}
                    client_socket.sendall("+OK\r\n")
                else:
                    client_socket.sendall("$-1\r\n")

            elif cmd == "GET":
                if len(command) >= 2:
                    key = command[1]
                    if key in redis_store:
                        entry = redis_store[key]
                        if entry.get("expire") and time.time() - entry["set_time"] > entry["expire"]:
                            del redis_store[key]
                            client_socket.sendall("$-1\r\n")
                        else:
                            response = f"${len(entry['value'])}\r\n{entry['value']}\r\n"
                            client_socket.sendall(response.encode('utf-8'))
                    else:
                        client_socket.sendall("$-1\r\n")
                else:
                    client_socket.sendall("$-1\r\n")

            elif cmd == "DEL":
                if len(command) >= 2:
                    key = command[1]
                    if key in redis_store:
                        del redis_store[key]
                        client_socket.sendall(":1\r\n")
                    else:
                        client_socket.sendall(":0\r\n")
                else:
                    client_socket.sendall(":0\r\n")

            elif cmd == "KEYS":
                if len(command) >= 2:
                    pattern = command[1]
                    keys = [k for k in redis_store.keys()]
                    if keys:
                        response = "*" + str(len(keys)) + "\r\n"
                        for k in keys:
                            response += "$" + str(len(k)) + "\r\n" + k + "\r\n"
                        client_socket.sendall(response.encode('utf-8'))
                    else:
                        client_socket.sendall("*0\r\n")
                else:
                    client_socket.sendall("*0\r\n")

            elif cmd == "TTL":
                if len(command) >= 2:
                    key = command[1]
                    if key in redis_store:
                        entry = redis_store[key]
                        if entry.get("expire"):
                            remaining = int(entry["expire"] - (time.time() - entry["set_time"]))
                            client_socket.sendall(f":{remaining}\r\n")
                        else:
                            client_socket.sendall(":-1\r\n")
                    else:
                        client_socket.sendall(":-2\r\n")
                else:
                    client_socket.sendall(":-2\r\n")

            elif cmd == "EXPIRE":
                if len(command) >= 3:
                    key = command[1]
                    seconds = int(command[2])
                    if key in redis_store:
                        redis_store[key]["expire"] = seconds
                        redis_store[key]["set_time"] = time.time()
                        client_socket.sendall(":1\r\n")
                    else:
                        client_socket.sendall(":0\r\n")
                else:
                    client_socket.sendall(":0\r\n")

            elif cmd == "FLUSHALL":
                redis_store.clear()
                client_socket.sendall("+OK\r\n")

            elif cmd == "DBSIZE":
                client_socket.sendall(f":{len(redis_store)}\r\n")

            elif cmd == "INFO":
                info = f"# Server\nredis_version:5.0.14\n# Keyspace\n"
                client_socket.sendall(info.encode('utf-8'))

            else:
                client_socket.sendall("-ERR unknown command \'" + cmd + "\'\r\n")

    except Exception as e:
        print(f"  [ERROR] 处理客户端异常: {e}")
    finally:
        client_socket.close()
        print(f"  [INFO] 客户端连接已关闭")


# 创建服务器套接字
server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

try:
    server_socket.bind((REDIS_HOST, REDIS_PORT))
    server_socket.listen(5)
    print(f"\n{'=' * 50}")
    print(f"  模拟 Redis 服务器已成功启动!")
    print(f"  监听地址: {REDIS_HOST}:{REDIS_PORT}")
    print(f"  按 Ctrl+C 停止服务")
    print(f"{'=' * 50}\n")

    while True:
        client_socket, addr = server_socket.accept()
        print(f"  [INFO] 新连接来自: {addr}")
        client_thread = threading.Thread(target=handle_client, args=(client_socket,))
        client_thread.daemon = True
        client_thread.start()

except KeyboardInterrupt:
    print("\n\n  模拟 Redis 服务器已停止。")
except OSError as e:
    print(f"\n  [ERROR] 无法绑定到 {REDIS_HOST}:{REDIS_PORT}")
    print(f"  [ERROR] {e}")
    print(f"  提示: 端口 {REDIS_PORT} 可能被其他程序占用")
    print(f"  解决方法: 修改本脚本顶部的 REDIS_PORT 变量为其他端口")
finally:
    server_socket.close()