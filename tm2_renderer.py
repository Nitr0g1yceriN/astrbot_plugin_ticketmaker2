# -*- coding: utf-8 -*-
"""
renderer.py — 用 PIL 复刻 https://fog.vicc.wang/ticket.html 的 canvas 绘制逻辑。

坐标/字号/字距/颜色/绘制顺序逐条移植自站点 dist/ticket.bundle.js。
- 拉丁字体用 Arial / Times New Roman，中文回退 Microsoft YaHei（已用字形对比验证）。
- textBaseline='middle' 的偏移用浏览器实测比例换算。
- 文字层按 SUPERSAMPLE 倍超采样绘制后再降采样，以逼近浏览器的次像素抗锯齿。
"""
from __future__ import annotations

import math
import os
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_WIN_FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

_MID_RATIO = {"sans": 0.275, "serif": 0.265, "cjk": 0.265}
_MEASURE_SCALE = 64
SUPERSAMPLE = 4


def _first(*names: str) -> Optional[str]:
    for n in names:
        p = n if os.path.isabs(n) else os.path.join(_WIN_FONTS, n)
        if os.path.exists(p):
            return p
    return None


LATIN_SANS = _first("arial.ttf", "LiberationSans-Regular.ttf", "DejaVuSans.ttf")
LATIN_SERIF = _first("times.ttf", "LiberationSerif-Regular.ttf", "DejaVuSerif.ttf")
CJK_FONT = _first("msyh.ttc", "msyh.ttf", "simhei.ttf", "simsun.ttc",
                  "NotoSansCJK-Regular.ttc", "wqy-microhei.ttc")

_cache: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    f = _cache.get(key)
    if f is None:
        f = ImageFont.truetype(path, size)
        _cache[key] = f
    return f


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x2E80 <= o <= 0x9FFF) or (0xF900 <= o <= 0xFAFF) or (0xFE30 <= o <= 0xFE4F) \
        or (0xFF00 <= o <= 0xFFEF) or (0x3000 <= o <= 0x303F)


def _path_for(style: str, ch: str) -> str:
    if _is_cjk(ch):
        return CJK_FONT or (LATIN_SANS if style == "sans" else LATIN_SERIF)
    return LATIN_SANS if style == "sans" else LATIN_SERIF


try:
    import uharfbuzz as _hb
    _HB_OK = True
except Exception:
    _hb = None
    _HB_OK = False

_hb_cache: Dict[Tuple[str, int], object] = {}


def _hb_font(path: str, size: int):
    key = (path, size)
    f = _hb_cache.get(key)
    if f is None:
        blob = _hb.Blob.from_file_path(path)
        face = _hb.Face(blob)
        font = _hb.Font(face)
        font.scale = (size * 64, size * 64)
        _hb_cache[key] = font
        f = font
    return f


def _shape_run(path: str, size: int, run: str):
    """用 HarfBuzz 塑形一个 run，返回逐字 advance（含 kerning）。"""
    font = _hb_font(path, size)
    buf = _hb.Buffer()
    buf.add_str(run)
    buf.guess_segment_properties()
    _hb.shape(font, buf, {"kern": True, "liga": True})
    adv = [0.0] * len(run)
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        c = info.cluster
        if 0 <= c < len(run):
            adv[c] += pos.x_advance / 64.0
    return adv


def advances(style: str, size: int, text: str) -> List[float]:
    """逐字 advance（浏览器等效）；无可用字体时退回 0.5em 估算。"""
    if not text:
        return []
    if not _HB_OK:
        out = []
        for ch in text:
            path = _path_for(style, ch)
            out.append(_font(path, size * _MEASURE_SCALE).getlength(ch) / _MEASURE_SCALE
                       if path else size * 0.5)
        return out
    out: List[float] = []
    i = 0
    while i < len(text):
        path = _path_for(style, text[i])
        if not path:
            out.append(size * 0.5)
            i += 1
            continue
        j = i
        while j < len(text) and _path_for(style, text[j]) == path:
            j += 1
        out.extend(_shape_run(path, size, text[i:j]))
        i = j
    return out


def advance(style: str, size: int, ch: str) -> float:
    a = advances(style, size, ch)
    return a[0] if a else 0.0


def measure(style: str, size: int, text: str) -> float:
    return sum(advances(style, size, text))


def _mid_offset(style: str, size: int, text: str) -> float:
    ch = text[0] if text else "M"
    path = _path_for(style, ch)
    ratio = _MID_RATIO["sans"] if path == LATIN_SANS else _MID_RATIO["cjk"]
    return ratio * size


# ---------------------------------------------------------------- 文本绘制
def draw_text(draw, text, x, y, style, size, fill, align="left"):
    if not text:
        return
    adv = advances(style, size, text)
    w = sum(adv)
    if align == "center":
        x -= w / 2.0
    elif align == "right":
        x -= w
    yb = y + _mid_offset(style, size, text)
    cx = x
    for ch, a in zip(text, adv):
        draw.text((round(cx), round(yb)), ch, font=_font(_path_for(style, ch), size),
                  fill=fill, anchor="ls")
        cx += a


