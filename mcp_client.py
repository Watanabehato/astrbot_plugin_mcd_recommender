"""
麦当劳 MCP 客户端
基于 Streamable HTTP 协议与麦当劳 MCP Server 通信
"""

import json
import httpx
from typing import Optional, Dict, Any, List
import logging

logger = logging.getLogger(__name__)


class McdMCPClient:
    """麦当劳 MCP 客户端"""

    MCP_SERVER_URL = "https://mcp.mcd.cn"
    MCP_VERSION = "2025-06-18"

    def __init__(self, mcp_token: str):
        self.mcp_token = mcp_token
        self.session_id = None
        self.request_id = 0
        self._initialized = False  # 是否已初始化
        self._client = httpx.AsyncClient(
            base_url=self.MCP_SERVER_URL,
            headers={
                "Authorization": f"Bearer {mcp_token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            timeout=30.0,
        )

    async def ensure_initialized(self):
        """确保 MCP 已初始化（只初始化一次）"""
        if self._initialized:
            return True
        return await self.initialize()

    async def initialize(self):
        """初始化 MCP 会话"""
        self.request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": self.MCP_VERSION,
                "capabilities": {
                    "tools": {}
                },
                "clientInfo": {
                    "name": "astrbot-mcd-recommender",
                    "version": "1.0.0"
                }
            }
        }

        try:
            logger.debug(f"发送 MCP initialize 请求...")
            response = await self._client.post("/", json=payload)
            logger.debug(f"MCP initialize 响应状态: {response.status_code}")
            response.raise_for_status()
            data = response.json()
            if "result" in data:
                self.session_id = data["result"].get("sessionId")
                self._initialized = True
                logger.info("麦当劳 MCP 初始化成功")
                return True
            logger.error(f"MCP 初始化失败，响应中无 result: {data}")
            return False
        except httpx.HTTPStatusError as e:
            logger.error(f"MCP 初始化 HTTP 错误 {e.response.status_code}: {e.response.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"MCP 初始化异常: {e}", exc_info=True)
            return False

    async def call_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        调用 MCP 工具

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            工具调用结果（result 字段内容）
        """
        # 确保已初始化
        if not self._initialized:
            init_ok = await self.initialize()
            if not init_ok:
                return {"error": "MCP 初始化失败"}

        self.request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {}
            }
        }

        try:
            logger.debug(f"调用 MCP 工具: {tool_name}, 参数: {json.dumps(arguments or {}, ensure_ascii=False)[:100]}")
            response = await self._client.post("/", json=payload)
            logger.debug(f"MCP 工具 {tool_name} 响应状态: {response.status_code}")
            response.raise_for_status()
            data = response.json()

            if "result" in data:
                result = data["result"]
                # 检查是否有错误
                if isinstance(result, dict) and result.get("isError"):
                    logger.error(f"工具 {tool_name} 返回错误: {result}")
                return result
            elif "error" in data:
                logger.error(f"工具 {tool_name} 调用错误: {data['error']}")
                return {"error": data["error"]}
            else:
                logger.error(f"工具 {tool_name} 未知响应格式: {str(data)[:200]}")
                return {"error": "未知响应格式"}
        except httpx.HTTPStatusError as e:
            logger.error(f"工具 {tool_name} HTTP 错误 {e.response.status_code}: {e.response.text[:200]}")
            # 如果是 401/403，重置初始化状态，下次重试
            if e.response.status_code in (401, 403):
                self._initialized = False
            return {"error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            logger.error(f"工具 {tool_name} 调用异常: {e}", exc_info=True)
            return {"error": str(e)}

    async def list_tools(self) -> List[Dict[str, Any]]:
        """列出所有可用工具"""
        self.request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": "tools/list",
            "params": {}
        }

        try:
            response = await self._client.post("/", json=payload)
            response.raise_for_status()
            data = response.json()
            if "result" in data:
                return data["result"].get("tools", [])
            return []
        except Exception as e:
            logger.error(f"列出工具异常: {e}")
            return []

    # ========== 具体工具封装 ==========

    async def get_nutrition_foods(self) -> Dict[str, Any]:
        """获取餐品营养信息列表"""
        return await self.call_tool("list-nutrition-foods")

    async def query_nearby_stores(
        self,
        city: str,
        keyword: str = "",
        be_type: int = 1,
        search_type: int = 2
    ) -> Dict[str, Any]:
        """
        查询附近门店

        Args:
            city: 城市名称
            keyword: 位置关键词
            be_type: 业务类型 1=到店自取，5=得来速
            search_type: 搜索类型 2=按位置搜索
        """
        return await self.call_tool("query-nearby-stores", {
            "searchType": search_type,
            "beType": be_type,
            "city": city,
            "keyword": keyword
        })

    async def query_meals(
        self,
        store_code: str,
        be_code: str = "",
        order_type: int = 1,
        be_type: int = 1,
        reservation_date: str = ""
    ) -> Dict[str, Any]:
        """
        查询当前门店可售卖的餐品列表

        Args:
            store_code: 门店编码
            be_code: BE编码
            order_type: 1=到店，2=外送
            be_type: 业务类型
            reservation_date: 预约时间
        """
        params = {
            "storeCode": store_code,
            "orderType": order_type,
            "beType": be_type,
        }
        if be_code:
            params["beCode"] = be_code
        if reservation_date:
            params["reservationDate"] = reservation_date

        return await self.call_tool("query-meals", params)

    async def query_meal_detail(
        self,
        code: str,
        store_code: str = "",
        be_code: str = "",
        order_type: int = 1,
        be_type: int = 1
    ) -> Dict[str, Any]:
        """查询餐品详情"""
        params = {
            "code": code,
            "orderType": order_type,
            "beType": be_type,
        }
        if store_code:
            params["storeCode"] = store_code
        if be_code:
            params["beCode"] = be_code

        return await self.call_tool("query-meal-detail", params)

    async def query_store_coupons(
        self,
        store_code: str,
        be_code: str = "",
        order_type: int = 1,
        be_type: int = 1
    ) -> Dict[str, Any]:
        """查询门店可用优惠券"""
        params = {
            "storeCode": store_code,
            "orderType": order_type,
            "beType": be_type,
        }
        if be_code:
            params["beCode"] = be_code

        return await self.call_tool("query-store-coupons", params)

    async def get_campaign_calendar(self, specified_date: str = "") -> Dict[str, Any]:
        """
        查询活动日历

        Args:
            specified_date: 指定日期 yyyy-MM-dd
        """
        params = {}
        if specified_date:
            params["specifiedDate"] = specified_date
        return await self.call_tool("campaign-calendar", params)

    async def get_available_coupons(self) -> Dict[str, Any]:
        """查询麦麦省可用优惠券"""
        return await self.call_tool("available-coupons")

    async def get_now_time_info(self) -> Dict[str, Any]:
        """获取当前时间信息"""
        return await self.call_tool("now-time-info")

    async def close(self):
        """关闭客户端"""
        await self._client.aclose()
