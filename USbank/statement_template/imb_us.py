#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""USPS Intelligent Mail Barcode（USPS-B-3200 canonical 算法移植自 TCPDF imb.php）

两原稿样本位级复现（2026-10-26 逆向实证）：
- 65 根四态条，字体 USPSIMBCompact 每字符=1 根条（A=升/D=降/F=满/T=跟踪，字形实测）
- 载荷：跟踪码 20 位（BarcodeID 00 + STID 260 + MailerID 106481 + 9 位序列号）
  + 路由码 11 位（ZIP5 + 4 + 门牌号末 2 位）；ER 文字行 = MailerID+序列号+" ER"
- 结构：路由偏移(+0/1/100001/1000100001) → ×10+t0 → ×5+t1 → 十进制串接 t2..19
  → 13 字节 → CRC-11(poly 0x0F35, init 0x7FF, 首字节跳高 2 位)
  → 102bit 数据 → cw0=(mod 636)×2 + cw1..8 mod 1365 + cw9 余数(fcs bit10 置位时 +659)
  → 5of13/2of13 查表（对称对填表尾、非对称对填表头）→ fcs 位取反 → **数组反转**
  → 65 条 asc/dsc 位置表输出
- 坑：PHP `(x<<2)>>2` = 低 6 位（非 (x<<2)&0xFF）；fcs 取反在码字序、条形查表在
  反转序；解码 fcs 暴力 2048 须按 (9-k) 位取反
