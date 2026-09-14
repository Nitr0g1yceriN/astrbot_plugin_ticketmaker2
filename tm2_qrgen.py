# -*- coding: utf-8 -*-
"""qrgen.py — 生成与站点完全一致的二维码。

站点使用 node-qrcode（errorCorrectionLevel=L, margin=0, 亮块透明），
本模块通过 qrnode 复刻其编码/掩码，再按同样的渲染参数（natural = modules*4，
再平滑缩放到目标尺寸）绘制。
"""
from __future__ import annotations

import os
import sys

from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
_VENDOR = os.path.join(_HERE, "_vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import segno.consts as _C  # noqa: E402

from tm2_qrnode import make_matrix_utf8  # noqa: E402


def make_qr(data: str, size: int = 128) -> Image.Image:
    """返回 size x size 的 RGBA 二维码（亮模块透明）。"""
    if not data:
        data = "https://fog.vicc.wang/ticket"
    m, ver, mask = make_matrix_utf8(data, _C.ERROR_LEVEL_L)
    n = len(m)
    scale = 4  # node-qrcode 默认 scale
    nat = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    px = nat.load()
    for r in range(n):
        row = m[r]
        for c in range(n):
            if row[c]:
                px[c, r] = (0, 0, 0, 255)
    big = nat.resize((n * scale, n * scale), Image.NEAREST)
    return big.resize((size, size), Image.BILINEAR)
