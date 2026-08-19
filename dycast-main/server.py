import asyncio
import websockets
import time
import json
import socket

def get_local_ip():
    """获取本机局域网 IP 地址"""
    try:
        # 连接到一个远程地址（不会实际发送数据）
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

async def echo(websocket, path):
    client_addr = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
    print(f'[{time.ctime()}]: 客户端已连接 - {client_addr} (路径: {path})')
    
    try:
        # 发送欢迎消息
        welcome_msg = json.dumps({
            "type": "welcome",
            "message": "WebSocket 连接成功！",
            "timestamp": time.ctime()
        }, ensure_ascii=False)
        await websocket.send(welcome_msg)
        print(f'[{time.ctime()}]: 已发送欢迎消息')
        
        async for message in websocket:
            print(f'[{time.ctime()}]: 收到消息 (长度: {len(message)} 字节)')
            print(f'消息内容: {message}')
            
            # 尝试解析 JSON 消息
            try:
                data = json.loads(message)
                print(f'解析为 JSON: {data}')
            except:
                print(f'消息为纯文本')
            
            # 发送响应
            response = json.dumps({
                "type": "response",
                "original": message,
                "message": f"服务端已收到消息: {message}",
                "timestamp": time.ctime()
            }, ensure_ascii=False)
            await websocket.send(response)
            print(f'[{time.ctime()}]: 已发送响应')
            
    except websockets.exceptions.ConnectionClosed as e:
        print(f'[{time.ctime()}]: 客户端已断开连接 - 代码: {e.code}, 原因: {e.reason}')
    except Exception as e:
        print(f'[{time.ctime()}]: 处理消息时出错: {type(e).__name__}: {e}')
    finally:
        print(f'[{time.ctime()}]: 连接处理结束 - {client_addr}')

async def main():
    host = '0.0.0.0'  # 绑定到所有网络接口，允许外部访问
    port = 8765
    local_ip = get_local_ip()
    
    print('=' * 60)
    print('WebSocket 服务器启动中...')
    print('=' * 60)
    print(f'本地访问地址: ws://localhost:{port}')
    print(f'局域网访问地址: ws://{local_ip}:{port}')
    print(f'外部访问地址: ws://<你的公网IP>:{port}')
    print('=' * 60)
    print('服务状态: 运行中，等待客户端连接...')
    print('=' * 60)
    print('提示:')
    print('  - 本地测试: 使用 ws://localhost:8765')
    print('  - 局域网访问: 使用 ws://' + local_ip + ':8765')
    print('  - 使用 test-websocket.html 页面或前端应用进行连接')
    print('=' * 60)
    
    # 绑定到 0.0.0.0 允许外部访问
    async with websockets.serve(echo, host, port, ping_interval=20, ping_timeout=10):
        await asyncio.Future()  # 永久运行

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\n服务器已停止')

'''
# 创建一个WebSocket服务端
# 用于接收解析到的弹幕数据
# 测试弹幕转发功能
'''