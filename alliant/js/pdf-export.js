/* ============================================================
 * PDF 直接导出（不经浏览器打印）
 * ------------------------------------------------------------
 * 布局引擎的 ops 显示列表 → pdf-lib 逐图元绘制。字体声明与原稿
 * PDF 逐字段一致（js/font-decls.js，由 analysis/gen_font_decls.py
 * 自原稿生成）：
 *   Type1 + FontFile3/Type1C(Flate) 嵌入 —— EX_CFF_Helvetica、
 *     EX_CFF_Times-Roman、SCORPQ+Aileron-Regular、SCORPA+Aileron-Bold
 *     （Widths/FirstChar/FontDescriptor 镜像原稿；Aileron Widths 按重建字体）
 *   TrueType 不嵌入（Widths + WinAnsiEncoding）—— Arial-BoldMT、
 *     TimesNewRomanPS-BoldMT（后者声明未使用，与原稿一致）
 *   Type0/CIDFontType0 + CIDFontType0C —— MinionPro ×2（声明未使用，
 *     镜像原稿共享后代结构）
 * 文本以 WinAnsi 字节 + CFF 内建编码写入（BT/Tf/Tm/Tj/ET 原始算子）；
 * 目标字体缺的字符回退标准字体绘制。LOGO 为矢量路径，横幅/上传图嵌入
 * 位图，水印平铺，元数据写入 Info 字典（键集合与原稿一致）。
 * ============================================================ */
