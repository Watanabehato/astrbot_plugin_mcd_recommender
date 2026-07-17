"""
麦当劳美食推荐插件
检测关键词后调用 LLM 智能推荐麦当劳美食
"""

import re
import json
import logging
from typing import Optional, List, Dict, Any

from astrbot.api.star import Star, Context
from astrbot.api.event import filter, AstrMessageEvent

from .mcp_client import McdMCPClient

logger = logging.getLogger(__name__)


class Main(Star):
    """麦当劳美食推荐插件主类"""

    def __init__(self, context: Context, config: Optional[dict] = None):
        super().__init__(context)
        self.config = config or {}
        self.mcp_client: Optional[McdMCPClient] = None

        # 打印配置信息方便排查
        mcp_token = self.config.get("mcp_token", "")
        keywords = self.config.get("trigger_keywords", "吃什么,麦当劳,麦麦,今天吃啥,午饭,晚饭,早餐,夜宵")
        logger.info(f"[麦当劳推荐] 插件加载中... 版本: 1.0.3")
        logger.info(f"[麦当劳推荐] 配置: token={'已配置(' + mcp_token[:8] + '...)' if mcp_token else '❌未配置'}, 关键词={keywords}")

        self._init_mcp_client()

    def _init_mcp_client(self):
        """初始化 MCP 客户端"""
        mcp_token = self.config.get("mcp_token", "")
        if mcp_token:
            self.mcp_client = McdMCPClient(mcp_token)
            logger.info(f"✅ 麦当劳 MCP 客户端已初始化 (token: {mcp_token[:8]}...)")
        else:
            logger.warning("❌ 未配置麦当劳 MCP Token！请在插件配置中填入 MCP Token")
            logger.warning("❌ 插件将只能使用降级推荐模式（固定推荐列表）")

    def _get_trigger_keywords(self) -> List[str]:
        """获取触发关键词列表"""
        keywords_str = self.config.get(
            "trigger_keywords",
            "吃什么,麦当劳,麦麦,今天吃啥,午饭,晚饭,早餐,夜宵"
        )
        return [kw.strip() for kw in keywords_str.split(",") if kw.strip()]

    def _match_keyword(self, message: str) -> Optional[str]:
        """
        检测消息中是否包含触发关键词

        Returns:
            匹配到的关键词，未匹配返回 None
        """
        keywords = self._get_trigger_keywords()
        match_mode = self.config.get("keyword_match_mode", "包含匹配")
        message_lower = message.lower().strip()

        if match_mode == "精确匹配":
            for kw in keywords:
                if message_lower == kw.lower():
                    return kw
        elif match_mode == "正则匹配":
            for kw in keywords:
                try:
                    if re.search(kw, message, re.IGNORECASE):
                        return kw
                except re.error:
                    if kw.lower() in message_lower:
                        return kw
        else:  # 包含匹配
            for kw in keywords:
                if kw.lower() in message_lower:
                    return kw

        return None

    def _get_order_type_config(self):
        """获取点餐方式配置"""
        order_type_str = self.config.get("order_type", "到店取餐")
        if order_type_str == "麦乐送外送":
            return 2, 2  # order_type=2(外送), be_type=2(麦乐送)
        return 1, 1  # order_type=1(到店), be_type=1(到店取餐)

    def _get_recommendation_style_prompt(self) -> str:
        """获取推荐风格的提示词"""
        style = self.config.get("recommendation_style", "活泼可爱")
        styles = {
            "活泼可爱": "用活泼可爱、元气满满的语气，像朋友一样推荐美食，适当使用emoji表情",
            "专业简洁": "用专业简洁的语气，清晰列出推荐理由和价格信息",
            "幽默风趣": "用幽默风趣的语气，带点调侃和段子，让推荐更有趣",
            "温暖治愈": "用温暖治愈的语气，像关心你的朋友一样推荐暖心美食"
        }
        return styles.get(style, styles["活泼可爱"])

    def _extract_json_from_text(self, text: str) -> Optional[Dict[str, Any]]:
        """
        从文本中提取 JSON 对象（处理 Markdown 包装的情况）

        Args:
            text: 可能包含 JSON 的文本

        Returns:
            解析后的 JSON 对象，失败返回 None
        """
        if not text:
            return None

        # 情况 1: 文本直接就是 JSON
        stripped = text.strip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass

        # 情况 2: JSON 在 Markdown 代码块中 ```json ... ```
        code_block_match = re.search(
            r"```(?:json)?\s*\n(.*?)```",
            text,
            re.DOTALL | re.IGNORECASE
        )
        if code_block_match:
            try:
                return json.loads(code_block_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 情况 3: 文本中包含 JSON 对象，找到第一个 { 开始到最后一个 } 结束
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            # 尝试从多个可能的 JSON 片段中解析
            candidate = text[first_brace:last_brace + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                # 尝试逐字符收缩，找到有效的 JSON
                for end in range(last_brace, first_brace, -1):
                    try:
                        return json.loads(text[first_brace:end + 1])
                    except json.JSONDecodeError:
                        continue

        return None

    def _parse_mcp_response(self, resp: Dict[str, Any]) -> Optional[Any]:
        """
        解析 MCP 工具调用响应

        Args:
            resp: MCP 工具调用返回的原始响应（result 字段内容）

        Returns:
            解析后的数据，失败返回 None
        """
        if not resp:
            return None

        # 优先使用 structuredContent（已解析的结构化数据）
        if "structuredContent" in resp and resp["structuredContent"]:
            sc = resp["structuredContent"]
            if isinstance(sc, dict):
                # 检查是否是错误
                if sc.get("isError") or (sc.get("code") and sc.get("code") != 200):
                    logger.warning(f"MCP 返回错误: {sc.get('message', sc)}")
                    return None
                # 返回 data 字段
                if "data" in sc:
                    return sc["data"]
                return sc

        # 其次检查 isError 字段
        if resp.get("isError"):
            logger.warning(f"MCP 返回错误: {resp}")
            return None

        # 最后尝试从 content 文本中解析
        if "content" in resp:
            content = resp["content"]
            if isinstance(content, list) and len(content) > 0:
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text", "")
                        if not text:
                            continue

                        # 尝试提取 JSON
                        json_data = self._extract_json_from_text(text)
                        if json_data:
                            if json_data.get("success") and "data" in json_data:
                                return json_data["data"]
                            return json_data

                        # 如果没有 JSON，返回纯文本（如 Markdown 格式的活动日历）
                        return text
            elif isinstance(content, str):
                json_data = self._extract_json_from_text(content)
                if json_data:
                    if json_data.get("success") and "data" in json_data:
                        return json_data["data"]
                    return json_data
                return content

        # 如果有直接的 data 字段
        if "data" in resp:
            return resp["data"]

        # 如果有 error 字段
        if "error" in resp:
            return None

        return None

    async def _get_meals_data(self, city: str, keyword: str = "") -> Dict[str, Any]:
        """
        获取餐品数据

        Args:
            city: 城市名称
            keyword: 位置关键词

        Returns:
            包含餐品信息的字典
        """
        result = {
            "city": city,
            "store": {},  # 默认空字典，避免 None.get() 报错
            "meals": {},
            "categories": [],
            "coupons": [],
            "nutrition": None,
            "campaign": None,
            "error": None
        }

        if not self.mcp_client:
            result["error"] = "未配置麦当劳 MCP Token"
            logger.warning("未配置麦当劳 MCP Token，使用降级推荐")
            return result

        try:
            # 确保 MCP 已初始化
            init_ok = await self.mcp_client.ensure_initialized()
            if not init_ok:
                result["error"] = "MCP 初始化失败"
                logger.error("MCP 初始化失败，使用降级推荐")
                return result

            order_type, be_type = self._get_order_type_config()
            default_store_code = self.config.get("default_store_code", "")

            store_code = default_store_code
            store_info = None

            # 如果没有指定门店，查询附近门店
            if not store_code:
                logger.debug(f"查询 {city} 附近的门店...")
                stores_resp = await self.mcp_client.query_nearby_stores(
                    city=city,
                    keyword=keyword,
                    be_type=be_type
                )
                stores_data = self._parse_mcp_response(stores_resp)
                if stores_data and isinstance(stores_data, list) and len(stores_data) > 0:
                    store_info = stores_data[0]
                    store_code = store_info.get("storeCode", "")
                    logger.info(f"找到门店: {store_info.get('storeName', '未知')} (code: {store_code})")
                else:
                    logger.warning(f"未找到 {city} 的门店数据")

            result["store"] = store_info or {}

            # 查询餐品列表
            if store_code:
                logger.debug(f"查询门店 {store_code} 的餐品列表...")
                meals_resp = await self.mcp_client.query_meals(
                    store_code=store_code,
                    order_type=order_type,
                    be_type=be_type
                )
                meals_data = self._parse_mcp_response(meals_resp)
                if meals_data and isinstance(meals_data, dict):
                    result["categories"] = meals_data.get("categories", [])
                    result["meals"] = meals_data.get("meals", {})
                    logger.info(f"获取到 {len(result['meals'])} 款餐品")
                else:
                    logger.warning(f"解析餐品数据失败: {meals_resp}")

                # 查询优惠券
                if self.config.get("enable_coupon_info", True):
                    logger.debug(f"查询门店 {store_code} 的优惠券...")
                    coupons_resp = await self.mcp_client.query_store_coupons(
                        store_code=store_code,
                        order_type=order_type,
                        be_type=be_type
                    )
                    coupons_data = self._parse_mcp_response(coupons_resp)
                    if coupons_data and isinstance(coupons_data, list):
                        result["coupons"] = coupons_data
                        logger.info(f"获取到 {len(coupons_data)} 张优惠券")
                    else:
                        logger.debug("未获取到优惠券数据")

            # 获取营养信息
            if self.config.get("enable_nutrition_info", True):
                logger.debug("获取营养信息...")
                nutrition_resp = await self.mcp_client.get_nutrition_foods()
                nutrition_data = self._parse_mcp_response(nutrition_resp)
                if nutrition_data:
                    result["nutrition"] = nutrition_data if isinstance(nutrition_data, str) else str(nutrition_data)
                    logger.debug("营养信息获取成功")

            # 获取活动日历
            try:
                logger.debug("获取活动日历...")
                campaign_resp = await self.mcp_client.get_campaign_calendar()
                campaign_data = self._parse_mcp_response(campaign_resp)
                if campaign_data and isinstance(campaign_data, str):
                    result["campaign"] = campaign_data[:1000]
                    logger.debug("活动日历获取成功")
            except Exception as e:
                logger.warning(f"获取活动日历失败: {e}")

        except Exception as e:
            logger.error(f"获取餐品数据异常: {e}", exc_info=True)
            result["error"] = str(e)

        return result

    def _format_meals_for_prompt(self, meals_data: Dict[str, Any]) -> str:
        """将餐品数据格式化为提示词"""
        parts = []

        if meals_data.get("store"):
            store = meals_data["store"]
            parts.append(f"【门店信息】")
            parts.append(f"门店名称: {store.get('storeName', '未知')}")
            parts.append(f"地址: {store.get('address', '未知')}")
            parts.append("")

        if meals_data.get("categories"):
            parts.append("【餐品分类】")
            for cat in meals_data["categories"]:
                cat_name = cat.get("name", "未知分类")
                meals_in_cat = cat.get("meals", [])
                parts.append(f"- {cat_name}: {len(meals_in_cat)}款")
            parts.append("")

        if meals_data.get("meals"):
            parts.append("【当前可售餐品（部分）】")
            meals = meals_data["meals"]
            count = 0
            for code, meal in meals.items():
                if count >= 20:
                    break
                name = meal.get("name", "未知")
                price = meal.get("currentPrice", "未知")
                original_price = meal.get("originalPrice", "")
                discount = meal.get("discountType", "")
                price_str = f"¥{price}"
                if original_price and str(original_price) != str(price):
                    price_str += f" (原价¥{original_price})"
                if discount:
                    price_str += f" [{discount}]"
                parts.append(f"- {name}: {price_str}")
                count += 1
            parts.append("")

        if meals_data.get("coupons"):
            parts.append("【可用优惠券】")
            for coupon in meals_data["coupons"][:5]:
                title = coupon.get("title", "未知")
                parts.append(f"- {title}")
            parts.append("")

        if meals_data.get("campaign"):
            parts.append("【当前活动】")
            parts.append(meals_data["campaign"][:800])
            parts.append("")

        if meals_data.get("nutrition"):
            parts.append("【营养信息参考】")
            parts.append(str(meals_data["nutrition"])[:600])
            parts.append("")

        if not parts:
            parts.append("（暂无实时餐品数据，将基于通用知识推荐）")

        return "\n".join(parts)

    async def _generate_recommendation(
        self,
        user_message: str,
        meals_data: Dict[str, Any],
        event: AstrMessageEvent
    ) -> str:
        """
        使用 LLM 生成美食推荐

        Args:
            user_message: 用户消息
            meals_data: 餐品数据
            event: 消息事件

        Returns:
            推荐结果文本
        """
        style_prompt = self._get_recommendation_style_prompt()
        max_rec = self.config.get("max_recommendations", 5)
        meals_context = self._format_meals_for_prompt(meals_data)

        system_prompt = f"""你是一个专业的麦当劳美食推荐助手。{style_prompt}。

请根据用户的需求，从麦当劳餐品中推荐最合适的美食。

推荐要求：
1. 推荐 {max_rec} 款以内的餐品
2. 每款推荐要说明推荐理由
3. 如果有优惠活动或优惠券，可以提及
4. 如果用户有特殊需求（如减脂、早餐、套餐等），请针对性推荐
5. 结尾可以加一句温馨的祝福语

以下是当前可用的餐品信息：
{meals_context}

注意：如果餐品信息不完整或获取失败，请基于你对麦当劳的了解进行推荐，并说明数据可能有所更新，以实际门店为准。"""

        user_prompt = f"用户说：{user_message}\n\n请为用户推荐合适的麦当劳美食。"

        try:
            # 获取 LLM Provider ID
            llm_provider_id = self.config.get("llm_provider_id", "")

            # 如果用户没配置，尝试获取第一个可用的 provider
            if not llm_provider_id:
                try:
                    providers = self.context.get_all_providers()
                    if providers:
                        # 获取第一个 provider 的 id
                        for p in providers:
                            if hasattr(p, 'id'):
                                llm_provider_id = p.id
                                break
                        if not llm_provider_id:
                            llm_provider_id = providers[0].id
                        logger.info(f"[麦当劳推荐] 使用默认 LLM Provider: {llm_provider_id}")
                except Exception as e:
                    logger.warning(f"[麦当劳推荐] 获取 Provider 列表失败: {e}")

            if not llm_provider_id:
                logger.error("[麦当劳推荐] 没有可用的 LLM Provider")
                return self._get_fallback_recommendation(user_message, meals_data)

            logger.info(f"[麦当劳推荐] 调用 LLM 生成推荐 (provider: {llm_provider_id})...")

            resp = await self.context.llm_generate(
                chat_provider_id=llm_provider_id,
                prompt=user_prompt,
                system_prompt=system_prompt,
            )

            # 解析返回结果
            if resp is None:
                logger.warning("[麦当劳推荐] LLM 返回 None")
                return self._get_fallback_recommendation(user_message, meals_data)

            # LLMResponse 的 completion_text 属性获取纯文本
            if hasattr(resp, 'completion_text'):
                text = resp.completion_text
                if text:
                    logger.info("[麦当劳推荐] LLM 生成成功!")
                    return text
                logger.warning("[麦当劳推荐] LLM completion_text 为空")
            elif hasattr(resp, 'result_chain') and resp.result_chain:
                # 从消息链中提取文本
                texts = []
                for item in resp.result_chain:
                    if hasattr(item, 'text'):
                        texts.append(item.text)
                    elif isinstance(item, str):
                        texts.append(item)
                if texts:
                    logger.info("[麦当劳推荐] LLM 生成成功!")
                    return "\n".join(texts)

            # 如果以上都失败，尝试转字符串
            result_str = str(resp)
            if result_str and result_str != "None":
                return result_str

            logger.warning("[麦当劳推荐] LLM 返回内容为空，使用降级推荐")
            return self._get_fallback_recommendation(user_message, meals_data)

        except Exception as e:
            logger.error(f"[麦当劳推荐] LLM 生成推荐失败: {e}", exc_info=True)
            # 降级：返回基础推荐
            return self._get_fallback_recommendation(user_message, meals_data)

    def _get_fallback_recommendation(self, user_message: str, meals_data: Dict[str, Any]) -> str:
        """降级推荐方案"""
        recommendations = [
            "🍔 巨无霸 - 经典之选，双层牛肉饼配上特制酱料",
            "🍟 麦辣鸡腿堡 - 香辣酥脆，回味无穷",
            "🥤 可乐 + 薯条 - 黄金组合，永远的神",
            "🍦 麦旋风 - 甜蜜收尾，快乐加倍",
            "🥪 板烧鸡腿堡 - 鲜嫩多汁，健康之选"
        ]

        # 显示降级原因
        error_reason = meals_data.get("error", "未知原因")
        max_rec = self.config.get('max_recommendations', 5)

        return f"""🍟 为你推荐以下麦当劳美食：

{chr(10).join(recommendations[:max_rec])}

💡 小贴士：搭配优惠券更划算哦！
⚠️ [降级模式] MCP 实时数据获取失败: {error_reason}
以上为通用推荐，具体以门店实际供应为准。"""

    def _extract_city(self, message: str) -> Optional[str]:
        """从消息中提取城市名"""
        city_pattern = r"(北京|上海|广州|深圳|杭州|南京|成都|武汉|西安|重庆|天津|苏州|长沙|郑州|青岛|大连|厦门|福州|济南|合肥|南昌|南宁|昆明|贵阳|拉萨|乌鲁木齐|兰州|西宁|银川|海口|三亚|石家庄|太原|沈阳|长春|哈尔滨|呼和浩特|宁波|无锡|常州|温州|佛山|东莞|珠海|中山|泉州|烟台|潍坊|徐州|南通|扬州|镇江|泰州|盐城|淮安|连云港|宿迁|湖州|嘉兴|绍兴|金华|台州|芜湖|蚌埠|马鞍山|安庆|黄山|滁州|阜阳|宿州|六安|亳州|池州|宣城|漳州|龙岩|三明|南平|宁德|九江|赣州|上饶|宜春|吉安|抚州|景德镇|萍乡|新余|鹰潭|株洲|湘潭|衡阳|岳阳|常德|张家界|益阳|郴州|永州|怀化|娄底|邵阳|襄樊|宜昌|黄石|十堰|孝感|荆门|鄂州|黄冈|咸宁|随州|荆州|安阳|新乡|焦作|鹤壁|濮阳|许昌|漯河|三门峡|南阳|商丘|信阳|周口|驻马店|平顶山|开封|洛阳|平顶山|邯郸|邢台|保定|张家口|承德|唐山|秦皇岛|沧州|廊坊|衡水|大同|阳泉|长治|晋城|朔州|晋中|运城|忻州|临汾|吕梁|包头|乌海|赤峰|通辽|鄂尔多斯|呼伦贝尔|巴彦淖尔|乌兰察布|鞍山|抚顺|本溪|丹东|锦州|营口|阜新|辽阳|盘锦|铁岭|朝阳|葫芦岛|吉林|四平|辽源|通化|白山|松原|白城|齐齐哈尔|鸡西|鹤岗|双鸭山|大庆|伊春|佳木斯|七台河|牡丹江|黑河|绥化|齐齐哈尔|桂林|柳州|梧州|北海|防城港|钦州|贵港|玉林|百色|贺州|河池|来宾|崇左|遵义|安顺|六盘水|毕节|铜仁|玉溪|曲靖|保山|昭通|丽江|普洱|临沧|白银|天水|嘉峪关|金昌|武威|张掖|平凉|酒泉|庆阳|定西|陇南|石嘴山|吴忠|固原|中卫|克拉玛依|吐鲁番|哈密)市?"
        city_match = re.search(city_pattern, message)
        if city_match:
            return city_match.group(1) + "市"
        return None

    async def _do_recommendation(self, message: str, event: AstrMessageEvent):
        """执行推荐流程"""
        # 解析用户提到的城市
        city = self.config.get("default_city", "北京市")
        extracted_city = self._extract_city(message)
        if extracted_city:
            city = extracted_city

        logger.info(f"[麦当劳推荐] 开始处理，城市={city}, MCP客户端={'已配置' if self.mcp_client else '未配置'}")

        # 获取餐品数据
        meals_data = await self._get_meals_data(city)

        if meals_data.get("error"):
            logger.warning(f"[麦当劳推荐] MCP 数据获取失败: {meals_data['error']}")
        else:
            store = meals_data.get('store') or {}
            meals = meals_data.get('meals') or {}
            logger.info(f"[麦当劳推荐] MCP 数据获取成功: 门店={store.get('storeName', '无')}, 餐品数={len(meals)}")

        # 生成推荐
        recommendation = await self._generate_recommendation(message, meals_data, event)

        return recommendation

    @filter.command("mcd", alias={'麦当劳', '麦麦', 'm记'})
    async def mcd_command(self, event: AstrMessageEvent, args: str = ""):
        """
        麦当劳美食推荐

        用法: /mcd [需求描述]
        示例: /mcd 今天午饭吃什么
              /mcd 推荐一个减脂套餐
              /mcd 上海有什么好吃的
              /mcd test  - 测试配置和连接状态
        """
        user_message = args if args else event.message_str

        # 诊断指令
        if user_message.strip().lower() in ("test", "测试", "status", "状态"):
            diag = await self._run_diagnostics()
            yield event.plain_result(diag)
            event.stop_event()
            return

        logger.info("[麦当劳推荐] 收到 /mcd 指令，触发美食推荐")

        recommendation = await self._do_recommendation(user_message, event)
        yield event.plain_result(recommendation)
        event.stop_event()

    async def _run_diagnostics(self) -> str:
        """运行诊断，返回诊断结果文本"""
        lines = ["🔍 麦当劳推荐插件诊断", "=" * 30]

        # 1. 检查配置
        mcp_token = self.config.get("mcp_token", "")
        keywords = self.config.get("trigger_keywords", "（默认）")
        city = self.config.get("default_city", "（默认）")
        style = self.config.get("recommendation_style", "（默认）")

        lines.append(f"📋 配置状态:")
        lines.append(f"  Token: {'✅ 已配置(' + mcp_token[:8] + '...)' if mcp_token else '❌ 未配置!'}")
        lines.append(f"  Token完整值: '{mcp_token}'")
        lines.append(f"  关键词: {keywords}")
        lines.append(f"  默认城市: {city}")
        lines.append(f"  推荐风格: {style}")
        lines.append(f"  MCP客户端: {'✅ 已创建' if self.mcp_client else '❌ 未创建'}")
        lines.append("")

        # 2. 测试 MCP 连接
        if not mcp_token:
            lines.append("❌ MCP Token 未配置，无法测试连接")
            lines.append("")
            lines.append("💡 请在管理面板填入 Token:")
            lines.append("   插件管理 → 麦当劳美食推荐 → 配置 → MCP Token")
            return "\n".join(lines)

        if not self.mcp_client:
            lines.append("❌ MCP 客户端未创建（可能插件未正确加载配置）")
            return "\n".join(lines)

        # 3. 测试 initialize
        lines.append("🔌 测试 MCP 连接...")
        try:
            init_ok = await self.mcp_client.ensure_initialized()
            if init_ok:
                lines.append("  ✅ MCP 初始化成功!")
            else:
                lines.append("  ❌ MCP 初始化失败!")
                return "\n".join(lines)
        except Exception as e:
            lines.append(f"  ❌ MCP 初始化异常: {e}")
            return "\n".join(lines)

        # 4. 测试 now-time-info
        lines.append("")
        lines.append("🛠 测试调用 now-time-info...")
        try:
            resp = await self.mcp_client.call_tool("now-time-info", {})
            if resp and not (isinstance(resp, dict) and resp.get("error")):
                data = self._parse_mcp_response(resp)
                if data:
                    lines.append(f"  ✅ 调用成功! 返回数据类型: {type(data).__name__}")
                    if isinstance(data, dict):
                        lines.append(f"  📅 当前时间: {data.get('formatted', str(data)[:100])}")
                else:
                    lines.append(f"  ⚠️ 调用返回但解析失败，原始: {str(resp)[:200]}")
            else:
                lines.append(f"  ❌ 调用失败: {resp}")
        except Exception as e:
            lines.append(f"  ❌ 调用异常: {e}")

        # 5. 测试查门店
        lines.append("")
        lines.append("🏪 测试查询门店...")
        try:
            resp = await self.mcp_client.call_tool("query-nearby-stores", {
                "searchType": 2,
                "beType": 1,
                "city": "北京市",
                "keyword": "王府井"
            })
            if resp and not (isinstance(resp, dict) and resp.get("error")):
                data = self._parse_mcp_response(resp)
                if data and isinstance(data, list) and len(data) > 0:
                    store = data[0]
                    lines.append(f"  ✅ 查到 {len(data)} 家门店")
                    lines.append(f"  📍 第1家: {store.get('storeName', '?')} (code: {store.get('storeCode', '?')})")
                else:
                    lines.append(f"  ⚠️ 解析失败: {str(data)[:200]}")
            else:
                lines.append(f"  ❌ 查询失败: {resp}")
        except Exception as e:
            lines.append(f"  ❌ 查询异常: {e}")

        lines.append("")
        lines.append("✅ 诊断完成!")

        return "\n".join(lines)

    @filter.regex(r".+")
    async def on_message(self, event: AstrMessageEvent):
        """监听所有消息，检测关键词"""
        message = event.message_str

        # 跳过指令消息（以 / 开头的消息）
        if message.startswith("/") or message.startswith("\\"):
            return

        # 检测关键词
        matched_keyword = self._match_keyword(message)
        if not matched_keyword:
            return  # 未匹配到关键词，不处理

        logger.info(f"[麦当劳推荐] 检测到关键词 '{matched_keyword}'，触发美食推荐")

        recommendation = await self._do_recommendation(message, event)
        yield event.plain_result(recommendation)
        event.stop_event()

    async def initialize(self):
        """插件激活时调用"""
        logger.info("麦当劳美食推荐插件已激活")
        if self.mcp_client:
            await self.mcp_client.initialize()

    async def terminate(self):
        """插件禁用时调用"""
        logger.info("麦当劳美食推荐插件已禁用")
        if self.mcp_client:
            await self.mcp_client.close()