def draw_text_spacing(draw, text, x, y, style, size, fill, spacing):
    if not text:
        return
    yb = y + _mid_offset(style, size, text)
    cx = x
    for ch, a in zip(text, advances(style, size, text)):
        draw.text((round(cx), round(yb)), ch, font=_font(_path_for(style, ch), size),
                  fill=fill, anchor="ls")
        cx += a + spacing


def draw_text_width(draw, text, x, y, style, size, fill, width):
    if not text:
        return
    adv = advances(style, size, text)
    w = sum(adv)
    if w > width or len(text) <= 1:
        draw_text(draw, text, x, y, style, size, fill, "left")
        return
    yb = y + _mid_offset(style, size, text)
    step = (width - w) / (len(text) - 1)
    cx = x
    for ch, a in zip(text, adv):
        draw.text((round(cx), round(yb)), ch, font=_font(_path_for(style, ch), size),
                  fill=fill, anchor="ls")
        cx += a + step


# ---------------------------------------------------------------- 线条
def _dashed_path(draw, pts, color, width, pattern=(10, 5)):
    on, off = pattern
    remaining = on
    pen = True
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg == 0:
            continue
        t = 0.0
        while t < seg - 1e-9:
            step = min(remaining, seg - t)
            if pen and step > 0:
                ax = x0 + (x1 - x0) * (t / seg)
                ay = y0 + (y1 - y0) * (t / seg)
                bx = x0 + (x1 - x0) * ((t + step) / seg)
                by = y0 + (y1 - y0) * ((t + step) / seg)
                draw.line([(ax, ay), (bx, by)], fill=color, width=width)
            t += step
            remaining -= step
            if remaining <= 1e-9:
                pen = not pen
                remaining = on if pen else off


