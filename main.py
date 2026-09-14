# -*- coding: utf-8 -*-
"""
astrbot_plugin_ticketmaker2 — 本地 PIL 重绘版报销凭证生成插件。

与 1 代（astrbot_plugin_ticketmaker）的区别：
- 1 代用 Playwright 打开 https://fog.vicc.wang/ticket.html，填表后对 canvas 截图；
- 本插件完全离线，用 PIL 按站点 canvas 的同一套绘图逻辑重绘，产出一致的票面图，
  不再依赖第三方网站，也不需要浏览器/Playwright。

命令与 1 代保持一致：
  /ticket help
  /ticket <发站> <到站> <车次> <日期> <时间> <车厢> <座位> <座别> <票价> [姓名] [身份证] [检票信息] [颜色]
  /ticket ocr [颜色]   （并在同一条消息附上车票截图）
"""
import os
import re
import sys
import json
import base64
import tempfile
from pathlib import Path
from datetime import datetime

import requests
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import tm2_renderer as R  # noqa: E402

# ===================== 配置 =====================
# OCR / 大模型均为可选：未配置时仍可正常手动生成与纯正则识别。
# 可写在插件目录下的 config.json（首次运行会自动生成模板），或用环境变量覆盖。
_CONFIG_PATH = os.path.join(_HERE, "config.json")
_DEFAULT_CONFIG = {
    "baidu_ocr_api_key": "",
    "baidu_ocr_secret_key": "",
    "deepseek_api_key": "",
    "deepseek_base_url": "https://api.deepseek.com/v1",
}


