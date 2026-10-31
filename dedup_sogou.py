# -*- coding: utf-8 -*-
"""sogou_lexicon.dict.yaml 一键修复：
1. 去重（每条词重复两遍，去重键 = (词, 码)，保留多音字）
2. 重新赋权 weight = BASE * min(音节数, 4)
   （luna_pinyin 官方词频为百分比量级 0~100，搜狗词放 100~400 档：
     单字不低于超高频字太多，长词靠整词命中自然靠前）
3. 头部删除 import_tables: luna_pinyin（结束菱形 import）
用法: python dedup_sogou.py
"""
import os

BASE = 100  # 唯一调参常数：官方高频被挤压降 50，搜狗长词偏弱升 200

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sogou_lexicon.dict.yaml")

header_lines = []
body = []
seen = set()
in_body = False
skip_indent = False

with open(SRC, encoding="utf-8") as f:
    for line in f:
        if in_body:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            word, code = parts[0], parts[1]
            if (word, code) in seen:
                continue
            seen.add((word, code))
            syllables = min(code.count(" ") + 1, 4)
            body.append((word, code, BASE * syllables))
        else:
            if line.strip() == "...":
                in_body = True
                header_lines.append(line.rstrip("\n"))
                continue
            # 跳过 import_tables 块及其他所有缩进行（header 只有顶层键）——菱形 import 的根源
            if line.strip().startswith("import_tables"):
                skip_indent = True
                continue
            if skip_indent and (line.startswith(" ") or line.startswith("\t")):
                continue
            skip_indent = False
            if line.startswith(" ") or line.startswith("\t"):
                continue
            header_lines.append(line.rstrip("\n"))

with open(SRC, "w", encoding="utf-8", newline="\n") as f:
    for h in header_lines:
        f.write(h + "\n")
    for word, code, weight in body:
        f.write(f"{word}\t{code}\t{weight}\n")

print(f"done: {len(body)} 词条（去重前 {len(seen) * 2 + 0} 半整数条 -> {len(body)} 条）")