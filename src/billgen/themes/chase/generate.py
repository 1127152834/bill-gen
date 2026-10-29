#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大通（Chase）模板生成器 — 分发层。
个人版与商业版为完全独立模块（各自独立的模板/码表/布局/元数据资产，物理隔离不混用）：
  generate_personal.py  ← 个人版 College Checking
  generate_business.py  ← 商业版 Business Complete
本文件仅按 template 字段路由。"""
from billgen.themes.chase import generate_business, generate_personal


def build_pdf(d, out_path, watermark=True):
    tag = d.get("template")
    if tag == "business":
        return generate_business.build_pdf(d, out_path, watermark)
    if tag == "personal":
        return generate_personal.build_pdf(d, out_path, watermark)
    raise ValueError(f"unknown template: {tag}")