def _load_config():
    cfg = dict(_DEFAULT_CONFIG)
    if os.path.exists(_CONFIG_PATH):
        try:
            cfg.update(json.load(open(_CONFIG_PATH, encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            logger.error(f"读取 config.json 失败: {e}")
    else:
        try:
            json.dump(_DEFAULT_CONFIG, open(_CONFIG_PATH, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
        except Exception:
            pass
    cfg["baidu_ocr_api_key"] = os.environ.get("BAIDU_OCR_API_KEY", cfg["baidu_ocr_api_key"])
    cfg["baidu_ocr_secret_key"] = os.environ.get("BAIDU_OCR_SECRET_KEY", cfg["baidu_ocr_secret_key"])
    cfg["deepseek_api_key"] = os.environ.get("DEEPSEEK_API_KEY", cfg["deepseek_api_key"])
    return cfg


# ===================== 默认值（与 1 代一致） =====================
DEFAULT_TIME = "12:00"
DEFAULT_CARRIAGE = "01"
DEFAULT_SEAT = "001"
DEFAULT_SEAT_CLASS = "二等座"
DEFAULT_NAME = "旅客"
DEFAULT_IDENTITY = "101111200001010001"
DEFAULT_COLOR = "blue"

COLOR_MAP = {
    '蓝': 'blue', 'blue': 'blue', '蓝色': 'blue', '蓝票': 'blue',
    '红': 'red', 'red': 'red', '红色': 'red', '红票': 'red'
}

# 站点页面里由 HTML 默认值提供、1 代插件从不修改的字段（保持与原版一致）
TICKET_DEFAULTS = {
    "ticket_no": "L098229",
    "seat_position": "",
    "ticket_machine_id": "23795310160122L098453  JM",
    "qrcode_string": "https://fog.vicc.wang/ticket",
    "infoline1": "0",
    "infoline2": "0",
    "infoline3": "0",
    "is_child": False,
    "is_student": False,
    "is_online": False,
    "is_discount": False,
}

# 输出尺寸：False = canvas 原生分辨率（809x509 / 800x535，最清晰）
#           True  = 复刻 1 代元素截图尺寸（高度 540 + 1px 边框）
MATCH_ORIGINAL_PLUGIN_SIZE = False

# ===================== 车站拼音表 =====================
_STATIONS = None


def _stations():
    global _STATIONS
    if _STATIONS is None:
        try:
            _STATIONS = json.load(open(os.path.join(_HERE, "stations.json"),
                                       encoding="utf-8")).get("pinyin", {})
        except Exception as e:  # noqa: BLE001
            logger.error(f"车站拼音表加载失败: {e}")
            _STATIONS = {}
    return _STATIONS


# ============================================================
# 工具函数
# ============================================================
def get_baidu_ocr_token(api_key, secret_key):
    """获取百度OCR access_token，返回 (token, error_msg)。"""
    url = (f"https://aip.baidubce.com/oauth/2.0/token?grant_type=client_credentials"
           f"&client_id={api_key}&client_secret={secret_key}")
    try:
        resp = requests.post(url, timeout=8)
    except Exception as e:  # noqa: BLE001
        return None, f"网络请求异常: {e}"
    if resp.status_code != 200:
        return None, f"HTTP 状态码 {resp.status_code}"
    try:
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        return None, f"响应解析失败: {e}"
    if "access_token" in data:
        return data["access_token"], None
    return None, f"{data.get('error')}: {data.get('error_description')}"


def ocr_image(image_bytes, token):
    url = f"https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic?access_token={token}"
    img_base64 = base64.b64encode(image_bytes).decode()
    payload = {"image": img_base64}
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=10)
        return resp.json()
    except Exception as e:  # noqa: BLE001
        logger.error(f"OCR请求异常: {e}")
        return {"error_code": -1, "error_msg": str(e)}


# ============================================================
# 插件主类
# ============================================================
@register("ticket_web_auto2", "捞化", "本地 PIL 重绘生成报销凭证（离线，支持OCR）", "2.0.0")
class TicketWebPlugin2(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.cfg = _load_config()
        self.ocr_token = None
        self.ocr_reason = ""
        self.deepseek_client = None
        err = self._ensure_ocr()
        if err:
            logger.warning(f"OCR 暂不可用：{err}")
        self._ensure_deepseek()
        logger.info("TicketWebPlugin2 初始化完成（本地 PIL 渲染）")

    # ========== OCR / DeepSeek 惰性初始化（改 config.json 后无需重启） ==========
    def _ensure_ocr(self):
        """确保百度OCR token 可用。返回 None 表示可用，否则返回原因字符串。"""
        if self.ocr_token:
            return None
        cfg = _load_config()
        self.cfg = cfg
        ak = cfg.get("baidu_ocr_api_key")
        sk = cfg.get("baidu_ocr_secret_key")
        if not (ak and sk):
            self.ocr_reason = ("未配置百度OCR密钥：请在插件目录 config.json 里填写 "
                               "baidu_ocr_api_key / baidu_ocr_secret_key（保存后直接重发命令即可）")
            return self.ocr_reason
        token, err = get_baidu_ocr_token(ak, sk)
        if not token:
            self.ocr_token = None
            self.ocr_reason = f"百度OCR鉴权失败（{err}）"
            return self.ocr_reason
        self.ocr_token = token
        self.ocr_reason = ""
        logger.info("百度OCR Token获取成功")
        return None

    def _ensure_deepseek(self):
        if self.deepseek_client:
            return
        cfg = self.cfg or {}
        key = cfg.get("deepseek_api_key")
        if not key:
            return
        try:
            from openai import OpenAI
            self.deepseek_client = OpenAI(
                api_key=key,
                base_url=cfg.get("deepseek_base_url", "https://api.deepseek.com/v1"),
            )
            logger.info("DeepSeek客户端初始化成功")
        except Exception as e:  # noqa: BLE001
            logger.error(f"DeepSeek初始化失败: {e}")

    # ========== 提取图片 ==========
    def _get_image_urls(self, event: AstrMessageEvent):
        urls = []
        if hasattr(event, 'get_images') and callable(event.get_images):
            try:
                imgs = event.get_images()
                if imgs:
                    urls.extend(imgs)
                    return urls
            except Exception:
                pass
        if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'message'):
            for seg in event.message_obj.message:
                if isinstance(seg, Image):
                    url = getattr(seg, 'url', None)
                    if url:
                        urls.append(url)
                        continue
                    file_path = getattr(seg, 'file', None)
                    if file_path:
                        urls.append(file_path)
                        continue
        return urls

    def _clean_station(self, name):
        if not name:
            return ""
        if name.endswith("站"):
            return name[:-1]
        return name

    # ========== DeepSeek 解析（仅核心字段） ==========
    def _parse_with_deepseek(self, raw_text):
        if not self.deepseek_client:
            return {}
        prompt = f"""
你是一个火车票信息提取助手。请从以下OCR识别出的文本中提取出发站(start)、到达站(end)、车次(train)、日期(date，格式 YYYY-MM-DD)、出发时间(time_depart，HH:mm)、到达时间(time_arrive，HH:mm)、车厢号(carriage)、座位号(seat)、座别(seat_class)、票价(price，数字)。**注意**：站名不要包含“站”字。如果某个字段缺失，设为空字符串。以JSON格式返回，key分别为：start, end, train, date, time_depart, time_arrive, carriage, seat, seat_class, price。只返回JSON。

OCR文本：
{raw_text}
"""
        try:
            response = self.deepseek_client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                response_format={"type": "json_object"}
            )
            result = json.loads(response.choices[0].message.content)
            logger.info(f"DeepSeek解析结果: {result}")
            for key in ['start', 'end']:
                if result.get(key):
                    result[key] = self._clean_station(result[key])
            return result
        except Exception as e:  # noqa: BLE001
            logger.error(f"DeepSeek解析失败: {e}")
            return {}

    # ========== 构建命令字符串 ==========
    def _build_command(self, start, end, train, date, time, carriage, seat, seat_class,
                       price, name="旅客", identity="", checkin="", color="blue"):
        if not name:
            name = DEFAULT_NAME
        if not identity:
            identity = DEFAULT_IDENTITY
        parts = ["/ticket", start, end, train, date, time, carriage, seat, seat_class,
                 str(price), name, identity]
        if checkin:
            parts.append(checkin)
        if color != "blue":
            parts.append("红" if color == "red" else "蓝")
        return " ".join(parts)

    def _parse_color(self, parts):
        color = DEFAULT_COLOR
        if not parts:
            return parts, color
        last = parts[-1]
        if last in COLOR_MAP:
            color = COLOR_MAP[last]
            parts = parts[:-1]
        return parts, color

    # ========== 生成图片（本地 PIL 渲染） ==========
    def _generate(self, color, **fields):
        data = dict(TICKET_DEFAULTS)
        data.update({k: v for k, v in fields.items() if v is not None})
        # 车站拼音（与站点一致：查不到则保留页面默认值）
        py = _stations()
        if data.get("start") and "start_py" not in fields:
            data["start_py"] = py.get(str(data["start"]), "Shenzhendong")
        if data.get("end") and "end_py" not in fields:
            data["end_py"] = py.get(str(data["end"]), "Jiujiang")
        img = R.render_ticket(data, color)
        if MATCH_ORIGINAL_PLUGIN_SIZE:
            w = max(1, round(img.size[0] * 540 / img.size[1]))
            img = img.resize((w, 540))
            from PIL import ImageOps
            img = ImageOps.expand(img, border=1, fill=(209, 208, 208))
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG")
            return tmp.name

    # ========== 主命令 ==========
    @filter.command("ticket")
    async def handle_ticket(self, event: AstrMessageEvent):
        parts = event.message_str.strip().split()

        if len(parts) == 2 and parts[1].lower() == 'help':
            help_text = (
                "📋 **铁路报销凭证生成器 - 使用指南**\n\n"
                "**手动生成**：\n"
                "/ticket 发站 到站 车次 日期 时间 车厢号 座位号 座别 票价 [姓名] [身份证] [检票/候车信息] [颜色]\n"
                "示例：/ticket 深圳东 九江 K1020 2024-01-25 18:55 11 104 二等座 163.5 张三 110101199001011234 检票:候车室5 红\n"
                "注意：姓名、身份证、检票信息、颜色均为可选参数。颜色可选“蓝”或“红”（或 blue/red），默认蓝色。\n"
                "若省略姓名和身份证，将使用默认值（姓名“旅客”，身份证 101111200001010001）。\n\n"
                "**OCR 自动识别**：\n"
                "发送 /ticket ocr [颜色] 并附上一张车票截图（同一条消息），机器人将自动识别并生成。\n"
                "示例：/ticket ocr 红 （生成红色票证）\n"
                "⚠️ OCR识别仅提取发站、到站、车次、日期、时间、车厢、座位、座别、票价，其余字段均使用默认值。\n"
                "默认姓名“旅客”，身份证 101111200001010001。\n\n"
                "💡 生成图片后，机器人会附带可复制的命令，方便重新生成或修改参数。"
            )
            yield event.plain_result(help_text)
            return

        # OCR 模式
        if len(parts) >= 2 and parts[1].lower() == 'ocr':
            err = self._ensure_ocr()
            if err:
                yield event.plain_result(f"❌ {err}")
                return
            ocr_parts = parts[2:]
            ocr_parts, color = self._parse_color(ocr_parts)
            image_urls = self._get_image_urls(event)
            if not image_urls:
                yield event.plain_result("📸 请在同一消息中附上一张车票截图，例如：发送 /ticket ocr 并带上图片")
                return
            result_chain = await self._handle_image_logic(event, image_urls[0], color)
            if result_chain:
                yield event.chain_result(result_chain)
            return

        # 手动生成
        if len(parts) < 10:
            yield event.plain_result(
                "参数不足！格式：\n"
                "/ticket 发站 到站 车次 日期 时间 车厢号 座位号 座别 票价 [姓名] [身份证] [检票/候车信息] [颜色]\n"
                "示例：/ticket 深圳东 九江 K1020 2024-01-25 18:55 11 104 二等座 163.5\n"
                "或发送 /ticket ocr 并附上图片进行自动识别，输入 /ticket help 查看帮助"
            )
            return

        args = parts[1:]
        args, color = self._parse_color(args)

        if len(args) < 9:
            yield event.plain_result("缺少必要参数（发站、到站、车次、日期、时间、车厢号、座位号、座别、票价）。")
            return
        start, end, train, date, time, carriage, seat, seat_class, price = args[:9]

        name = DEFAULT_NAME
        identity = DEFAULT_IDENTITY
        checkin = ""
        if len(args) >= 10:
            name = args[9] if args[9] else DEFAULT_NAME
        if len(args) >= 11:
            identity = args[10] if args[10] else DEFAULT_IDENTITY
        if len(args) >= 12:
            checkin = args[11]

        logger.info(f"手动生成: start={start}, end={end}, train={train}, date={date}, time={time}, "
                    f"carriage={carriage}, seat={seat}, seat_class={seat_class}, price={price}, "
                    f"name={name}, identity={identity}, checkin={checkin}, color={color}")

        try:
            tmp_path = self._generate(color, start=start, end=end, train=train, date=date,
                                      time=time, carriage=carriage, seat=seat,
                                      seat_class=seat_class, price=price, name=name,
                                      identity=identity, checkin=checkin)
            cmd = self._build_command(start, end, train, date, time, carriage, seat, seat_class,
                                      price, name, identity, checkin, color)
            yield event.chain_result([
                Plain(f"✅ 已生成报销凭证（重新生成请复制此命令：{cmd}）"),
                Image.fromFileSystem(tmp_path)
            ])
        except Exception as e:  # noqa: BLE001
            logger.error(f"生成失败: {e}")
            yield event.plain_result(f"❌ 生成失败：{str(e)}")

    # ========== 图片 OCR 逻辑 ==========
    async def _handle_image_logic(self, event, image_ref, color=DEFAULT_COLOR):
        err = self._ensure_ocr()
        if err:
            return [Plain(f"❌ {err}")]
        self._ensure_deepseek()

        try:
            if os.path.exists(image_ref):
                with open(image_ref, 'rb') as f:
                    img_bytes = f.read()
            else:
                resp = requests.get(image_ref, timeout=10)
                if resp.status_code != 200:
                    return [Plain(f"❌ 下载图片失败，状态码: {resp.status_code}")]
                img_bytes = resp.content
        except Exception as e:  # noqa: BLE001
            logger.error(f"下载/读取图片失败: {e}")
            return [Plain("❌ 图片下载失败，请重试。")]

        try:
            ocr_result = ocr_image(img_bytes, self.ocr_token)
            if "error_code" in ocr_result:
                return [Plain(f"❌ OCR识别失败: {ocr_result.get('error_msg')}")]
            raw_text = "\n".join([item["words"] for item in ocr_result.get("words_result", [])])
            logger.info(f"OCR原始文本:\n{raw_text}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"OCR处理异常: {e}")
            return [Plain(f"❌ OCR处理失败: {str(e)}")]

        fields = self._parse_with_regex(raw_text)
        logger.info(f"正则解析字段: {fields}")

        if self.deepseek_client:
            llm_fields = self._parse_with_deepseek(raw_text)
            for key in fields:
                if llm_fields.get(key):
                    fields[key] = llm_fields[key]
            logger.info(f"DeepSeek增强后字段: {fields}")

        required = ['start', 'end', 'train', 'price']
        missing = [k for k in required if not fields.get(k)]
        if missing:
            return [Plain(f"❌ 未能从图片中提取完整信息（缺失: {', '.join(missing)}），请手动使用 /ticket 命令。")]

        default_msg = []
        if not fields.get("time_depart"):
            fields["time_depart"] = DEFAULT_TIME
            default_msg.append("时间")
        if not fields.get("carriage"):
            fields["carriage"] = DEFAULT_CARRIAGE
            default_msg.append("车厢号")
        if not fields.get("seat"):
            fields["seat"] = DEFAULT_SEAT
            default_msg.append("座位号")
        if not fields.get("seat_class"):
            fields["seat_class"] = DEFAULT_SEAT_CLASS
            default_msg.append("座别")
        if not fields.get("date"):
            fields["date"] = datetime.now().strftime("%Y-%m-%d")
            default_msg.append("日期")

        name = DEFAULT_NAME
        identity = DEFAULT_IDENTITY
        checkin = ""

        hint = ""
        if default_msg:
            hint = f"（以下字段使用默认值：{', '.join(default_msg)}）"

        try:
            tmp_path = self._generate(color,
                                      start=fields["start"], end=fields["end"],
                                      train=fields["train"], date=fields["date"],
                                      time=fields["time_depart"], carriage=fields["carriage"],
                                      seat=fields["seat"], seat_class=fields["seat_class"],
                                      price=fields["price"], name=name, identity=identity,
                                      checkin=checkin)
            cmd = self._build_command(fields["start"], fields["end"], fields["train"],
                                      fields["date"], fields["time_depart"], fields["carriage"],
                                      fields["seat"], fields["seat_class"], fields["price"],
                                      name, identity, checkin, color)
            return [
                Plain(f"✅ 已根据图片自动生成报销凭证 {hint}\n重新生成请复制此命令：{cmd}"),
                Image.fromFileSystem(tmp_path)
            ]
        except Exception as e:  # noqa: BLE001
            logger.error(f"生成票证失败: {e}")
            return [Plain(f"❌ 生成票证失败: {str(e)}")]

    # ========== 正则解析（只提取核心字段） ==========
    def _parse_with_regex(self, full_text):
        start = end = train = date = time_depart = time_arrive = carriage = seat = seat_class = price = ""

        train_match = re.search(r'([A-Z]?\d+[A-Z]?)', full_text)
        if train_match:
            train = train_match.group(1)

        price_match = re.search(r'[￥¥]?(\d+\.?\d*)', full_text)
        if price_match:
            price = price_match.group(1)

        date_match = re.search(r'(\d{1,2})月(\d{1,2})日', full_text)
        if date_match:
            month, day = date_match.group(1), date_match.group(2)
            year = datetime.now().year
            date = f"{year}-{month.zfill(2)}-{day.zfill(2)}"

        times = re.findall(r'(\d{2}:\d{2})', full_text)
        if len(times) >= 2:
            time_depart, time_arrive = times[0], times[1]
        elif len(times) == 1:
            time_depart = times[0]

        seat_class_match = re.search(r'(二等座|一等座|商务座|特等座|软卧|硬卧|无座)', full_text)
        if seat_class_match:
            seat_class = seat_class_match.group(1)

        seat_match = re.search(r'(\d+)车(\d+[A-Z]?)', full_text)
        if seat_match:
            carriage = seat_match.group(1)
            seat = seat_match.group(2)

        station_pattern = r'([\u4e00-\u9fa5]{2,})'
        stations = re.findall(station_pattern, full_text)
        ignore_words = ['车票', '当日', '当次', '有效', '成人', '儿童', '学生', '现金', '支付', '购买',
                        '出站', '检票', '候车', '车厢', '座位', '二等座', '一等座', '商务座', '特等座',
                        '软卧', '硬卧', '无座', '线上购买', '非现金支付']
        stations = [s for s in stations if s not in ignore_words and len(s) >= 2]

        if "福州" in full_text and "上饶" in full_text:
            start, end = "福州", "上饶"
        elif len(stations) >= 2:
            start = stations[0]
            end = stations[1]
        else:
            from_match = re.search(r'发站[:：]\s*([\u4e00-\u9fa5]{2,})', full_text)
            to_match = re.search(r'到站[:：]\s*([\u4e00-\u9fa5]{2,})', full_text)
            if from_match:
                start = from_match.group(1)
            if to_match:
                end = to_match.group(1)

        start = self._clean_station(start)
        end = self._clean_station(end)

        if not seat_class:
            seat_class = "二等座"

        return {
            "start": start,
            "end": end,
            "train": train,
            "date": date,
            "time_depart": time_depart,
            "time_arrive": time_arrive,
            "carriage": carriage,
            "seat": seat,
            "seat_class": seat_class,
            "price": price
        }

    async def terminate(self):
        logger.info("TicketWebPlugin2 已卸载")
