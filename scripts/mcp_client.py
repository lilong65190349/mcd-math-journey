"""麦麦取经录 —— 纯标准库 MCP 客户端。

本模块只用 Python 标准库实现 MCP（Model Context Protocol）客户端，
协议形态为 JSON-RPC 2.0 over Streamable HTTP，因此评委把仓库 clone
下来、装好 Python 3.13 即可直接运行，不需要 pip 安装任何依赖。

设计要点
--------
1. 令牌只从环境变量 ``MCD_MCP_TOKEN`` 读取，代码里不存在任何真实凭证。
2. 调用 ``tools/call`` 之前强制做**只读白名单**校验，白名单之外的工具名
   一律抛 :class:`PermissionError`，从机制上杜绝下单、代付、抽奖等写操作。
3. 响应体同时兼容 ``text/event-stream``（SSE，逐行 ``data:`` 拼接）与
   普通 ``application/json``，两种形态都能解析。
4. 调用顺序遵循 MCP 规范：``initialize`` → ``notifications/initialized``
   → ``tools/call``。

参考：MCP 规范 2024-11-05（JSON-RPC 2.0 传输层）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_ENDPOINT: str = "https://mcp.mcd.cn"
"""麦当劳 MCP 服务端点。"""

TOKEN_ENV: str = "MCD_MCP_TOKEN"
"""保存访问令牌的环境变量名。"""

PROTOCOL_VERSION: str = "2024-11-05"
"""客户端声明的 MCP 协议版本。"""

CLIENT_INFO: Dict[str, str] = {
    "name": "mcd-math-journey",
    "version": "1.0.0",
}
"""上报给服务端的客户端信息。"""

USER_AGENT: str = "mcd-math-journey/1.0 (python-stdlib-urllib)"

READONLY_WHITELIST: frozenset = frozenset(
    {
        "list-nutrition-foods",   # 餐品营养信息
        "query-meals",            # 可售餐品列表
        "query-meal-detail",      # 餐品详情
        "calculate-price",        # 价格计算（用于校验算术题答案）
        "available-coupons",      # 麦麦省券列表
        "campaign-calendar",      # 活动日历
        "mall-points-products",   # 商城积分商品
        "now-time-info",          # 当前时间
    }
)
"""只读工具白名单。白名单之外的工具名一律拒绝调用。"""

FORBIDDEN_WRITE_TOOLS: Tuple[str, ...] = (
    "create-order",
    "cancel-order",
    "delivery-create-address",
    "mall-create-order",
    "party-order-create",
    "draw-lottery",
    "auto-bind-coupons",
)
"""明确禁止的写操作工具（会改变账号状态 / 产生真实交易）。"""


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class McpError(Exception):
    """MCP 相关异常基类。"""


class McpConfigError(McpError):
    """配置错误：缺少令牌、端点非法等。"""


class McpProtocolError(McpError):
    """协议错误：HTTP 异常、响应体无法解析、JSON-RPC 结构不合法等。"""


class McpToolError(McpError):
    """工具执行错误：服务端返回 JSON-RPC error 或 isError=true。"""


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------


class McpClient:
    """一个最小的 MCP Streamable HTTP 客户端。

    Attributes:
        endpoint: MCP 服务端点 URL。
        timeout: 单次 HTTP 请求超时秒数。
    """

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        token: Optional[str] = None,
        timeout: float = 20.0,
    ) -> None:
        """初始化客户端。

        Args:
            endpoint: MCP 服务端点，默认 ``https://mcp.mcd.cn``。
            token: 访问令牌；为 ``None`` 时延迟到真正请求前从环境变量读取。
            timeout: HTTP 超时（秒），默认 20 秒。
        """
        self.endpoint: str = (endpoint or DEFAULT_ENDPOINT).rstrip("/")
        self.timeout: float = float(timeout)
        self._token: Optional[str] = token
        self._session_id: Optional[str] = None
        self._initialized: bool = False
        self._protocol_version: str = PROTOCOL_VERSION
        self._next_id: int = 0
        self.last_error: str = ""

    # -- 令牌 ---------------------------------------------------------------

    def _require_token(self) -> str:
        """读取并校验令牌。

        Returns:
            非空令牌字符串。

        Raises:
            McpConfigError: 环境变量缺失或为空。
        """
        token = self._token
        if token is None:
            token = os.environ.get(TOKEN_ENV, "")
        token = (token or "").strip()
        if token.startswith("Bearer "):
            token = token[len("Bearer ") :].strip()
        if not token:
            raise McpConfigError(
                f"未找到 MCP 访问令牌。请先设置环境变量 {TOKEN_ENV}，"
                f"例如：export {TOKEN_ENV}=你的令牌"
            )
        self._token = token
        return token

    def has_token(self) -> bool:
        """判断当前是否已配置令牌（不发起网络请求）。"""
        try:
            self._require_token()
        except McpConfigError:
            return False
        return True

    # -- HTTP 底层 ----------------------------------------------------------

    def _headers(self, with_token: bool) -> Dict[str, str]:
        """构造请求头。"""
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": USER_AGENT,
            "MCP-Protocol-Version": self._protocol_version,
        }
        if with_token:
            headers["Authorization"] = f"Bearer {self._require_token()}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, payload: Dict[str, Any], with_token: bool = True) -> Tuple[bytes, str]:
        """发送一次 HTTP POST。

        Args:
            payload: JSON-RPC 消息。
            with_token: 是否携带 Authorization 头（通知类消息同样需要）。

        Returns:
            ``(响应原始字节, Content-Type)``。

        Raises:
            McpConfigError: 401/403 或缺少令牌。
            McpProtocolError: 网络错误或其它非 2xx 状态码。
        """
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint, data=body, headers=self._headers(with_token), method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "") or ""
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self._session_id = session_id
                return raw, content_type
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:400]
            except Exception:  # pragma: no cover - 读取失败时忽略细节
                detail = ""
            if exc.code in (401, 403):
                raise McpConfigError(
                    f"MCP 鉴权失败（HTTP {exc.code}）。请检查环境变量 {TOKEN_ENV} 是否有效。"
                    f"服务端返回：{detail}"
                ) from exc
            raise McpProtocolError(f"MCP 请求失败（HTTP {exc.code}）：{detail}") from exc
        except urllib.error.URLError as exc:
            raise McpProtocolError(f"无法连接 MCP 服务 {self.endpoint}：{exc.reason}") from exc
        except TimeoutError as exc:  # pragma: no cover - 网络超时
            raise McpProtocolError(f"MCP 请求超时（{self.timeout} 秒）。") from exc

    # -- 响应解析 -----------------------------------------------------------

    @staticmethod
    def _split_sse(text: str) -> List[str]:
        """把 SSE 文本切成若干条 ``data`` 负载。

        SSE 事件之间以空行分隔，同一事件的多个 ``data:`` 行需用换行拼接。
        以 ``:`` 开头的行为注释，``event:`` / ``id:`` / ``retry:`` 行忽略。

        Args:
            text: 完整的响应文本。

        Returns:
            每个元素是一条可尝试解析为 JSON 的字符串。
        """
        events: List[str] = []
        buffer: List[str] = []
        for line in text.splitlines():
            if line == "":
                if buffer:
                    events.append("\n".join(buffer))
                    buffer = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                value = line[5:]
                if value.startswith(" "):
                    value = value[1:]
                buffer.append(value)
            # event: / id: / retry: 对本客户端无意义，忽略
        if buffer:
            events.append("\n".join(buffer))
        return events

    def _parse_response(self, raw_bytes: bytes, content_type: str = "") -> Dict[str, Any]:
        """解析 MCP 响应，兼容 SSE 与纯 JSON 两种形态。

        Args:
            raw_bytes: 响应原始字节。
            content_type: 响应头中的 Content-Type。

        Returns:
            一条 JSON-RPC 消息（含 ``result`` 或 ``error``）。

        Raises:
            McpProtocolError: 响应体为空或无法解析出合法的 JSON-RPC 消息。
        """
        if not raw_bytes:
            raise McpProtocolError("MCP 服务返回空响应体。")
        text = raw_bytes.decode("utf-8", "replace").strip()
        if not text:
            raise McpProtocolError("MCP 服务返回空白响应体。")

        candidates: List[Dict[str, Any]] = []
        lowered = (content_type or "").lower()
        looks_like_sse = "text/event-stream" in lowered or text.startswith("data:") or "\ndata:" in text

        if looks_like_sse:
            for chunk in self._split_sse(text):
                chunk = chunk.strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    parsed = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    candidates.append(parsed)
        if not candidates:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise McpProtocolError(
                    f"MCP 响应既不是合法 JSON 也不是可解析的 SSE：{text[:200]}"
                ) from exc
            if isinstance(parsed, dict):
                candidates.append(parsed)
            elif isinstance(parsed, list):
                candidates.extend([item for item in parsed if isinstance(item, dict)])

        if not candidates:
            raise McpProtocolError("MCP 响应中没有可用的 JSON-RPC 消息。")

        # 优先返回带 result / error 的消息，其次返回最后一条。
        for message in reversed(candidates):
            if "result" in message or "error" in message:
                return message
        return candidates[-1]

    # -- JSON-RPC 调用 ------------------------------------------------------

    def _request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """发送一次带 id 的 JSON-RPC 请求并返回消息。"""
        self._next_id += 1
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        raw, content_type = self._post(payload)
        return self._parse_response(raw, content_type)

    def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """发送一次 JSON-RPC 通知（无 id，不需要响应体）。"""
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        try:
            self._post(payload)
        except McpConfigError:
            raise
        except McpProtocolError:
            # 通知失败不阻断主流程：部分服务端对通知返回 202 空体。
            return

    def initialize(self) -> Dict[str, Any]:
        """完成 MCP 握手。

        Returns:
            服务端返回的 ``initialize`` 结果字典。

        Raises:
            McpConfigError: 缺少令牌或鉴权失败。
            McpProtocolError: 协议层错误。
        """
        message = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        if message.get("error"):
            error = message["error"]
            raise McpProtocolError(f"initialize 失败：{error.get('message', error)}")
        result = message.get("result") or {}
        if not isinstance(result, dict):
            raise McpProtocolError("initialize 返回结构异常。")
        self._protocol_version = str(result.get("protocolVersion") or PROTOCOL_VERSION)
        self._notify("notifications/initialized", {})
        self._initialized = True
        return result

    def ensure_initialized(self) -> None:
        """确保已完成握手（幂等）。"""
        if not self._initialized:
            self.initialize()

    def list_tools(self) -> List[str]:
        """拉取服务端工具清单（只用于自检展示）。"""
        message = self._request("tools/list", {})
        if message.get("error"):
            return []
        tools = (message.get("result") or {}).get("tools") or []
        names: List[str] = []
        for tool in tools:
            if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                names.append(tool["name"])
        return names

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """调用一个只读工具。

        Args:
            name: 工具名，必须命中 :data:`READONLY_WHITELIST`。
            arguments: 工具入参。

        Returns:
            工具调用结果（``{"content": [...], "structuredContent": {...}}`` 形态）。

        Raises:
            PermissionError: 工具名不在只读白名单内。
            McpConfigError: 缺少令牌或鉴权失败。
            McpProtocolError: 协议层错误。
            McpToolError: 工具执行返回错误。
        """
        if not isinstance(name, str) or not name:
            raise PermissionError("工具名不能为空。")
        if name in FORBIDDEN_WRITE_TOOLS:
            raise PermissionError(
                f"工具 {name!r} 会改变账号状态，本项目为只读作品，禁止调用。"
            )
        if name not in READONLY_WHITELIST:
            raise PermissionError(
                f"工具 {name!r} 不在只读白名单内，已拒绝调用。"
                f"允许的工具：{', '.join(sorted(READONLY_WHITELIST))}"
            )

        self.ensure_initialized()
        message = self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        if message.get("error"):
            error = message["error"]
            detail = error.get("message", error) if isinstance(error, dict) else error
            raise McpToolError(f"工具 {name} 执行失败：{detail}")
        result = message.get("result") or {}
        if not isinstance(result, dict):
            raise McpProtocolError(f"工具 {name} 返回结构异常。")
        if result.get("isError"):
            raise McpToolError(f"工具 {name} 返回错误：{self.extract_text(result)[:300]}")
        return result

    # -- 结果解包 -----------------------------------------------------------

    @staticmethod
    def extract_text(result: Dict[str, Any]) -> str:
        """把工具结果的 ``content`` 数组拼成纯文本。"""
        parts: List[str] = []
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)

    @classmethod
    def extract_payload(cls, result: Dict[str, Any]) -> Any:
        """把工具结果解包成 Python 对象。

        优先使用 ``structuredContent``；否则尝试把 ``content`` 文本当 JSON 解析；
        再失败则返回 ``{"text": 原文}``。

        Args:
            result: :meth:`call_tool` 的返回值。

        Returns:
            解析后的对象（通常是 dict 或 list）。
        """
        structured = result.get("structuredContent")
        if isinstance(structured, (dict, list)) and structured:
            return structured
        text = cls.extract_text(result).strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    def close(self) -> None:
        """结束会话（尽力而为，失败忽略）。"""
        if not self._session_id:
            self._initialized = False
            return
        request = urllib.request.Request(
            self.endpoint, headers=self._headers(with_token=True), method="DELETE"
        )
        try:
            urllib.request.urlopen(request, timeout=min(self.timeout, 5.0)).close()
        except Exception:  # pragma: no cover - 关闭失败不影响结果
            pass
        finally:
            self._session_id = None
            self._initialized = False

    def __enter__(self) -> "McpClient":
        """上下文管理器入口。"""
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """上下文管理器出口，自动关闭会话。"""
        self.close()
