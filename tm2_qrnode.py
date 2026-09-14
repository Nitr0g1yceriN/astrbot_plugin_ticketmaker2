# -*- coding: utf-8 -*-
"""
qrnode.py — 复刻 node-qrcode 的"最优分段"编码，使二维码与站点完全一致。

node-qrcode 的 Segments.fromString() 会做最优分段（numeric/alphanumeric/byte 混合），
而 segno 默认是整串单模式编码，导致同一段文本产生不同二维码。这里把 node-qrcode
的分段算法（正则切分 + 位长 DP + Dijkstra 最短路径）逐行移植，再把分段交给
segno 的 _encode() 做 ECC / 排布 / 掩码，从而得到与原站一模一样的矩阵。

参考实现位置：站点 dist/ticket.bundle.js 内 qrcode 浏览器版。
"""
from __future__ import annotations

import re
from typing import Dict, List

import segno.consts as C
import segno.encoder as E

NUMERIC_RE = re.compile(r"[0-9]+")
ALNUM_RE = re.compile(r"[A-Z $%*+\-./:]+")
# kanji 模式未启用时 node-qrcode 使用的 BYTE 正则
BYTE_RE = re.compile(r"[^A-Z0-9 $%*+\-./:]+")

_CC = {C.MODE_NUMERIC: (10, 12, 14),
       C.MODE_ALPHANUMERIC: (9, 11, 13),
       C.MODE_BYTE: (8, 16, 16)}