def _price_str(price) -> str:
    try:
        d = Decimal(str(float(price))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    except Exception:
        return "￥NaN"
    return "￥" + format(d, "f")


BLUE = {"file": "blueTicket.png", "width": 809, "height": 509, "dx": 0, "dy": 0}
RED = {"file": "redTicket.png", "width": 800, "height": 535, "dx": 10, "dy": 20}
INFO3 = {
    0: ("报销凭证 遗失不补", "退票改签时须交回车站"),
    1: ("买票请到12306 发货请到95306", "中国铁路祝您旅途愉快"),
    2: ("欢度国庆 祝福祖国", "中国铁路祝您旅途愉快"),
    9: ("奋斗百年路 启航新征程", "热烈庆祝中国共产党成立100周年"),
}


def _background(cfg) -> Image.Image:
    W, H = cfg["width"], cfg["height"]
    bg = Image.open(os.path.join(ASSET_DIR, cfg["file"])).convert("RGBA")
    img = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    img.paste(bg, (0, 0), bg)
    return img


def render_ticket(fields: Dict, color: str = "blue", supersample: int = SUPERSAMPLE) -> Image.Image:
    cfg = RED if color == "red" else BLUE
    W, H = cfg["width"], cfg["height"]
    a, d = cfg["dx"], cfg["dy"]
    S, R, BLACK = "serif", "sans", (0, 0, 0)
    g = fields.get
    ss = max(1, int(supersample))

    layer = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    dr = ImageDraw.Draw(layer)

    def X(v):
        return v * ss

    def T(text, x, y, style, size, fill, align="left"):
        draw_text(dr, text, X(x), X(y), style, size * ss, fill, align)

    def TS(text, x, y, style, size, fill, spacing):
        draw_text_spacing(dr, text, X(x), X(y), style, size * ss, fill, spacing * ss)

    def TW(text, x, y, style, size, fill, width):
        draw_text_width(dr, text, X(x), X(y), style, size * ss, fill, width * ss)

    # ticketNo（红色 40px Arial，字距10）
    y, b = 50, 45
    TS(str(g("ticket_no", "")), b + a, y + d, R, 40, (255, 0, 0), 10)
    # 检票信息（30px Times 右对齐 @760）
    T(str(g("checkin", "")), 760 + a, y + d - 10, S, 30, BLACK, "right")

    # 始发站 / 到达站
    y += 55
    _station(T, TW, str(g("start", "")), 60.0 + a, y + d, S, BLACK, True, str(g("start_py", "")), ss)
    _station(T, TW, str(g("end", "")), 550.0 + a, y + d, S, BLACK, False, str(g("end_py", "")), ss)

    # 车次 + 箭头
    nx, ny = 400 + a, y + d
    T(str(g("train", "")), nx, ny, S, 46, BLACK, "center")
    dr.line([(X(nx - 55), X(ny + 30)), (X(nx + 65), X(ny + 30)), (X(nx + 50), X(ny + 25))],
            fill=BLACK, width=2 * ss, joint="curve")

    # 日期 / 时间
    y += 85
    _date_time(T, str(g("date", "")), str(g("time", "")), 40 + a, y + d, S, BLACK)

    # 车厢 / 座位
    cx = 510 + a
    for seg, sz in ((g("carriage", ""), 40), ("车", 24), (g("seat", ""), 40), ("号", 24)):
        txt = str(seg)
        T(txt, cx, y + d, S, sz, BLACK, "left")
        cx += measure(S, sz, txt) + 3
    cx -= 3
    T(str(g("seat_position", "")), cx, y + d, S, 24, BLACK, "left")

    # 票价
    y += 45
    price_txt = _price_str(g("price", ""))
    T(price_txt, 40 + a, y + d, S, 36, BLACK, "left")
    pw = measure(S, 36, price_txt)
    T("元", 40 + pw + a, y + d, S, 24, BLACK, "left")

    # 孩/学/网/惠（原插件不勾选，逻辑保留）
    flags = [(g("is_child"), "孩"), (g("is_student"), "学"), (g("is_online"), "网"),
             (g("is_discount"), "惠")]
    fb = 360 - 20 * sum(1 for v, _ in flags if v)
    for v, ch in flags:
        if v:
            fb += 40
            T(ch, fb + a, y + d, S, 24, BLACK, "left")
            _circle(dr, X(fb + a + 12), X(y + d - 2), X(15), BLACK, 2 * ss)

    # 座别 @620 居中
    T(str(g("seat_class", "")), 620 + a, y + d, S, 30, BLACK, "center")

    # infoline1 / infoline2 / 证件号 / 姓名
    y += 35
    T(["", "退票费", "限乘当日当次车"][int(g("infoline1", 0) or 0)], 40 + a, y + d, S, 24, BLACK, "left")
    y += 35
    T(["仅供报销使用", ""][int(g("infoline2", 0) or 0)], 40 + a, y + d, S, 24, BLACK, "left")
    y += 40
    T(str(g("identity", "")), 40 + a, y + d, S, 35, BLACK, "left")
    T(str(g("name", "")), 400 + a, y + d, S, 32, BLACK, "left")

    # 信息框（虚线 10/5）
    y += 20
    _dashed_path(dr, [(X(80 + a), X(y + d)), (X(540 + a), X(y + d)),
                      (X(540 + a), X(y + d + 70)), (X(80 + a), X(y + d + 70)),
                      (X(80 + a), X(y + d))], BLACK, 2 * ss, (10 * ss, 5 * ss))

    # infoline3 两行居中 @300
    y += 20
    l1, l2 = INFO3.get(int(g("infoline3", 0) or 0), INFO3[0])
    T(l1, 300 + a, y + d, S, 24, BLACK, "center")
    y += 30
    T(l2, 300 + a, y + d, S, 24, BLACK, "center")

    # 取票机编号
    y += 50
    T(str(g("ticket_machine_id", "")), 40 + a, y + d + (0 if color == "red" else 10), S, 27, BLACK, "left")

    # 降采样 -> 叠加到底图
    if ss > 1:
        layer = layer.resize((W, H), Image.LANCZOS)
    img = _background(cfg)
    img.alpha_composite(layer)

    # 二维码（绝对坐标，1x 直接贴）
    try:
        from tm2_qrgen import make_qr
        size = 136 if color == "red" else 128
        pos = (600, 360) if color == "red" else (600, 300)
        qr = make_qr(str(g("qrcode_string", "https://fog.vicc.wang/ticket")), size)
        img.paste(qr, pos, qr)
    except Exception:
        pass

    return img.convert("RGB")


def _station(T, TW, station, x, y, style, color, is_start, pinyin, ss):
    n = x
    aa = n + 90
    if len(station) < 4:
        TW(station, n, y, style, 50, color, 160)
    else:
        off = 15 if is_start else 20
        TW(station, n - off * (len(station) - 3), y, style, 50, color, 170 + 20 * (len(station) - 3))
    n += 165 if len(station) < 4 else 165 + (33 if is_start else 25) * (len(station) - 3)
    T("站", n, y, style, 32, color, "left")
    # 注意：浏览器此处 font 已切为 30px，故用 30px 度量站名宽度
    cx = max(aa, measure(style, 30, station) / 2 + 100) if is_start else aa
    T(pinyin, cx, y + 40, style, 30, color, "center")


def _date_time(T, date_s, time_s, x, y, style, color):
    try:
        yy, mm, dd = date_s.split("-")
        mm = mm.zfill(2)
        dd = dd.zfill(2)
    except Exception:
        yy = mm = dd = "NaN"
    n = x
    for val, unit in ((yy, "年"), (mm, "月"), (dd, "日")):
        T(val, n, y, style, 38, color, "left")
        n += measure(style, 38, val) + 3
        T(unit, n, y, style, 24, color, "left")
        n += measure(style, 24, unit) + 3
    n += 10
    T(time_s, n, y, style, 38, color, "left")
    n += measure(style, 38, time_s) + 3
    T("开", n, y, style, 24, color, "left")


def _circle(draw, cx, cy, r, color, width):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=width)