(function (global) {
  "use strict";

  const PAGE_H = 792;

  /* ---- WinAnsi: unicode → 码位 ---- */
  const WINANSI_U2C = (() => {
    const m = new Map();
    for (let c = 0x20; c <= 0x7e; c++) m.set(c, c);
    for (let c = 0xa1; c <= 0xff; c++) m.set(c, c);
    const sp = { 0x20AC: 0x80, 0x201A: 0x82, 0x0192: 0x83, 0x201E: 0x84, 0x2026: 0x85, 0x2020: 0x86,
      0x2021: 0x87, 0x02C6: 0x88, 0x2030: 0x89, 0x0160: 0x8a, 0x2039: 0x8b, 0x0152: 0x8c, 0x017D: 0x8e,
      0x2018: 0x91, 0x2019: 0x92, 0x201C: 0x93, 0x201D: 0x94, 0x2022: 0x95, 0x2013: 0x96, 0x2014: 0x97,
      0x02DC: 0x98, 0x2122: 0x99, 0x0161: 0x9a, 0x203A: 0x9b, 0x0153: 0x9c, 0x017E: 0x9e, 0x0178: 0x9f };
    for (const u of Object.keys(sp)) m.set(+u, sp[u]);
    return m;
  })();

  const hex2rgb = hex => {
    let h = String(hex || "#000").replace("#", "");
    if (h.length === 3) h = h.split("").map(c => c + c).join("");
    const n = parseInt(h, 16) || 0;
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  };

  async function fetchBin(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error("资源加载失败: " + url);
    return new Uint8Array(await r.arrayBuffer());
  }
  const b64Bytes = b64 => {
    const bin = atob(b64), u = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    return u;
  };
  /* 声明资产（*.cff.z）优先取内嵌 base64（file:// 场景），否则回退网络加载 */
  async function loadDeclBytes(key) {
    const emb = global.EMBEDDED_ASSETS && global.EMBEDDED_ASSETS.fonts[key];
    if (emb) return b64Bytes(emb);
    return fetchBin(key);
  }

  function rasterizeSvg(dataUrl) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1200 / Math.max(1, img.width), 4);
        const cv = document.createElement("canvas");
        cv.width = Math.max(1, Math.round(img.width * scale));
        cv.height = Math.max(1, Math.round(img.height * scale));
        cv.getContext("2d").drawImage(img, 0, 0, cv.width, cv.height);
        cv.toBlob(b => b ? resolve(b.arrayBuffer()) : reject(new Error("SVG 栅格化失败")), "image/png");
      };
      img.onerror = () => reject(new Error("图片加载失败"));
      img.src = dataUrl;
    });
  }
  async function imageBytes(src) {
    if (/^data:image\/svg/i.test(src)) {
      const ab = await rasterizeSvg(src);
      return { bytes: new Uint8Array(ab), png: true };
    }
    const emb = global.EMBEDDED_ASSETS && global.EMBEDDED_ASSETS.images[src];
    if (emb) { const b = b64Bytes(emb); return { bytes: b, png: b[0] === 0x89 && b[1] === 0x50 }; }
    const bytes = await fetchBin(src);
    return { bytes, png: bytes[0] === 0x89 && bytes[1] === 0x50 };
  }

  async function build(data, opts) {
    opts = opts || {};
    const { PDFDocument, rgb, StandardFonts, PDFName, PDFNumber, PDFRawStream, PDFHexString,
            PDFOperator, PDFOperatorNames } = PDFLib;
    const wmCfg = (opts.watermark === null || opts.watermark === false)
      ? null : Object.assign({ text: "SAMPLE", color: "#0F6191", opacity: 0.10 }, opts.watermark || {});

    const out = StatementEngine.render(data, { watermark: wmCfg });
    const doc = await PDFDocument.create();
    const ctx = doc.context;
    const D = global.FONT_DECLS;
    const famKey = fam => { const m = /'([^']+)'/.exec(fam || ""); return m ? m[1] : String(fam || "").split(",")[0].trim(); };
    const declKeyFor = fam => (famKey(fam) in D.fonts) ? famKey(fam) : "Arial-BoldMT";

    /* ---------- 字体对象（字典结构与原稿逐字段一致） ---------- */
    const built = {};
    async function buildFont(key) {
      if (built[key]) return built[key];
      const decl = D.fonts[key];
      const dictObj = ctx.obj({});
      let ffRef = null;
      if (decl.fontFile) {
        const bytes = await loadDeclBytes(decl.fontFile);
        ffRef = ctx.register(PDFRawStream.of(
          ctx.obj({ Length: bytes.length, Filter: "FlateDecode", Subtype: "Type1C" }), bytes));
      }
      const { PDFString } = PDFLib;
      const fd = { Type: "FontDescriptor", FontName: PDFName.of(decl.desc.FontName || decl.baseFont) };
      for (const k of ["Flags", "ItalicAngle", "Ascent", "Descent", "CapHeight", "StemV", "XHeight", "FontWeight"])
        if (decl.desc[k] != null) fd[k] = decl.desc[k];
      fd.FontBBox = decl.desc.FontBBox || [0, 0, 0, 0];
      if (decl.desc.FontFamily) fd.FontFamily = PDFString.of(decl.desc.FontFamily);
      if (decl.desc.FontStretch) fd.FontStretch = PDFName.of(decl.desc.FontStretch);
      if (decl.desc.CharSet) fd.CharSet = PDFString.of(decl.desc.CharSet);
      if (ffRef) fd.FontFile3 = ffRef;
      const fdRef = ctx.register(ctx.obj(fd));
      dictObj.set(PDFName.of("Type"), PDFName.of("Font"));
      dictObj.set(PDFName.of("Subtype"), PDFName.of(decl.subtype));
      dictObj.set(PDFName.of("BaseFont"), PDFName.of(decl.baseFont));
      if (decl.encoding) dictObj.set(PDFName.of("Encoding"), PDFName.of(decl.encoding));
      dictObj.set(PDFName.of("FirstChar"), PDFNumber.of(decl.firstChar));
      dictObj.set(PDFName.of("LastChar"), PDFNumber.of(decl.lastChar));
      dictObj.set(PDFName.of("Widths"), ctx.obj(decl.widths));
      dictObj.set(PDFName.of("FontDescriptor"), fdRef);
      built[key] = { ref: ctx.register(dictObj), decl,
        codes: new Set(decl.codes),
        widthOf: (code, size) => decl.widths[code - decl.firstChar] / 1000 * size };
      return built[key];
    }

    /* Minion ×2：声明未使用，镜像原稿共享后代结构 */
    async function buildMinionPair() {
      if (built.__minion) return built.__minion;
      const M = D.minion;
      const mk = async (key, subtype) => {
        const bytes = await loadDeclBytes(M.files[key]);
        const d = { Length: bytes.length, Filter: "FlateDecode" };
        if (subtype) d.Subtype = subtype;
        return ctx.register(PDFRawStream.of(ctx.obj(d), bytes));
      };
      const ffRef = await mk("cff", "CIDFontType0C");
      const cidSetRef = await mk("cidSet", null);
      const tuRef = await mk("toUnicode", null);
      const fd = { Type: "FontDescriptor", FontName: PDFName.of(M.desc.FontName || M.descendantBaseFont) };
      for (const k of ["Flags", "ItalicAngle", "Ascent", "Descent", "CapHeight", "StemV", "XHeight", "FontWeight"])
        if (M.desc[k] != null) fd[k] = M.desc[k];
      fd.FontBBox = M.desc.FontBBox || [0, 0, 0, 0];
      if (M.desc.FontFamily) fd.FontFamily = PDFLib.PDFString.of(M.desc.FontFamily);
      if (M.desc.FontStretch) fd.FontStretch = PDFName.of(M.desc.FontStretch);
      fd.FontFile3 = ffRef; fd.CIDSet = cidSetRef;
      const fdRef = ctx.register(ctx.obj(fd));
      const wArr = ctx.obj(M.w);
      const cidRef = ctx.register(ctx.obj({
        Type: "Font", Subtype: "CIDFontType0", BaseFont: M.descendantBaseFont,
        CIDSystemInfo: ctx.obj({ Registry: "Adobe", Ordering: "Identity", Supplement: 0 }),
        DW: M.dw, W: wArr, FontDescriptor: fdRef }));
      built.__minion = [M.base, M.base2].map(b => ctx.register(ctx.obj({
        Type: "Font", Subtype: "Type0", BaseFont: b,
        DescendantFonts: ctx.obj([cidRef]), Encoding: "Identity-H", ToUnicode: tuRef })));
      return built.__minion;
    }

    /* 标准回退字体（目标字体缺字符时逐字使用；不缺则不进页面资源） */
    const std = {
      helv: await doc.embedFont(StandardFonts.Helvetica),
      helvBold: await doc.embedFont(StandardFonts.HelveticaBold),
      times: await doc.embedFont(StandardFonts.TimesRoman),
    };
    const stdFor = (fam, weight) =>
      /Times/i.test(famKey(fam)) ? (weight >= 700 ? std.helvBold : std.times) : (weight >= 700 ? std.helvBold : std.helv);

    /* ---------- WinAnsi 分段绘制（缺字回退标准字体） ---------- */
    const hexOf = bytes => {
      let s = "";
      for (const b of bytes) s += b.toString(16).padStart(2, "0");
      return PDFHexString.of(s.toUpperCase());
    };
    function segList(s, key) {
      /* 返回段列表：
       *   {codes:[..], w}      声明内码位（/Widths 有值）——批量 Tj
       *   {per:[{code,w}], w}  声明外但字体有字形（Aileron 补齐码位）——逐字符定位
       *   {chs:[..], fb}       字体缺字形——回退标准字体
       *   {w, sp}              空格声明外步进（Aileron-Bold FirstChar=46）
       */
      const B = built[key];
      const full = new Set(B.decl.codesFull || []);
      const wFull = code => (B.decl.widthsFull || [])[code - (B.decl.codesFull ? 32 : 0)] || 0;
      const segs = [];
      let cur = null;
      const push = seg => { segs.push(seg); cur = seg; };
      for (const ch of s) {
        const cp = ch.codePointAt(0);
        const code = WINANSI_U2C.get(cp);
        if (code != null && B.codes.has(code)) {
          if (!cur || !cur.codes) push({ codes: [], w: 0 });
          cur.codes.push(code); cur.w += B.widthOf(code, 1);
        } else if (code != null && full.has(code)) {
          if (!cur || !cur.per) push({ per: [], w: 0 });
          const w = wFull(code) / 1000;
          cur.per.push({ code, w }); cur.w += w;
        } else if (cp === 32 && B.decl.spaceWidth != null) {
          if (!cur || !cur.sp) push({ w: 0, sp: true });
          cur.w += B.decl.spaceWidth / 1000;
        } else {
          if (!cur || !cur.fb) push({ chs: [], fb: true });
          cur.chs.push(ch);
        }
      }
      return segs;
    }
    function drawSegments(page, resName, x, y, s, key, weight, size, color) {
      let cx = x;
      const c = hex2rgb(color);
      const rgbOps = () => PDFOperator.of(PDFOperatorNames.NonStrokingColorRgb,
        [PDFNumber.of(c[0] / 255), PDFNumber.of(c[1] / 255), PDFNumber.of(c[2] / 255)]);
      for (const seg of segList(s, key)) {
        if (seg.sp) { cx += seg.w * size; continue; }
        if (seg.per) {                        // 声明外码位：本字体逐字符绝对定位
          for (const g of seg.per) {
            page.pushOperators(
              rgbOps(),
              PDFOperator.of(PDFOperatorNames.BeginText),
              PDFOperator.of(PDFOperatorNames.SetFontAndSize, [resName, PDFNumber.of(size)]),
              PDFOperator.of(PDFOperatorNames.SetTextMatrix,
                [PDFNumber.of(1), PDFNumber.of(0), PDFNumber.of(0), PDFNumber.of(1),
                 PDFNumber.of(cx), PDFNumber.of(PAGE_H - y)]),
              PDFOperator.of(PDFOperatorNames.ShowText, [hexOf(new Uint8Array([g.code]))]),
              PDFOperator.of(PDFOperatorNames.EndText));
            cx += g.w * size;
          }
        } else if (!seg.fb) {
          page.pushOperators(
            rgbOps(),
            PDFOperator.of(PDFOperatorNames.BeginText),
            PDFOperator.of(PDFOperatorNames.SetFontAndSize, [resName, PDFNumber.of(size)]),
            PDFOperator.of(PDFOperatorNames.SetTextMatrix,
              [PDFNumber.of(1), PDFNumber.of(0), PDFNumber.of(0), PDFNumber.of(1),
               PDFNumber.of(cx), PDFNumber.of(PAGE_H - y)]),
            PDFOperator.of(PDFOperatorNames.ShowText, [hexOf(new Uint8Array(seg.codes))]),
            PDFOperator.of(PDFOperatorNames.EndText));
          cx += seg.w * size;
        } else {
          const f = stdFor(key, weight);
          const txt = seg.chs.join("");
          let w = 0;
          try {
            w = f.widthOfTextAtSize(txt, size);
            page.drawText(txt, { x: cx, y: PAGE_H - y, size, font: f,
              color: rgb(c[0] / 255, c[1] / 255, c[2] / 255) });
          } catch (e) { }
          cx += w;
        }
      }
      return cx - x;
    }
    function runWidth(s, key, weight, size) {
      let w = 0;
      for (const seg of segList(s, key))
        w += seg.fb ? safeWidth(stdFor(key, weight), seg.chs.join(""), size) : seg.w * size;
      return w;
    }
    function safeWidth(f, s, size) {
      try { return f.widthOfTextAtSize(s, size); } catch (e) { return 0; }
    }

    /* ---------- 水印（260×180pt 平铺，−32°，34pt，字距 10，EX 字体） ---------- */
    function drawWatermark(page, resName, wm) {
      const B = built["EX_CFF_Helvetica"];
      const c = hex2rgb(wm.color);
      const size = 34, spacing = 10;
      const chars = [...String(wm.text)];
      const widths = chars.map(ch => {
        const code = WINANSI_U2C.get(ch.codePointAt(0));
        return (code != null && B.codes.has(code)) ? B.widthOf(code, size) : 0;
      });
      const total = widths.reduce((s, w) => s + w, 0) + spacing * Math.max(0, chars.length - 1);
      const a = 32 * Math.PI / 180;
      const dx = Math.cos(a), dy = Math.sin(a);
      const gsRef = ctx.register(ctx.obj({ Type: "ExtGState", ca: wm.opacity, CA: wm.opacity }));
      const gsName = page.node.newExtGState(page.node.newExtGStateKey(), gsRef);
      const ops = [
        PDFOperator.of(PDFOperatorNames.PushGraphicsState),
        PDFOperator.of(PDFOperatorNames.SetGraphicsStateParams, [gsName]),
        PDFOperator.of(PDFOperatorNames.NonStrokingColorRgb,
          [PDFNumber.of(c[0] / 255), PDFNumber.of(c[1] / 255), PDFNumber.of(c[2] / 255)]),
      ];
      for (let ty = 0; ty < PAGE_H; ty += 180) {
        for (let tx = 0; tx < 612; tx += 260) {
          const cx = tx + 130 + 12 * Math.sin(a);
          const cy = PAGE_H - (ty + 90) - 12 * Math.cos(a);
          let px = cx - (total / 2) * dx, py = cy - (total / 2) * dy;
          for (let i = 0; i < chars.length; i++) {
            const code = WINANSI_U2C.get(chars[i].codePointAt(0));
            if (code != null && B.codes.has(code) && widths[i] > 0) {
              ops.push(
                PDFOperator.of(PDFOperatorNames.BeginText),
                PDFOperator.of(PDFOperatorNames.SetFontAndSize, [resName, PDFNumber.of(size)]),
                PDFOperator.of(PDFOperatorNames.SetTextMatrix,
                  [PDFNumber.of(1), PDFNumber.of(0), PDFNumber.of(0), PDFNumber.of(1),
                   PDFNumber.of(px), PDFNumber.of(py)]),
                PDFOperator.of(PDFOperatorNames.ShowText, [hexOf(new Uint8Array([code]))]),
                PDFOperator.of(PDFOperatorNames.EndText));
            }
            px += dx * (widths[i] + spacing); py += dy * (widths[i] + spacing);
          }
        }
      }
      ops.push(PDFOperator.of(PDFOperatorNames.PopGraphicsState));
      page.pushOperators(...ops);
    }

    /* ---------- 披露段落：两端对齐 + 悬挂缩进 ---------- */
    function drawPara(page, resName, op) {
      const key = declKeyFor(op.f);
      const spaceW = runWidth(" ", key, 400, op.z);
      const ws = op.ws || 0;
      const indent = op.indent || 0;
      const isBullet = indent > 0;
      op.lines.forEach((line, li) => {
        const words = line.split(/\s+/).filter(Boolean);
        if (!words.length) return;
        const gaps = words.length - 1;
        const nat = words.reduce((s, w) => s + runWidth(w, key, 400, op.z), 0)
          + gaps * (spaceW + ws);
        const isLast = li === op.lines.length - 1;
        /* 原稿行为：非末行两端对齐到段宽；项目符号段的续行为自然排（实测） */
        const justify = !isLast && !(isBullet && li > 0);
        const avail = li === 0 ? (op.avail != null ? op.avail : op.restAvail) : op.restAvail;
        const extra = (justify && gaps > 0) ? Math.max(0, (avail - nat) / gaps) : 0;
        let x = op.x + (li === 0 ? indent : 0);
        const y = op.first + li * op.lineH;
        for (const w of words) {
          drawSegments(page, resName, x, y, w, key, 400, op.z, op.c);
          x += runWidth(w, key, 400, op.z) + spaceW + ws + extra;
        }
      });
    }

    /* ---------- 图像缓存 ---------- */
    const cache = {};
    async function embedImage(src) {
      if (cache[src]) return cache[src];
      const { bytes, png } = await imageBytes(src);
      cache[src] = png ? await doc.embedPng(bytes) : await doc.embedJpg(bytes);
      return cache[src];
    }

    /* ---------- 逐页绘制（与预览相同顺序） ---------- */
    let firstPage = true;
    for (const pg of out.pages) {
      const page = doc.addPage([612, PAGE_H]);
      /* 首页资源引用横幅位图但不绘制（镜像原稿：xref41 挂在两页资源、仅第 2 页绘制） */
      if (firstPage) {
        firstPage = false;
        try {
          const src = (data.reporting && data.reporting.promoUrl) || "assets/promo-logo.jpg";
          const emb = await embedImage(src);
          page.node.setXObject(page.node.newXObjectKey(), emb.ref);
        } catch (e) { }
      }
      /* 收集本页用到的字体族并注册资源名 */
      const used = new Set();
      for (const op of pg.ops) {
        if (op.t === "text") used.add(declKeyFor(op.f));
        else if (op.t === "para") used.add(declKeyFor(op.f));
        else if (op.t === "chars") used.add(declKeyFor(op.f));
        else if (op.t === "wm") used.add("EX_CFF_Helvetica");
      }
      /* 声明未使用字体（与原稿资源声明一致） */
      used.add("TimesNewRomanPS-BoldMT");
      const resNames = {};
      for (const key of used) {
        const B = await buildFont(key);
        resNames[key] = page.node.newFontDictionary(page.node.newFontDictionaryKey(), B.ref);
      }
      const minionRefs = await buildMinionPair();
      for (const r of minionRefs) page.node.newFontDictionary(page.node.newFontDictionaryKey(), r);

      for (const op of pg.ops) {
        if (op.t === "text") {
          drawSegments(page, resNames[declKeyFor(op.f)], op.x, op.y, op.s, declKeyFor(op.f), op.w, op.z, op.c);
        } else if (op.t === "chars") {
          /* 固化块：原稿逐字符坐标，逐字符绝对定位绘制 */
          const key = declKeyFor(op.f);
          for (const it of op.items) {
            if (it.c === " ") continue;
            drawSegments(page, resNames[key], it.x, it.y, it.c, key, op.w, op.z, op.c);
          }
        } else if (op.t === "rect") {
          page.drawRectangle({
            x: op.x, y: PAGE_H - op.y - op.h, width: op.w, height: op.h,
            color: rgb(...hex2rgb(op.c).map(v => v / 255)),
          });
        } else if (op.t === "img") {
          try {
            const emb = await embedImage(op.src);
            page.drawImage(emb, { x: op.x, y: PAGE_H - op.y - op.h, width: op.w, height: op.h });
          } catch (e) { console.warn("图像嵌入跳过:", op.src, e.message); }
        } else if (op.t === "logoVec") {
          if (global.LOGO_PATHS) {
            for (const p of global.LOGO_PATHS) {
              page.drawSvgPath(p.d, { x: 0, y: PAGE_H, scale: 1, color: rgb(...hex2rgb(p.fill).map(v => v / 255)) });
            }
          }
        } else if (op.t === "para") {
          drawPara(page, resNames[declKeyFor(op.f)], op);
        } else if (op.t === "wm") {
          drawWatermark(page, resNames["EX_CFF_Helvetica"], op);
        }
      }
    }

    /* ---------- 元数据（键集合与原稿一致；CreationDate 为原稿明文格式） ---------- */
    const meta = data.meta || {};
    const info = ctx.lookup(ctx.trailerInfo.Info);
    const fmtOrig = iso => {
      const d = new Date(iso);
      if (isNaN(d)) return String(iso);
      const p = n => String(n).padStart(2, "0");
      return `${d.getMonth() + 1}/${d.getDate()}/${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    };
    if (info) {
      const K = PDFName.of.bind(PDFName);
      const set = (k, v) => info.set(K(k), PDFLib.PDFString.of(v));
      set("Title", meta.title || "");
      set("Author", meta.author || "");
      set("Creator", meta.creator || "");
      set("CreationDate", fmtOrig(String(meta.creationDate || "").trim()
          || defaultCreation(data.statement.periodEnd)));
      if (meta.subject) set("Subject", meta.subject);
      if (meta.keywords) set("Keywords", meta.keywords);
      for (const k of ["Producer", "ModDate", "Subject", "Keywords"])
        if (!(meta.subject && k === "Subject") && !(meta.keywords && k === "Keywords")) info.delete(K(k));
    }

    return await doc.save();
  }

  /* 触发浏览器下载 */
  async function download(data, opts, fileName) {
    const bytes = await build(data, opts);
    const blob = new Blob([bytes], { type: "application/pdf" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = fileName || "statement.pdf";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    return bytes;
  }

  /* 默认创建时间：Statement Period 止（MM/DD/YY 或四位年）次日凌晨 7-8 点随机
     （[7,8) 小时恒 07），无时区 ISO 串（fmtOrig 会转成原稿明文格式） */
  const defaultCreation = pe => {
    const m = String(pe || "").match(/(\d{1,2})\/(\d{1,2})\/(\d{2,4})/);
    if (!m) return "";
    let y = +m[3]; if (y < 100) y += 2000;
    const d = new Date(Date.UTC(y, +m[1] - 1, +m[2]) + 86400000);
    const p = n => String(n).padStart(2, "0");
    /* 直接产出原稿明文格式（M/D/YYYY HH:MM:SS，无时区）：字段=数据=导出三者一致；
       fmtOrig 对该格式 round-trip 无损（new Date 可解析 M/D/YYYY HH:MM:SS） */
    return `${d.getUTCMonth() + 1}/${d.getUTCDate()}/${d.getUTCFullYear()}` +
           ` 07:${p(Math.floor(Math.random() * 60))}:${p(Math.floor(Math.random() * 60))}`;
  };
  global.PDFExporter = { build, download, defaultCreation };
})(window);
