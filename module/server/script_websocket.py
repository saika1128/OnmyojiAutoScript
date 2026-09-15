# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

class ScriptWSManager:

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        # 等待连接
        await ws.accept()
        self.active_connections.append(ws)

    async def disconnect(self, ws: WebSocket):
        # 关闭时移除 ws 对象；可能已被移除，忽略 ValueError
        try:
            self.active_connections.remove(ws)
        except ValueError:
            pass
        try:
            # 给前端发送最后一次关闭信号
            await ws.close()
        except Exception:
            # 连接已坏时 close 也可能抛错，静默即可
            pass

    async def _send_all(self, sender) -> None:
        # 遍历副本：发送失败会移除连接，不能边遍历原列表边改
        dead = []
        for connection in list(self.active_connections):
            try:
                await sender(connection)
            except Exception:
                # 断连不只会抛 RuntimeError，任何发送失败都视为僵尸连接清理掉，
                # 否则 active_connections 只增不减、每次广播越遍历越慢
                dead.append(connection)
        for connection in dead:
            await self.disconnect(connection)

    async def broadcast(self, message: str):
        # 广播消息
        await self._send_all(lambda c: c.send_text(message))

    async def broadcast_state(self, data: dict):
        # 广播自身的状态
        await self._send_all(lambda c: c.send_json(data))

    async def broadcast_log(self, log: str):
        # 广播日志
        await self._send_all(lambda c: c.send_text(log))