"""

ASC_CHR = [4,0,2,6,3,5,1,9,8,7,1,2,0,6,4,8,2,9,5,3,0,1,3,7,4,6,8,9,2,0,5,1,9,4,3,8,6,7,1,2,4,3,9,5,7,8,3,0,2,1,4,0,9,1,7,0,2,4,6,3,7,1,9,5,8]
DSC_CHR = [7,1,9,5,8,0,2,4,6,3,5,8,9,7,3,0,6,1,7,4,6,8,9,2,5,1,7,5,4,3,8,7,6,0,2,5,4,9,3,0,1,6,8,2,0,4,5,9,6,7,5,2,6,3,8,5,1,9,8,7,4,0,2,6,3]
ASC_POS = [3,0,8,11,1,12,8,11,10,6,4,12,2,7,9,6,7,9,2,8,4,0,12,7,10,9,0,7,10,5,7,9,6,8,2,12,1,4,2,0,1,5,4,6,12,1,0,9,4,7,5,10,2,6,9,11,2,12,6,7,5,11,0,3,2]
DSC_POS = [2,10,12,5,9,1,5,4,3,9,11,5,10,1,6,3,4,1,10,0,2,11,8,6,1,12,3,8,6,4,4,11,0,6,1,9,11,5,3,7,3,10,7,11,8,2,10,3,5,8,0,3,12,11,8,4,5,1,3,0,7,12,9,8,10]

ROUTING_OFFSET = {0: 0, 5: 1, 9: 100001, 11: 1000100001}


def _reverse_us(num):
    rev = 0
    for _ in range(16):
        rev = (rev << 1) | (num & 1)
        num >>= 1
    return rev


def _imb_tables(n, size):
    """对称对(反序==自身)填表尾；非对称对(值+反序)填表头两槽。"""
    table = [0] * size
    lli, lui = 0, size - 1
    for count in range(8192):
        if bin(count).count("1") == n:
            rev = _reverse_us(count) >> 3
            if rev >= count:
                if rev == count:
                    table[lui] = count
                    lui -= 1
                else:
                    table[lli] = count
                    lli += 1
                    table[lli] = rev
                    lli += 1
    return table


T2 = _imb_tables(2, 78)
T5 = _imb_tables(5, 1287)
INV5 = {v: k for k, v in enumerate(T5)}
INV2 = {v: k for k, v in enumerate(T2)}


def _crc11(bytes_arr):
    genpoly = 0x0F35
    fcs = 0x07FF
    data = bytes_arr[0] << 5          # 首字节跳过高 2 位（右移对齐到 bit10）
    for _ in range(6):
        fcs = ((fcs << 1) ^ genpoly) if ((fcs ^ data) & 0x400) else (fcs << 1)
        fcs &= 0x7FF
        data <<= 1
    for byte in range(1, 13):
        data = bytes_arr[byte] << 3
        for _ in range(8):
            fcs = ((fcs << 1) ^ genpoly) if ((fcs ^ data) & 0x400) else (fcs << 1)
            fcs &= 0x7FF
            data <<= 1
    return fcs


def encode_imb(tracking: str, routing: str) -> str:
    """跟踪码(20 位) + 路由码(0/5/9/11 位) → 65 字符 ADFT 串。"""
    tracking = re_digits(tracking).zfill(20)[:20]
    routing = re_digits(routing)
    if len(routing) not in ROUTING_OFFSET:
        raise ValueError(f"路由码长度非法: {len(routing)}")
    b = int(routing) + ROUTING_OFFSET[len(routing)]
    b = b * 10 + int(tracking[0])
    b = b * 5 + int(tracking[1])
    binary = int(str(b) + tracking[2:])
    hexcode = format(binary, "x").zfill(26)
    barr = [int(hexcode[i:i + 2], 16) for i in range(0, 26, 2)]
    fcs = _crc11(barr)
    # PHP (x<<2)>>2 = 低 6 位
    data = int(format(barr[0] & 0x3F, "02x") + hexcode[2:], 16)
    cws = [data % 636 * 2]
    data //= 636
    for i in range(1, 9):
        cws.append(data % 1365)
        data //= 1365
    cws.append(data)
    if (fcs >> 10) & 1:
        cws[9] += 659
    chars = []
    for k, val in enumerate(cws):
        chrcode = T5[val] if val <= 1286 else T2[val - 1287]
        if fcs & (512 >> k):
            chrcode = (~chrcode) & 8191
        chars.append(chrcode)
    chars.reverse()                       # 关键：条形查表用反转序
    out = []
    for i in range(65):
        asc = chars[ASC_CHR[i]] & (1 << ASC_POS[i])
        dsc = chars[DSC_CHR[i]] & (1 << DSC_POS[i])
        out.append("F" if (asc and dsc) else "A" if asc else "D" if dsc else "T")
    return "".join(out)


def decode_imb(bc: str):
    """65 字符 ADFT → (跟踪码, 路由码)。失败返回 None。"""
    bc = bc.upper().strip()
    if len(bc) != 65 or any(c not in "ADFT" for c in bc):
        return None
    post = [0] * 10
    for i in range(65):
        if bc[i] in "AF":
            post[ASC_CHR[i]] |= 1 << ASC_POS[i]
        if bc[i] in "DF":
            post[DSC_CHR[i]] |= 1 << DSC_POS[i]
    pre = post[::-1]
    for fcs in range(2048):
        c = pre[:]
        for k in range(10):
            if (fcs >> (9 - k)) & 1:
                c[k] ^= 0x1FFF
        cws, ok = [], True
        for v in c:
            if v in INV5:
                cws.append(INV5[v])
            elif v in INV2:
                cws.append(INV2[v] + 1287)
            else:
                ok = False
                break
        if not ok or cws[0] % 2 or cws[0] // 2 >= 636:
            continue
        cw9 = cws[9]
        if (fcs >> 10) & 1:
            if cw9 < 659 or cw9 - 659 > 1364:
                continue
            cw9 -= 659
        elif cw9 > 1364:
            continue
        cws[9] = cw9
        data = cw9
        for k in range(8, 0, -1):
            data = data * 1365 + cws[k]
        data = data * 636 + cws[0] // 2
        if data >= (1 << 102):
            continue
        barr = list(data.to_bytes(13, "big"))
        for top in range(4):
            barr2 = barr[:]
            barr2[0] = (barr2[0] & 0x3F) | (top << 6)
            if _crc11(barr2) == fcs:
                return _split_payload(int.from_bytes(barr2, "big"))
    return None


def _split_payload(binary):
    s = str(binary).zfill(19)
    tail = s[-18:]
    head = int(s[:-18])
    t1 = head % 5
    tmp = head // 5
    t0 = tmp % 10
    r_off = tmp // 10
    tracking = f"{t0}{t1}{tail}"
    for rl, off in ROUTING_OFFSET.items():
        r = r_off - off
        if 0 <= r and len(str(r)) <= rl:
            return tracking, str(r).zfill(rl) if rl else ""
    return tracking, str(r_off)


def re_digits(s):
    import re as _re
    return _re.sub(r"\D", "", str(s))


# ============ US Bank 派生模型（两原稿实证） ============
IMB_BARCODE_ID = "00"
IMB_STID = "260"
IMB_MAILER_ID = "106481"


def imb_tracking(serial9: str) -> str:
    return IMB_BARCODE_ID + IMB_STID + IMB_MAILER_ID + \
        re_digits(serial9).zfill(9)[:9]


def imb_routing(zip_text: str, street_line: str) -> str:
    """ZIP(+4) + 门牌号末 2 位 → 11 位路由码（53818161890 实证模型）。"""
    z = re_digits(zip_text)
    if len(z) < 5:
        z = z.zfill(5)
    import re as _re
    num = _re.search(r"(\d+)", str(street_line))
    dp = (num.group(1)[-2:] if num else "00").zfill(2)
    return (z[:5] + z[5:9].ljust(4, "0") + dp)[:11]
