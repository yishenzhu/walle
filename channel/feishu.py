"""飞书通知观察者：把 notify 流推送到飞书群（自定义机器人 webhook）。

模式 A（纯推送）：agent 运行时把 Delta / 工具事件推送到飞书群，方便手机查看。
模式 B（收发）演进时，此文件扩展为 FeishuChannel（实现 Channel）。
"""
import base64
import hashlib
import hmac
import logging
import time

import httpx

from ..schemas import Delta, DeltaEnd, NotificationUnion
from .render import render_notification

logger = logging.getLogger(__name__)

FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/{token}"


class FeishuObserver:
    """飞书推送观察者。

    - Delta 流式增量：缓冲，回合结束（DeltaEnd）统一发送，避免刷屏
    - 工具事件（ToolStart / ToolResult / Error）：即时发送
    - 可选签名校验（secret）
    """

    def __init__(self, webhook: str, secret: str | None = None):
        self._webhook = webhook
        self._secret = secret
        self._client: httpx.AsyncClient | None = None
        self._buffer: list[str] = []

    async def _send(self, text: str) -> None:
        if not text.strip():
            return
        payload = {"msg_type": "text", "content": {"text": text}}
        if self._secret:
            ts = str(int(time.time()))
            payload["timestamp"] = ts
            payload["sign"] = self._sign(ts, self._secret)
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=10.0)
            resp = await self._client.post(self._webhook, json=payload)
            resp.raise_for_status()
        except Exception as err:  # 推送失败不拖垮主链路
            logger.warning(f"feishu push failed: {err}")

    @staticmethod
    def _sign(timestamp: str, secret: str) -> str:
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
        ).digest()
        return base64.b64encode(hmac_code).decode("utf-8")

    async def __call__(self, n: NotificationUnion) -> None:
        match n:
            case Delta(delta=delta):
                self._buffer.append(delta)  # 缓冲流式增量
            case DeltaEnd():
                text = "".join(self._buffer)
                self._buffer.clear()
                if text:
                    await self._send(text)
            case _:
                text, _ = render_notification(n)
                if text:
                    await self._send(text)
