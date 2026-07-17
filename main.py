"""
麦当劳美食推荐插件
检测关键词后调用 LLM 智能推荐麦当劳美食
"""

import re
import json
import os
import random
from typing import Optional, List, Dict, Any

from astrbot.api.star import Star, Context
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger  # 使用 astrbot 提供的 logger 接口

from .mcp_client import McdMCPClient

# 卡片 HTML 模板路径
CARD_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "card_template.html")
COUPON_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "coupon_template.html")

# 意图关键词组（用于自然语言意图识别）
INTENT_COUPON_KEYWORDS = ["优惠券", "优惠", "折扣", "打折", "券", "省钱", "划算", "便宜"]
INTENT_STORE_KEYWORDS = ["门店", "附近", "哪里有", "离我近", "地址", "在哪个", "几家"]
INTENT_ACTIVITY_KEYWORDS = ["活动", "促销", "新品", "上新", "限定", "日历", "有什么活动"]
INTENT_NUTRITION_KEYWORDS = ["营养", "热量", "卡路里", "减肥", "减脂", "低卡", "蛋白质"]


class Main(Star):
    """麦当劳美食推荐插件主类"""

    def __init__(self, context: Context, config: Optional[dict] = None):
        super().__init__(context)
        self.config = config or {}
        self.mcp_client: Optional[McdMCPClient] = None

        # 打印配置信息方便排查
        mcp_token = self.config.get("mcp_token", "")
        keywords = self.config.get("trigger_keywords", "吃什么,麦当劳,麦麦,今天吃啥,午饭,晚饭,早餐,夜宵")
        logger.info(f"[麦当劳推荐] 插件加载中... 版本: 1.1.3")
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

    def _detect_intent(self, message: str) -> str:
        """检测用户消息的意图

        Returns:
            意图类型: "coupon" | "store" | "activity" | "nutrition" | None
        """
        msg_lower = message.lower().strip()

        # 优先级：优惠券 > 门店 > 活动 > 营养 > None
        for kw in INTENT_COUPON_KEYWORDS:
            if kw in msg_lower:
                return "coupon"
        for kw in INTENT_STORE_KEYWORDS:
            if kw in msg_lower:
                return "store"
        for kw in INTENT_ACTIVITY_KEYWORDS:
            if kw in msg_lower:
                return "activity"
        for kw in INTENT_NUTRITION_KEYWORDS:
            if kw in msg_lower:
                return "nutrition"

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
                # MCP API 要求 searchType=2 时 city 和 keyword 都必填
                # 去掉"市"后缀（MCP 可能不认"上海市"，只认"上海"）
                city_for_mcp = city.rstrip("市") if city.endswith("市") else city
                # 如果没有提取到关键词，用城市名当关键词（保证两者都非空）
                keyword_for_mcp = keyword or city_for_mcp
                logger.debug(f"查询 {city_for_mcp} 附近的门店，关键词={keyword_for_mcp}...")

                stores_resp = await self.mcp_client.query_nearby_stores(
                    city=city_for_mcp,
                    keyword=keyword_for_mcp,
                    be_type=be_type
                )
                stores_data = self._parse_mcp_response(stores_resp)

                # 重试: 如果没查到，尝试带"市"后缀重试（MCP 可能需要"上海市"格式）
                if (not stores_data or not isinstance(stores_data, list) or len(stores_data) == 0) and city_for_mcp:
                    city_with_suffix = city_for_mcp + "市"
                    logger.debug(f"带市后缀重试: city={city_with_suffix}, keyword={keyword_for_mcp}")
                    stores_resp = await self.mcp_client.query_nearby_stores(
                        city=city_with_suffix,
                        keyword=keyword_for_mcp,
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
            parts.append("【当前可售餐品（随机抽样）】")
            meals = meals_data["meals"]
            # 将字典转为列表，随机打乱后取 25 款，避免每次推荐重复
            meal_items = list(meals.items())
            random.shuffle(meal_items)
            sample_count = min(25, len(meal_items))
            for code, meal in meal_items[:sample_count]:
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
    ) -> tuple:
        """
        使用 LLM 生成美食推荐

        Returns:
            (image_url_or_path, text_fallback): 图片URL/路径, 纯文本兜底
        """
        style_prompt = self._get_recommendation_style_prompt()
        max_rec = self.config.get("max_recommendations", 5)
        meals_context = self._format_meals_for_prompt(meals_data)

        system_prompt = f"""你是一个专业的麦当劳美食推荐助手。{style_prompt}。

请根据用户的需求，从麦当劳餐品中推荐最合适的美食。

**每次推荐都要有新鲜感！** 请从提供的餐品列表中随机选择不同种类的餐品组合，避免每次都推荐相同的经典款。尝试推荐一些用户可能忽略的好味道，包括但不限于：限定新品、不同口味变体、搭配套餐、小食拼盘、甜品饮品等。

**你必须以 JSON 格式回复，不要输出任何其他内容。** JSON 格式如下：
```json
{{
  "recommendations": [
    {{
      "name": "餐品名称",
      "emoji": "🍔",
      "price": "¥XX",
      "reason": "推荐理由（一句话）"
    }}
  ],
  "blessing": "一句温馨的祝福语"
}}
```

要求：
1. 推荐 {max_rec} 款以内的餐品，尽量涵盖不同品类（主食/小食/饮品/甜品）
2. 每款推荐要有 emoji 表情和推荐理由
3. 如果用户有特殊需求（如减脂、早餐、套餐等），请针对性推荐
4. blessing 是一句简短的祝福语，每次措辞不同
5. 只输出 JSON，不要输出 ```json 标记

以下是当前可用的餐品信息（已随机抽样）：
{meals_context}

注意：如果餐品信息不完整，请基于你对麦当劳的了解进行推荐。"""

        user_prompt = f"用户说：{user_message}\n\n请为用户推荐合适的麦当劳美食，以 JSON 格式回复。"

        try:
            # 获取 LLM Provider ID
            llm_provider_id = self.config.get("llm_provider_id", "")
            if not llm_provider_id:
                try:
                    providers = self.context.get_all_providers()
                    if providers:
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
                return None, self._get_fallback_recommendation(user_message, meals_data)

            logger.info(f"[麦当劳推荐] 调用 LLM 生成推荐 (provider: {llm_provider_id})...")

            resp = await self.context.llm_generate(
                chat_provider_id=llm_provider_id,
                prompt=user_prompt,
                system_prompt=system_prompt,
            )

            if resp is None:
                logger.warning("[麦当劳推荐] LLM 返回 None")
                return None, self._get_fallback_recommendation(user_message, meals_data)

            # 获取 LLM 返回文本
            llm_text = ""
            if hasattr(resp, 'completion_text'):
                llm_text = resp.completion_text or ""
            elif hasattr(resp, 'result_chain') and resp.result_chain:
                texts = []
                for item in resp.result_chain:
                    if hasattr(item, 'text'):
                        texts.append(item.text)
                    elif isinstance(item, str):
                        texts.append(item)
                llm_text = "\n".join(texts)

            if not llm_text:
                logger.warning("[麦当劳推荐] LLM 返回内容为空")
                return None, self._get_fallback_recommendation(user_message, meals_data)

            logger.info(f"[麦当劳推荐] LLM 返回: {llm_text[:200]}...")

            # 解析 JSON
            rec_data = self._parse_llm_json(llm_text)
            if not rec_data:
                logger.warning("[麦当劳推荐] JSON 解析失败，使用纯文本")
                return None, llm_text

            # 用 HTML 模板渲染成图片
            image_url = await self._render_card(rec_data, meals_data)
            if image_url:
                logger.info("[麦当劳推荐] HTML 渲染成功!")
                return image_url, llm_text
            else:
                logger.warning("[麦当劳推荐] HTML 渲染失败，使用纯文本")
                return None, self._format_text_from_json(rec_data, meals_data)

        except Exception as e:
            logger.error(f"[麦当劳推荐] LLM 生成推荐失败: {e}", exc_info=True)
            return None, self._get_fallback_recommendation(user_message, meals_data)

    def _parse_llm_json(self, text: str) -> Optional[Dict[str, Any]]:
        """从 LLM 返回文本中解析 JSON"""
        if not text:
            return None

        # 清理 markdown 代码块标记
        text = text.strip()
        if text.startswith("```"):
            # 去掉 ```json 或 ``` 标记
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试提取 { 到 } 之间的内容
        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last > first:
            try:
                return json.loads(text[first:last + 1])
            except json.JSONDecodeError:
                pass

        return None

    async def _render_card(self, rec_data: Dict[str, Any], meals_data: Dict[str, Any]) -> Optional[str]:
        """用 HTML 模板渲染推荐卡片为图片"""
        try:
            # 读取 HTML 模板
            with open(CARD_TEMPLATE_PATH, "r", encoding="utf-8") as f:
                tmpl = f.read()

            # 准备渲染数据
            store = meals_data.get("store") or {}
            coupons = meals_data.get("coupons") or []
            nutrition = meals_data.get("nutrition")

            # 构造优惠券列表
            coupon_texts = []
            for c in coupons[:4]:
                if isinstance(c, dict):
                    coupon_texts.append(c.get("title", ""))
                else:
                    coupon_texts.append(str(c))

            # 解析营养信息，匹配推荐餐品
            nutrition_items = self._match_nutrition_for_recommendations(
                str(nutrition) if nutrition else "",
                rec_data.get("recommendations", []),
            )

            data = {
                "store_name": store.get("storeName", ""),
                "store_address": store.get("address", ""),
                "recommendations": rec_data.get("recommendations", []),
                "coupons": coupon_texts if coupon_texts else None,
                "nutrition_items": nutrition_items if nutrition_items else None,
                "blessing": rec_data.get("blessing", "祝您用餐愉快！"),
            }

            # 调用 html_render 渲染
            # 使用 PNG 格式 + 高质量，覆盖默认的 jpeg quality=40
            render_options = {
                "type": "png",
                "quality": 100,
                "full_page": True,
            }
            url = await self.html_render(
                tmpl, data, return_url=True, options=render_options
            )
            return url

        except Exception as e:
            logger.error(f"[麦当劳推荐] HTML 渲染异常: {e}", exc_info=True)
            return None

    def _parse_nutrition_data(self, nutrition_str: str) -> List[Dict[str, str]]:
        """解析营养信息 CSV 字符串为结构化列表

        数据格式示例:
        (productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium):
        猪柳麦满分,null,1288,308,16,16,24,781,213 猪柳蛋麦满分,null,1618,387,...
        """
        if not nutrition_str:
            return []

        # 每条记录格式: 名称,null,energyKj,energyKcal,protein,fat,carb,sodium,calcium
        # 用正则匹配：名称(不含逗号空格),null,后面跟8个数字
        pattern = r'([^,\s]+),null,(\d+),(\d+),(\d+),(\d+),(\d+),(\d+),(\d+)'
        matches = re.findall(pattern, nutrition_str)

        result = []
        for m in matches:
            result.append({
                "name": m[0],
                "energy_kcal": m[2],  # energyKcal
                "protein": m[3],
                "fat": m[4],
                "carb": m[5],
            })
        return result

    def _match_nutrition_for_recommendations(
        self,
        nutrition_str: str,
        recommendations: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        """从营养数据中匹配推荐餐品的营养信息

        优先匹配推荐餐品；匹配不到则返回前3条作为参考。
        """
        all_nutrition = self._parse_nutrition_data(nutrition_str)
        if not all_nutrition:
            return []

        matched = []
        for rec in recommendations:
            rec_name = rec.get("name", "")
            for nut in all_nutrition:
                nut_name = nut["name"]
                # 双向模糊匹配：推荐餐品名包含营养表餐品名，或反过来
                if rec_name and nut_name and (
                    rec_name in nut_name or nut_name in rec_name
                ):
                    matched.append(nut)
                    break

        # 如果一条都没匹配到，返回前3条作为参考
        if not matched:
            matched = all_nutrition[:3]

        return matched[:5]  # 最多5条

    def _format_text_from_json(self, rec_data: Dict[str, Any], meals_data: Dict[str, Any]) -> str:
        """从 JSON 数据格式化纯文本（渲染失败时的兜底）"""
        lines = ["🍟 为你推荐以下麦当劳美食：", ""]

        for item in rec_data.get("recommendations", []):
            emoji = item.get("emoji", "🍔")
            name = item.get("name", "未知")
            price = item.get("price", "")
            reason = item.get("reason", "")
            line = f"{emoji} {name}"
            if price:
                line += f" {price}"
            lines.append(line)
            if reason:
                lines.append(f"   {reason}")

        blessing = rec_data.get("blessing", "")
        if blessing:
            lines.append("")
            lines.append(f"✨ {blessing}")

        store = meals_data.get("store") or {}
        if store.get("storeName"):
            lines.append("")
            lines.append(f"📍 {store['storeName']}")

        return "\n".join(lines)

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

    # ========== 意图处理方法 ==========

    async def _handle_coupon_intent(
        self, message: str, event: AstrMessageEvent
    ) -> tuple:
        """处理优惠券查询意图

        Returns:
            (image_url_or_path, text_fallback)
        """
        logger.info("[麦当劳推荐] 意图: 查询优惠券")

        city = self._extract_city(message) or self.config.get("default_city", "北京市")
        keyword = self._extract_keyword(message)
        meals_data = await self._get_meals_data(city, keyword)

        coupons = meals_data.get("coupons") or []
        if not coupons:
            text = "🎫 抱歉，当前没有查询到可用的优惠券信息，请稍后再试~"
            return None, text

        # 用 LLM 整理优惠券信息并生成祝福语
        coupon_summary = self._format_coupons_for_prompt(coupons)
        style_prompt = self._get_recommendation_style_prompt()

        system_prompt = f"""你是一个麦当劳优惠券助手。{style_prompt}。

根据提供的优惠券数据，生成一段简短的优惠券整理，并配一句祝福语。

**你必须以 JSON 格式回复，不要输出任何其他内容。** JSON 格式如下：
```json
{{
  "coupons": [
    {{
      "title": "优惠券标题",
      "discount": "优惠金额/折扣",
      "description": "简短描述（一句话）",
      "tag": "标签（如：限时/热门/超值，可选）"
    }}
  ],
  "blessing": "一句关于省钱/优惠的祝福语"
}}
```

要求：
1. 整理 {min(8, len(coupons))} 张以内的优惠券
2. 从原始数据中提取或推断优惠金额
3. blessing 每次措辞不同，和省钱/优惠相关
4. 只输出 JSON，不要输出 ```json 标记

优惠券原始数据：
{coupon_summary}"""

        user_prompt = f"用户说：{message}\n\n请整理当前的麦当劳优惠券信息，以 JSON 格式回复。"

        try:
            llm_provider_id = await self._get_llm_provider_id()
            if not llm_provider_id:
                return None, "❌ 没有可用的 LLM Provider，无法生成优惠券整理。"

            resp = await self.context.llm_generate(
                chat_provider_id=llm_provider_id,
                prompt=user_prompt,
                system_prompt=system_prompt,
            )

            llm_text = self._get_llm_text(resp)
            if not llm_text:
                return None, "❌ LLM 返回内容为空"

            rec_data = self._extract_json_from_text(llm_text)
            if not rec_data:
                return None, "❌ 无法解析 LLM 返回的 JSON"

            # 渲染优惠券卡片
            image_url = await self._render_coupon_card(rec_data, meals_data)
            text_fallback = self._format_coupon_text(rec_data, meals_data)
            return image_url, text_fallback

        except Exception as e:
            logger.error(f"[麦当劳推荐] 优惠券处理异常: {e}", exc_info=True)
            text = self._format_coupon_text_fallback(coupons)
            return None, text

    async def _handle_store_intent(
        self, message: str, event: AstrMessageEvent
    ) -> tuple:
        """处理门店查询意图

        Returns:
            (image_url_or_path, text_fallback)
        """
        logger.info("[麦当劳推荐] 意图: 查询门店")

        city = self._extract_city(message) or self.config.get("default_city", "北京市")
        keyword = self._extract_keyword(message)

        if not self.mcp_client:
            return None, "❌ 未配置麦当劳 MCP Token，无法查询门店"

        try:
            init_ok = await self.mcp_client.ensure_initialized()
            if not init_ok:
                return None, "❌ MCP 初始化失败"

            _, be_type = self._get_order_type_config()
            city_for_mcp = city.rstrip("市") if city.endswith("市") else city
            stores_resp = await self.mcp_client.query_nearby_stores(
                city=city_for_mcp, keyword=keyword, be_type=be_type
            )
            stores_data = self._parse_mcp_response(stores_resp)

            # 重试：用城市名当关键词
            if (not stores_data or not isinstance(stores_data, list) or len(stores_data) == 0) and not keyword:
                stores_resp = await self.mcp_client.query_nearby_stores(
                    city="", keyword=city_for_mcp, be_type=be_type
                )
                stores_data = self._parse_mcp_response(stores_resp)

            if not stores_data or not isinstance(stores_data, list):
                return None, f"抱歉，未找到 {city} 的麦当劳门店信息~"

            # 用 LLM 生成门店介绍
            store_list = self._format_stores_for_prompt(stores_data[:8])
            style_prompt = self._get_recommendation_style_prompt()

            system_prompt = f"""你是一个麦当劳门店查询助手。{style_prompt}。

根据提供的门店数据，整理出门店列表并生成简短介绍。

**你必须以 JSON 格式回复，不要输出任何其他内容。** JSON 格式如下：
```json
{{
  "city": "城市名",
  "stores": [
    {{
      "name": "门店名称",
      "address": "门店地址",
      "distance": "距离信息（如有）",
      "business_hours": "营业时间（如有）"
    }}
  ],
  "blessing": "一句关于探店的祝福语"
}}
```

要求：
1. 整理 5 家以内的门店
2. blessing 每次措辞不同
3. 只输出 JSON，不要输出 ```json 标记

门店原始数据：
{store_list}"""

            user_prompt = f"用户说：{message}\n\n请整理 {city} 的麦当劳门店信息，以 JSON 格式回复。"

            llm_provider_id = await self._get_llm_provider_id()
            if not llm_provider_id:
                return None, self._format_stores_text(stores_data[:5], city)

            resp = await self.context.llm_generate(
                chat_provider_id=llm_provider_id,
                prompt=user_prompt,
                system_prompt=system_prompt,
            )

            llm_text = self._get_llm_text(resp)
            if not llm_text:
                return None, self._format_stores_text(stores_data[:5], city)

            rec_data = self._extract_json_from_text(llm_text)
            if not rec_data:
                return None, self._format_stores_text(stores_data[:5], city)

            text = self._format_stores_from_json(rec_data)
            return None, text

        except Exception as e:
            logger.error(f"[麦当劳推荐] 门店查询异常: {e}", exc_info=True)
            return None, f"❌ 门店查询失败: {e}"

    async def _handle_activity_intent(
        self, message: str, event: AstrMessageEvent
    ) -> tuple:
        """处理活动查询意图

        Returns:
            (image_url_or_path, text_fallback)
        """
        logger.info("[麦当劳推荐] 意图: 查询活动")

        if not self.mcp_client:
            return None, "❌ 未配置麦当劳 MCP Token，无法查询活动"

        try:
            init_ok = await self.mcp_client.ensure_initialized()
            if not init_ok:
                return None, "❌ MCP 初始化失败"

            # 获取活动日历
            campaign_resp = await self.mcp_client.get_campaign_calendar()
            campaign_data = self._parse_mcp_response(campaign_resp)

            if not campaign_data:
                return None, "📅 抱歉，当前没有查询到活动信息~"

            campaign_text = str(campaign_data)[:1500]

            # 获取当前可售餐品（用于关联活动）
            city = self._extract_city(message) or self.config.get("default_city", "北京市")
            meals_data = await self._get_meals_data(city)

            style_prompt = self._get_recommendation_style_prompt()

            system_prompt = f"""你是一个麦当劳活动信息助手。{style_prompt}。

根据提供的活动日历数据，整理出当前的活动信息。

**你必须以 JSON 格式回复，不要输出任何其他内容。** JSON 格式如下：
```json
{{
  "activities": [
    {{
      "title": "活动名称",
      "description": "活动描述",
      "tag": "标签（如：限时/新品/优惠，可选）"
    }}
  ],
  "blessing": "一句关于活动的祝福语"
}}
```

要求：
1. 整理 5 条以内的活动
2. blessing 每次措辞不同
3. 只输出 JSON，不要输出 ```json 标记

活动日历原始数据：
{campaign_text}"""

            user_prompt = f"用户说：{message}\n\n请整理当前的麦当劳活动信息，以 JSON 格式回复。"

            llm_provider_id = await self._get_llm_provider_id()
            if not llm_provider_id:
                return None, f"📅 麦当劳活动日历：\n\n{campaign_text[:800]}"

            resp = await self.context.llm_generate(
                chat_provider_id=llm_provider_id,
                prompt=user_prompt,
                system_prompt=system_prompt,
            )

            llm_text = self._get_llm_text(resp)
            if not llm_text:
                return None, f"📅 麦当劳活动日历：\n\n{campaign_text[:800]}"

            rec_data = self._extract_json_from_text(llm_text)
            if not rec_data:
                return None, f"📅 麦当劳活动日历：\n\n{campaign_text[:800]}"

            text = self._format_activity_text(rec_data)
            return None, text

        except Exception as e:
            logger.error(f"[麦当劳推荐] 活动查询异常: {e}", exc_info=True)
            return None, f"❌ 活动查询失败: {e}"

    async def _handle_nutrition_intent(
        self, message: str, event: AstrMessageEvent
    ) -> tuple:
        """处理营养查询意图

        Returns:
            (image_url_or_path, text_fallback)
        """
        logger.info("[麦当劳推荐] 意图: 查询营养信息")

        if not self.mcp_client:
            return None, "❌ 未配置麦当劳 MCP Token，无法查询营养信息"

        try:
            init_ok = await self.mcp_client.ensure_initialized()
            if not init_ok:
                return None, "❌ MCP 初始化失败"

            nutrition_resp = await self.mcp_client.get_nutrition_foods()
            nutrition_data = self._parse_mcp_response(nutrition_resp)

            if not nutrition_data:
                return None, "📊 抱歉，当前没有查询到营养信息~"

            nutrition_str = str(nutrition_data)[:2000]
            style_prompt = self._get_recommendation_style_prompt()

            system_prompt = f"""你是一个麦当劳营养信息助手。{style_prompt}。

根据用户的需求（如减脂、低卡、高蛋白等），从营养数据中推荐合适的餐品。

**你必须以 JSON 格式回复，不要输出任何其他内容。** JSON 格式如下：
```json
{{
  "recommendations": [
    {{
      "name": "餐品名称",
      "emoji": "🍔",
      "energy": "308kcal",
      "protein": "16g",
      "reason": "推荐理由（结合营养数据）"
    }}
  ],
  "blessing": "一句关于健康饮食的祝福语"
}}
```

要求：
1. 推荐 5 款以内的餐品，优先满足用户营养需求
2. 每款标明热量和蛋白质
3. blessing 每次措辞不同，和健康饮食相关
4. 只输出 JSON，不要输出 ```json 标记

营养原始数据：
{nutrition_str}"""

            user_prompt = f"用户说：{message}\n\n请根据营养数据为用户推荐合适的餐品，以 JSON 格式回复。"

            llm_provider_id = await self._get_llm_provider_id()
            if not llm_provider_id:
                return None, "❌ 没有可用的 LLM Provider"

            resp = await self.context.llm_generate(
                chat_provider_id=llm_provider_id,
                prompt=user_prompt,
                system_prompt=system_prompt,
            )

            llm_text = self._get_llm_text(resp)
            if not llm_text:
                return None, "❌ LLM 返回内容为空"

            rec_data = self._extract_json_from_text(llm_text)
            if not rec_data:
                return None, "❌ 无法解析 LLM 返回的 JSON"

            # 复用推荐卡片模板渲染
            meals_data = {"nutrition": nutrition_str, "store": {}}
            image_url = await self._render_card(rec_data, meals_data)
            text_fallback = self._format_text_from_json(rec_data, meals_data)
            return image_url, text_fallback

        except Exception as e:
            logger.error(f"[麦当劳推荐] 营养查询异常: {e}", exc_info=True)
            return None, f"❌ 营养查询失败: {e}"

    # ========== 辅助方法 ==========

    async def _get_llm_provider_id(self) -> str:
        """获取 LLM Provider ID（配置或自动检测）"""
        llm_provider_id = self.config.get("llm_provider_id", "")
        if llm_provider_id:
            return llm_provider_id

        try:
            providers = self.context.get_all_providers()
            if providers:
                for p in providers:
                    if hasattr(p, "id"):
                        return p.id
                return providers[0].id
        except Exception as e:
            logger.warning(f"[麦当劳推荐] 获取 Provider 列表失败: {e}")

        return ""

    def _get_llm_text(self, resp) -> str:
        """从 LLMResponse 提取文本"""
        if resp is None:
            return ""
        if hasattr(resp, "completion_text"):
            return resp.completion_text or ""
        if hasattr(resp, "result_chain") and resp.result_chain:
            texts = []
            for item in resp.result_chain:
                if hasattr(item, "text"):
                    texts.append(item.text)
                elif isinstance(item, str):
                    texts.append(item)
            return "\n".join(texts)
        return ""

    def _format_coupons_for_prompt(self, coupons: list) -> str:
        """格式化优惠券数据为提示词"""
        parts = []
        for i, c in enumerate(coupons[:10], 1):
            if isinstance(c, dict):
                title = c.get("title", "未知")
                parts.append(f"{i}. {title}")
                # 尝试提取更多字段
                for key in ("content", "description", "discountAmount", "discountType", "useCondition"):
                    val = c.get(key)
                    if val:
                        parts.append(f"   {key}: {val}")
            else:
                parts.append(f"{i}. {c}")
        return "\n".join(parts)

    def _format_stores_for_prompt(self, stores: list) -> str:
        """格式化门店数据为提示词"""
        parts = []
        for i, s in enumerate(stores, 1):
            if isinstance(s, dict):
                name = s.get("storeName", "未知")
                addr = s.get("address", "未知")
                parts.append(f"{i}. 门店: {name}")
                parts.append(f"   地址: {addr}")
                for key in ("distance", "businessHours", "phone", "longitude", "latitude"):
                    val = s.get(key)
                    if val:
                        parts.append(f"   {key}: {val}")
            else:
                parts.append(f"{i}. {s}")
        return "\n".join(parts)

    async def _render_coupon_card(
        self, rec_data: Dict[str, Any], meals_data: Dict[str, Any]
    ) -> Optional[str]:
        """渲染优惠券卡片为图片"""
        try:
            with open(COUPON_TEMPLATE_PATH, "r", encoding="utf-8") as f:
                tmpl = f.read()

            store = meals_data.get("store") or {}
            coupons = rec_data.get("coupons", [])

            data = {
                "store_name": store.get("storeName", ""),
                "store_address": store.get("address", ""),
                "coupons": coupons,
                "blessing": rec_data.get("blessing", "省钱快乐！"),
            }

            render_options = {
                "type": "png",
                "quality": 100,
                "full_page": True,
            }
            url = await self.html_render(
                tmpl, data, return_url=True, options=render_options
            )
            return url
        except Exception as e:
            logger.error(f"[麦当劳推荐] 优惠券卡片渲染异常: {e}", exc_info=True)
            return None

    def _format_coupon_text(self, rec_data: Dict[str, Any], meals_data: Dict[str, Any]) -> str:
        """格式化优惠券为纯文本"""
        lines = ["🎫 麦当劳当前优惠券：", ""]

        for item in rec_data.get("coupons", []):
            title = item.get("title", "未知")
            discount = item.get("discount", "")
            desc = item.get("description", "")
            tag = item.get("tag", "")

            line = f"🎫 {title}"
            if discount:
                line += f" 【{discount}】"
            lines.append(line)
            if desc:
                lines.append(f"   {desc}")
            if tag:
                lines.append(f"   📌 {tag}")
            lines.append("")

        blessing = rec_data.get("blessing", "")
        if blessing:
            lines.append(f"✨ {blessing}")

        return "\n".join(lines)

    def _format_coupon_text_fallback(self, coupons: list) -> str:
        """优惠券文本兜底（LLM 不可用时）"""
        lines = ["🎫 麦当劳当前优惠券：", ""]
        for i, c in enumerate(coupons[:8], 1):
            if isinstance(c, dict):
                title = c.get("title", "未知")
                lines.append(f"{i}. {title}")
            else:
                lines.append(f"{i}. {c}")
        lines.append("")
        lines.append("💡 优惠券以门店实际为准")
        return "\n".join(lines)

    def _format_stores_text(self, stores: list, city: str) -> str:
        """格式化门店为纯文本"""
        lines = [f"📍 {city} 麦当劳门店：", ""]
        for i, s in enumerate(stores[:5], 1):
            if isinstance(s, dict):
                name = s.get("storeName", "未知")
                addr = s.get("address", "地址未知")
                lines.append(f"{i}. {name}")
                lines.append(f"   📍 {addr}")
            else:
                lines.append(f"{i}. {s}")
        return "\n".join(lines)

    def _format_stores_from_json(self, rec_data: Dict[str, Any]) -> str:
        """从 LLM JSON 格式化门店文本"""
        city = rec_data.get("city", "")
        lines = [f"📍 {city} 麦当劳门店：", ""]

        for i, item in enumerate(rec_data.get("stores", []), 1):
            name = item.get("name", "未知")
            addr = item.get("address", "地址未知")
            distance = item.get("distance", "")
            hours = item.get("business_hours", "")

            lines.append(f"{i}. {name}")
            lines.append(f"   📍 {addr}")
            if distance:
                lines.append(f"   📏 {distance}")
            if hours:
                lines.append(f"   🕐 {hours}")
            lines.append("")

        blessing = rec_data.get("blessing", "")
        if blessing:
            lines.append(f"✨ {blessing}")

        return "\n".join(lines)

    def _format_activity_text(self, rec_data: Dict[str, Any]) -> str:
        """从 LLM JSON 格式化活动文本"""
        lines = ["📅 麦当劳当前活动：", ""]

        for i, item in enumerate(rec_data.get("activities", []), 1):
            title = item.get("title", "未知活动")
            desc = item.get("description", "")
            tag = item.get("tag", "")

            lines.append(f"{i}. {title}")
            if desc:
                lines.append(f"   {desc}")
            if tag:
                lines.append(f"   📌 {tag}")
            lines.append("")

        blessing = rec_data.get("blessing", "")
        if blessing:
            lines.append(f"✨ {blessing}")

        return "\n".join(lines)

    def _extract_keyword(self, message: str) -> str:
        """从消息中提取位置关键词（区县/地标等）"""
        # 优先提取「XX区」「XX县」「XX镇」等区县信息
        district_pattern = r'([\u4e00-\u9fff]{2,4}(?:区|县|镇|街道|路|广场|商场|大厦|中心))'
        matches = re.findall(district_pattern, message)
        if matches:
            # 去掉可能重复的城市部分
            city = self._extract_city(message)
            for m in matches:
                if city and city.rstrip("市") in m:
                    continue
                return m

        # 移除城市名后，提取剩余位置关键词
        city = self._extract_city(message)
        if city:
            remaining = message.replace(city, "").strip()
            # 移除常见问句词
            for w in ["附近", "的", "麦当劳", "门店", "哪里有", "哪里", "有", "吗",
                       "？", "?", "帮我", "找一下", "查一下", "今天", "现在",
                       "麦麦", "m记", "优惠", "券", "折扣", "打折",
                       "什么", "活动", "新品", "上新", "限定", "日历",
                       "营养", "热量", "卡路里", "减肥", "减脂", "低卡", "蛋白质"]:
                remaining = remaining.replace(w, "")
            remaining = remaining.strip()
            if remaining and len(remaining) >= 2:
                return remaining
        return ""

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

        image_url, text_fallback = await self._do_recommendation(user_message, event)
        if image_url:
            logger.info("[麦当劳推荐] 渲染图片成功，发送图片消息")
            yield event.image_result(image_url)
        else:
            logger.info("[麦当劳推荐] 未渲染图片，发送文本消息")
            yield event.plain_result(text_fallback)
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
        """监听所有消息，检测关键词和意图"""
        message = event.message_str

        # 跳过指令消息（以 / 开头的消息）
        if message.startswith("/") or message.startswith("\\"):
            return

        # 优先检测意图（优惠券/门店/活动/营养）
        if self.config.get("enable_intent_detection", True):
            intent = self._detect_intent(message)
        else:
            intent = None
        if intent:
            logger.info(f"[麦当劳推荐] 检测到意图: {intent}")

            if intent == "coupon":
                image_url, text_fallback = await self._handle_coupon_intent(message, event)
            elif intent == "store":
                image_url, text_fallback = await self._handle_store_intent(message, event)
            elif intent == "activity":
                image_url, text_fallback = await self._handle_activity_intent(message, event)
            elif intent == "nutrition":
                image_url, text_fallback = await self._handle_nutrition_intent(message, event)
            else:
                return

            if image_url:
                logger.info(f"[麦当劳推荐] 意图 {intent} 渲染图片成功")
                yield event.image_result(image_url)
            else:
                logger.info(f"[麦当劳推荐] 意图 {intent} 发送文本消息")
                yield event.plain_result(text_fallback)
            event.stop_event()
            return

        # 未匹配意图，检测关键词触发美食推荐
        matched_keyword = self._match_keyword(message)
        if not matched_keyword:
            return  # 未匹配到关键词，不处理

        logger.info(f"[麦当劳推荐] 检测到关键词 '{matched_keyword}'，触发美食推荐")

        image_url, text_fallback = await self._do_recommendation(message, event)
        if image_url:
            logger.info("[麦当劳推荐] 渲染图片成功，发送图片消息")
            yield event.image_result(image_url)
        else:
            logger.info("[麦当劳推荐] 未渲染图片，发送文本消息")
            yield event.plain_result(text_fallback)
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