def _bits_len(n: int, mode: int) -> int:
    if mode == C.MODE_NUMERIC:
        return 10 * (n // 3) + (0 if n % 3 == 0 else (4 if n % 3 == 1 else 7))
    if mode == C.MODE_ALPHANUMERIC:
        return 11 * (n // 2) + (n % 2) * 6
    return 8 * n


def _cc_bits(mode: int, ver: int) -> int:
    a = _CC[mode]
    return a[0] if 1 <= ver < 10 else (a[1] if ver < 27 else a[2])


def _byte_len(s: str) -> int:
    return len(s.encode("utf-8"))


def _raw_split(text: str) -> List[Dict]:
    groups = []
    for rx, mode in ((NUMERIC_RE, C.MODE_NUMERIC), (ALNUM_RE, C.MODE_ALPHANUMERIC),
                     (BYTE_RE, C.MODE_BYTE)):
        for m in rx.finditer(text):
            groups.append({"data": m.group(0), "index": m.start(), "mode": mode,
                           "length": len(m.group(0))})
    groups.sort(key=lambda g: g["index"])
    return groups


def _dijkstra(graph: Dict, src: str, dst: str) -> List[str]:
    dist = {src: 0.0}
    prev: Dict[str, str] = {}
    q = [(0.0, src)]
    while q:
        cost, node = q.pop(0)
        for u, w in graph.get(node, {}).items():
            nd = cost + w
            if u not in dist or dist[u] > nd:
                dist[u] = nd
                prev[u] = node
                q.append((nd, u))
                q.sort(key=lambda x: x[0])  # 稳定排序，等价 JS Array.sort
    path = []
    cur = dst
    while cur is not None:
        path.append(cur)
        cur = prev.get(cur)
    path.reverse()
    return path


def _optimal_segments(text: str, ver: int) -> List[Dict]:
    groups = _raw_split(text)
    cand: List[List[Dict]] = []
    for g in groups:
        if g["mode"] == C.MODE_NUMERIC:
            cand.append([g,
                         {"data": g["data"], "mode": C.MODE_ALPHANUMERIC, "length": g["length"]},
                         {"data": g["data"], "mode": C.MODE_BYTE, "length": g["length"]}])
        elif g["mode"] == C.MODE_ALPHANUMERIC:
            cand.append([g,
                         {"data": g["data"], "mode": C.MODE_BYTE, "length": g["length"]}])
        else:
            cand.append([{"data": g["data"], "mode": C.MODE_BYTE, "length": _byte_len(g["data"])}])

    nodes: Dict[str, Dict] = {}
    graph: Dict[str, Dict] = {"start": {}}
    prev_layer = ["start"]
    for t in range(len(cand)):
        layer = cand[t]
        cur = []
        for n in range(len(layer)):
            o = layer[n]
            lid = f"{t}{n}"
            cur.append(lid)
            nodes[lid] = {"node": o, "lastCount": 0}
            graph[lid] = {}
            for tp in prev_layer:
                if tp in nodes and nodes[tp]["node"]["mode"] == o["mode"]:
                    graph[tp][lid] = (_bits_len(nodes[tp]["lastCount"] + o["length"], o["mode"])
                                      - _bits_len(nodes[tp]["lastCount"], o["mode"]))
                    nodes[tp]["lastCount"] += o["length"]
                else:
                    if tp in nodes:
                        nodes[tp]["lastCount"] = o["length"]
                    graph[tp][lid] = (_bits_len(o["length"], o["mode"]) + 4
                                      + _cc_bits(o["mode"], ver))
        prev_layer = cur
    for nid in prev_layer:
        graph[nid]["end"] = 0

    path = _dijkstra(graph, "start", "end")
    segs = [nodes[x]["node"] for x in path[1:-1]]
    merged: List[Dict] = []
    for s in segs:
        if merged and merged[-1]["mode"] == s["mode"]:
            merged[-1]["data"] += s["data"]
        else:
            merged.append({"data": s["data"], "mode": s["mode"]})
    for s in merged:  # 还原 node-qrcode 段对象 getLength() 的语义
        s["length"] = _byte_len(s["data"]) if s["mode"] == C.MODE_BYTE else len(s["data"])
    return merged


def _capacity_bits(ver: int, error: int) -> int:
    return C.SYMBOL_CAPACITY[ver][error]


def _segments_bitlen(segs: List[Dict], ver: int) -> int:
    return sum(4 + _cc_bits(s["mode"], ver) + _bits_len(s["length"], s["mode"]) for s in segs)


def _best_version(segs: List[Dict], error: int) -> int:
    for v in range(1, 41):
        if _segments_bitlen(segs, v) <= _capacity_bits(v, error):
            return v
    return 40


def _node_penalty(m) -> int:
    """逐行移植 node-qrcode 的 getPenaltyN1..N4（注意：N1/N3 的行列拿取顺序与 N4 统计全部模块）。"""
    size = len(m)

    # N1
    p1 = 0
    for s in range(size):
        h = g = 0
        e = t = None
        for u in range(size):
            v = g_(m, s, u)
            if v == e:
                h += 1
            else:
                if h >= 5:
                    p1 += h - 5 + 3
                e = v
                h = 1
            w = g_(m, u, s)
            if w == t:
                g = g + 1
            else:
                if g >= 5:
                    p1 += g - 5 + 3
                t = w
                g = 1
        if h >= 5:
            p1 += h - 5 + 3
        if g >= 5:
            p1 += g - 5 + 3
    # N2
    p2 = 0
    for i in range(size - 1):
        for j in range(size - 1):
            s = g_(m, i, j) + g_(m, i, j + 1) + g_(m, i + 1, j) + g_(m, i + 1, j + 1)
            if s == 4 or s == 0:
                p2 += 1
    p2 *= 3
    # N3
    p3 = 0
    for i in range(size):
        h = 0
        gg = 0
        for j in range(size):
            h = ((h << 1) & 2047) | g_(m, i, j)
            if j >= 10 and (h == 1488 or h == 93):
                p3 += 1
            gg = ((gg << 1) & 2047) | g_(m, j, i)
            if j >= 10 and (gg == 1488 or gg == 93):
                p3 += 1
    p3 *= 40
    # N4
    dark = sum(sum(row) for row in m)
    total = size * size
    den = total * 5
    ceil_val = -((-100 * dark) // den)  # ceil(100*dark/(total*5))
    p4 = 10 * abs(ceil_val - 10)
    return p1 + p2 + p3 + p4


def g_(m, r, c):
    return int(m[r][c])


def _node_best_mask(base, version: int, error: int, size: int, is_region) -> int:
    mask_fns = E.get_data_mask_functions(False)
    best = 0
    best_score = None
    for t in range(8):
        m = [bytearray(r) for r in base]
        E.apply_mask(m, mask_fns[t], size, size, is_region)
        E.add_format_info(m, version, error, t)
        score = _node_penalty(m)
        if best_score is None or score < best_score:
            best_score = score
            best = t
    return best


def _encode_node(segments, error: int, version: int):
    """逐字节复刻 node-qrcode 的比特流组装与掩码选择（含其“字节对齐时不再补 8 个 0”的填充行为），
    再复用 segno 的 ECC / 排布实现。"""
    buff = E.Buffer()
    for seg in segments:
        E.write_segment(buff, seg, None, E.version_range(version), False)
    capacity = C.SYMBOL_CAPACITY[version][error]
    E.write_terminator(buff, capacity, None, len(buff))
    rem = len(buff) % 8
    if rem:  # segno 在此处会多补 8 个 0，node-qrcode 不会
        buff.extend([0] * (8 - rem))
    E.write_pad_codewords(buff, version, capacity, len(buff))
    buff = E.make_final_message(version, error, buff)
    width = E.calc_matrix_size(version)
    base = E.make_matrix(width, width)
    E.add_finder_patterns(base, width, width)
    E.add_alignment_patterns(base, width, width)
    E.add_codewords(base, buff, version)

    fm = E.make_matrix(width, width)
    E.add_finder_patterns(fm, width, width)
    E.add_alignment_patterns(fm, width, width)
    fm[-8][8] = 1
    is_region = lambda i, j: fm[i][j] > 1  # noqa: E731

    mask = _node_best_mask(base, version, error, width, is_region)
    matrix = [bytearray(r) for r in base]
    E.apply_mask(matrix, E.get_data_mask_functions(False)[mask], width, width, is_region)
    E.add_format_info(matrix, version, error, mask)
    E.add_version_info(matrix, version)
    return matrix, mask


def make_matrix_utf8(text: str, error: int = C.ERROR_LEVEL_L):
    """返回 (matrix, version, mask)，与 node-qrcode 对同一文本的编码一致。"""
    raw = [{"data": g["data"], "mode": g["mode"],
            "length": _byte_len(g["data"]) if g["mode"] == C.MODE_BYTE else g["length"]}
           for g in _raw_split(text)]
    guess = _best_version(raw, error) if raw else 1
    segs = _optimal_segments(text, guess)
    ver = _best_version(segs, error)

    segments = E.Segments()
    for s in segs:
        segments.add_segment(E.make_segment(s["data"], s["mode"]))
    matrix, mask = _encode_node(segments, error, ver)
    return [list(row) for row in matrix], ver, mask


if __name__ == "__main__":
    m, v, mask = make_matrix_utf8("https://fog.vicc.wang/ticket")
    print("version", v, "mask", mask, "modules", len(m))
