/* ============================================================
 * 账单模板布局引擎（复刻自原稿实测坐标；单位 pt；基线定位）
 * ------------------------------------------------------------
 * 页面 612×792 (US Letter)。文本以基线定位：
 * CSS ascent-override:75% + line-height:1 ⇒ 基线 = 盒顶 + 0.75×字号。
 * 增删流水时的联动：计数/合计/余额/期末/汇总面板/年度合计自动重算，
 * 行高与分页自动重排（行高 = max(10.32, 9.12×行数)，描述自动折行）。
 * ============================================================ */
(function (global) {
  "use strict";

  /* ---------- 版面常量（实测自原稿） ---------- */
  const P = {
    PAGE_W: 612, PAGE_H: 792,
    LEFT: 18, RIGHT: 594.7, RIGHT2: 595,
    BOTTOM: 756,                 // 页面内容下界（792 − 36）
    TOP1: 270,                   // 首页首个区段顶（固定，原稿 270）
    TOPN: 79.2,                  // 续页内容顶（原稿第 2 页蓝条位置）
    SEC_GAP: 10.8,               // 区段间距（原稿 515.8→526.6）
    COLS: [18, 75.6, 421.2, 478.8, 536.4, 594.7],
    STRIP_COLS: [18, 101, 192.2, 274.6, 354, 433.2, 512.6, 594.7],
    INFO_CELLS: [378, 450, 536.4, 594],
    F_EX: "'EX_CFF_Helvetica', Helvetica, Arial, sans-serif",
    F_AB: "'Arial-BoldMT', Arial, sans-serif",
    F_AR: "'Aileron-Regular', 'Aileron-Web', Helvetica, sans-serif",
    F_ABold: "'Aileron-Bold', 'Aileron-Web', Helvetica, sans-serif",
    F_TR: "'EX_CFF_Times-Roman', 'Times New Roman', Times, serif",
    BLUE: "#0F6191", GRAY: "#F5F7F7", SEP: "#C0C0C0", BLACK: "#000", DARK: "#231F20",
    ASC: 0.75,
  };
  const RH = {
    BAR: 14.4, STRIP: 23, VALUES: 14.4, HEADER: 15.85, TICKS: 1.45,
    STACK: 14.4 + 23 + 14.4 + 15.85 + 1.45,   // 69.1：蓝条顶 → 表体首行顶
    BEGIN: 9.35, END: 10.08, LINE: 9.12,
    /* 行高规律（原稿实测）: 单行跟单行=10.08；单行跟多行/BEGIN=10.32；n 行(n≥2)=9.12n */
    rowH: (n, prevSingle) => n === 1 ? (prevSingle ? 10.08 : 10.32) : 9.12 * n,
  };
  const DESC_X = 82.8, DESC_X2 = 100.8, DESC_R = 421.2;   // 描述列起止

  /* ---------- 工具 ---------- */
  const esc = s => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const r2 = n => Math.round((Number(n) + Number.EPSILON) * 100) / 100;
  function fmtAmt(n) {
    const v = r2(n), neg = v < 0;
    return (neg ? "-" : "") + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  const fmtUSD = n => "$" + fmtAmt(n);
  const fmtUSDSpace = n => "$ " + fmtAmt(n);

  let _cv = null, _mk = "";
  function _ctx(fam, size, weight) {
    if (!_cv) _cv = document.createElement("canvas").getContext("2d");
    const k = weight + "|" + size + "|" + fam;
    if (k !== _mk) { _cv.font = `${weight ? weight + " " : ""}${size}px ${fam}`; _mk = k; }
    return _cv;
  }
  const tw = (t, fam, size, weight) => _ctx(fam, size, weight).measureText(t).width;

  function wrap(text, firstW, restW, fam, size, weight, ws) {
    ws = ws || 0;
    const tokens = String(text).split(/(\s+)/);
    const lines = [];
    let cur = "", curW = 0, avail = firstW;
    const spw = tk => tw(tk, fam, size, weight) + ws * (tk.split(" ").length - 1);
    for (const tk of tokens) {
      if (!tk) continue;
      if (/^\s+$/.test(tk)) { cur += tk; curW += spw(tk); continue; }
      const w = tw(tk, fam, size, weight);
      if (cur.replace(/\s+$/, "") !== "" && curW + w > avail) {
        lines.push(cur.replace(/\s+$/, ""));
        cur = tk; curW = w; avail = restW;
      } else { cur += tk; curW += w; }
    }
    if (cur.replace(/\s+$/, "") !== "" || lines.length === 0) lines.push(cur.replace(/\s+$/, ""));
    return lines;
  }

  /* ---------- ops 记录器（PDF 直接导出用的显示列表） ----------
   * 渲染 HTML 的同时把每个图元记录为平台无关的 op；PDF 导出器按同一次序绘制，
   * 与预览的绘制顺序/坐标完全一致。坐标均为页面 pt、y 向下、文本为基线。 */
  let _OPS = null;                            // null = 当前未在记录
  const rec = op => { if (_OPS) _OPS.push(op); };

  /* ---------- HTML 片段 ---------- */
  const rect = (x, y, w, h, fill, extra) => {
    rec({ t: "rect", x, y, w, h, c: fill });
    return `<div class="rect" style="left:${x}pt;top:${y}pt;width:${w}pt;height:${h}pt;background:${fill};${extra || ""}"></div>`;
  };
  const hline = (x1, x2, y, c, w) => {
    rec({ t: "rect", x: x1, y: y - w / 2, w: x2 - x1, h: w, c });
    return `<div class="ln" style="left:${x1}pt;top:${y - w / 2}pt;width:${x2 - x1}pt;height:${w}pt;background:${c}"></div>`;
  };
  const vline = (x, y1, y2, c, w) => {
    rec({ t: "rect", x: x - w / 2, y: y1, w, h: y2 - y1, c });
    return `<div class="ln" style="left:${x - w / 2}pt;top:${y1}pt;width:${w}pt;height:${y2 - y1}pt;background:${c}"></div>`;
  };

  function T(baseline, text, o) {
    const size = o.size, weight = o.weight || 400;
    const width = tw(text, o.fam, size, weight);
    let left;
    if (o.right != null) left = o.right - width;
    else if (o.center != null) left = o.center - width / 2;
    else left = o.x;
    const top = baseline - P.ASC * size;
    rec({ t: "text", x: left, y: baseline, s: text, f: o.fam, z: size, w: weight, c: o.color });
    return `<div class="txt" style="left:${left}pt;top:${top}pt;${weight !== 400 ? "font-weight:" + weight + ";" : ""}font-family:${o.fam};font-size:${size}pt;line-height:1;color:${o.color};${o.extra || ""}">${esc(text)}</div>`;
  }

  function watermarkHTML(wm) {
    if (wm === null || wm === false || (wm && wm.text === "")) return "";
    const w = Object.assign({ text: "SAMPLE", color: "#0F6191", opacity: 0.10 }, wm || {});
    rec({ t: "wm", text: w.text, color: w.color, opacity: w.opacity });
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="260" height="180"><g transform="rotate(-32 130 90)"><text x="130" y="102" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-weight="700" font-size="34" letter-spacing="10" fill="${w.color}" fill-opacity="${w.opacity}">${esc(w.text)}</text></g></svg>`;
    const url = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
    return `<div class="watermark" style="background-image:url('${url}');background-size:260pt 180pt"></div>`;
  }


  /* ---------- 固化文本块（js/baked-lines.js，自原稿逐字符坐标） ----------
   * 文档标题 / 机构地址行1 / 披露区段：逐字符绝对定位渲染，与原稿逐点一致；
   * 编辑器中锁定不可编辑。 */
  const BAKED_FAM = {
    "EX_CFF_Helvetica": null, "Arial-BoldMT": null, "Aileron-Regular": null,
  };
  function bakedFamCSS(fam) {
    if (fam === "Arial-BoldMT") return { css: P.F_AB, weight: 700 };
    if (fam === "Aileron-Regular") return { css: P.F_AR, weight: 400 };
    return { css: P.F_EX, weight: 400 };
  }
  function bakedText(b, yAbs) {
    const f = bakedFamCSS(b.fam);
    let h = "";
    const items = [];
    for (const ch of b.chars) {
      if (ch.c === " ") continue;          // 空格无墨水，字符坐标已含间距
      h += `<div class="txt" style="left:${ch.x}pt;top:${yAbs - P.ASC * b.size}pt;${f.weight !== 400 ? "font-weight:" + f.weight + ";" : ""}font-family:${f.css};font-size:${b.size}pt;line-height:1;color:${b.color};">${esc(ch.c)}</div>`;
      items.push({ c: ch.c, x: ch.x, y: yAbs });
    }
    rec({ t: "chars", f: f.css, z: b.size, w: f.weight, c: b.color, items });
    return h;
  }

  /* ============================================================
   * 一阶段：数据推导（联动计算）
   * ============================================================ */
  function derive(data) {
    const accounts = [];
    let ytd = 0;
    for (const acc of data.accounts) {
      const rows = [];
      let bal = r2(acc.beginning), totDep = 0, totWd = 0, nDep = 0, nWd = 0;
      for (const t of acc.transactions || []) {
        const amt = r2(Math.abs(t.amount || 0));
        if (t.dir === "withdrawal") { bal = r2(bal - amt); totWd = r2(totWd + amt); nWd++; }
        else { bal = r2(bal + amt); totDep = r2(totDep + amt); nDep++; }
        rows.push({ t, lines: wrap(t.desc, DESC_R - DESC_X, DESC_R - DESC_X2, P.F_EX, 8), bal });
      }
      const noteLines = acc.note ? wrap(acc.note, DESC_R - DESC_X, DESC_R - DESC_X2, P.F_EX, 8) : [];
      const ytdV = parseFloat(acc.ytdDividends);
      if (!isNaN(ytdV)) ytd += ytdV;
      accounts.push({ src: acc, rows, nDep, nWd, totDep, totWd, beginning: r2(acc.beginning), ending: bal, noteLines });
    }
    return { data, accounts, ytdTotal: r2(ytd) };
  }

  /* ============================================================
   * 二阶段：分页（把块直接挂到页对象上）
   * ============================================================ */
  function paginate(m, data) {
    const pages = [];
    let cur = null;
    const newPage = () => { cur = { first: pages.length === 0, blocks: [] }; pages.push(cur); };
    const push = b => cur.blocks.push(b);
    const fits = h => cur.y + h <= P.BOTTOM + 0.001;
    newPage();
    cur.y = P.TOP1;

    m.accounts.forEach((A, ai) => {
      // 区段头（蓝条..表头带 + 刻线 + BEGIN 行）必须整体放下
      if (!fits(RH.STACK + RH.BEGIN)) { newPage(); cur.y = P.TOPN; }
      push({ type: "secHead", A, y: cur.y });
      cur.y += RH.STACK;
      push({ type: "beginRow", A, y: cur.y, h: RH.BEGIN });
      cur.y += RH.BEGIN;

      // 流水行（行高取决于本行行数与上一行是否单行）
      let prevSingle = false;
      for (const r of A.rows) {
        const h = RH.rowH(r.lines.length, prevSingle);
        if (!fits(h)) {
          newPage(); cur.y = P.TOPN;
          push({ type: "repHead", A, y: cur.y });
          cur.y += RH.HEADER + RH.TICKS;
          prevSingle = false;
        }
        push({ type: "row", A, r, y: cur.y, h });
        cur.y += h;
        prevSingle = r.lines.length === 1;
      }
      // APY 备注行
      if (A.noteLines.length) {
        const h = RH.rowH(A.noteLines.length, prevSingle);
        if (!fits(h)) {
          newPage(); cur.y = P.TOPN;
          push({ type: "repHead", A, y: cur.y });
          cur.y += RH.HEADER + RH.TICKS;
        }
        push({ type: "note", A, y: cur.y, h });
        cur.y += h;
      }
      // ENDING 行
      if (!fits(RH.END)) {
        newPage(); cur.y = P.TOPN;
        push({ type: "repHead", A, y: cur.y });
        cur.y += RH.HEADER + RH.TICKS;
      }
      push({ type: "endRow", A, y: cur.y, h: RH.END });
      cur.y += RH.END;
      push({ type: "tableEnd", y: cur.y });
      const tableBottom = cur.y;

      // 披露框：固定跟随第 1 个账户区段（与原稿一致，顶部与表底线相连）
      if (ai === 0 && data.disclosure && data.disclosure.paragraphs && data.disclosure.paragraphs.length) {
        const dh = disclosureHeight(data.disclosure);
        if (!fits(dh)) {
          newPage(); cur.y = P.TOPN;
          push({ type: "disclosure", y: cur.y, h: Math.max(136.8, dh), attached: false });
        } else {
          push({ type: "disclosure", y: tableBottom, h: Math.max(136.8, dh), attached: true });
        }
        cur.y += Math.max(136.8, dh);
      }
      if (ai < m.accounts.length - 1 || data.reporting) cur.y += P.SEC_GAP;
    });

    if (data.reporting) {
      const rh = 14.4 + 50.4;
      if (!fits(rh)) { newPage(); cur.y = P.TOPN; }
      push({ type: "reporting", y: cur.y });
      cur.y += rh;
    }
    return pages;
  }

  /* 披露段 word-spacing：−1.0pt 时断行与原稿逐行一致（原稿为 Exstream 压缩字距排版）。
   * 固化模式（window.BAKED）：高度与文本渲染直接用原稿逐字符坐标。 */
  const DISC_WS = -1.0;
  function bakedDisclosureHeight() {
    const L = window.BAKED.disclosure.lines;
    return L[L.length - 1].y + 6.76;    // 末行基线 → 框底（原稿 136.8）
  }
  /* 披露框内容高度（游标模型：块间距 = 行进 9.36 + 3.84；末行基线 → 框底 6.76） */
  function disclosureLayout(disc) {
    const PARA_W = 546.3;
    const tLines = wrap(disc.heading, PARA_W, PARA_W, P.F_AB, 8, 700);
    let cursor = 13.4 + tLines.length * 9.36;   // 标题后“下一行基线”
    const paras = [];
    for (const para of disc.paragraphs || []) {
      const isB = para.startsWith("•");
      const txt = isB ? para.slice(1).trim() : para;
      /* 项目符号段 = 悬挂缩进：首行文字 38.88 起（宽 −9.36），续行回 29.52 全宽（原稿实测） */
      const lines = wrap(txt, isB ? PARA_W - 9.36 : PARA_W, PARA_W, P.F_EX, 8, 0, DISC_WS);
      const first = cursor + 3.84;
      paras.push({ isB, lines, first, text: txt });
      cursor = first + lines.length * 9.36;
    }
    return { headingLines: tLines, headingBaseline: 13.4, paras, height: cursor - 9.36 + 6.76 };
  }
  function disclosureHeight(disc) {
    if (window.BAKED && window.BAKED.disclosure) return bakedDisclosureHeight();
    return disclosureLayout(disc).height;
  }

  /* ============================================================
   * 三阶段：渲染
   * ============================================================ */
  function render(data, opts) {
    opts = opts || {};
    const m = derive(data);
    const pages = paginate(m, data);
    const total = pages.length;

    let html = "";
    pages.forEach((pg, pi) => {
      _OPS = [];
      html += `<div class="page" data-page="${pi + 1}">${watermarkHTML(opts.watermark)}<div class="content">`;
      html += headerHTML(m, data, pi + 1, total, pg.first);
      for (const b of pg.blocks) html += blockHTML(m, data, b);
      html += `</div></div>`;
      pg.ops = _OPS;
    });
    _OPS = null;
    return { html, pageCount: total, model: m, pages };
  }

  /* ---------- 页眉 ---------- */
  function headerHTML(m, data, pageNo, total, first) {
    const st = data.statement;
    let h = "";
    if (window.BAKED && window.BAKED.docTitle) h += bakedText(window.BAKED.docTitle, window.BAKED.docTitle.y);
    else h += T(24.72, st.docTitle || "Statement of Account ", { x: 420.48, fam: P.F_AB, size: 14, weight: 700, color: P.BLACK });
    const c = P.INFO_CELLS;
    for (let i = 0; i < 3; i++) h += rect(c[i], 36.2, c[i + 1] - c[i], 14.4, P.BLUE);
    ["Account Number", "Statement Period", "Page"].forEach((lab, i) =>
      h += T(46.08, lab, { center: (c[i] + c[i + 1]) / 2, fam: P.F_AB, size: 8, weight: 700, color: "#fff" }));
    h += hline(c[0], c[3], 50.6, P.BLUE, 0.24);
    h += vline(c[0], 50.6, 67.2, P.BLACK, 0.48);
    h += vline(c[3], 50.6, 67.2, P.BLACK, 0.48);
    h += hline(c[0], c[3], 67.2, P.BLACK, 0.48);
    const period = `${st.periodStart} thru ${st.periodEnd}`;
    h += T(61.68, String(st.accountNumber), { center: (c[0] + c[1]) / 2, fam: P.F_EX, size: 8, color: P.BLACK });
    h += T(61.68, period, { center: (c[1] + c[2]) / 2, fam: P.F_EX, size: 8, color: P.BLACK });
    h += T(61.92, `${pageNo} of ${total}`, { center: (c[2] + c[3]) / 2, fam: P.F_EX, size: 8, color: P.BLACK });
    if (!first) return h;

    const br = data.branding;
    // LOGO：默认为原稿矢量复刻（内联 SVG，52.24,1.28 起 189.48×43.52pt）；branding.logo 可覆盖
    if (br.logo) {
      rec({ t: "img", src: br.logo, x: 52.24, y: 1.28, w: 189.48, h: 43.52 });
      h += `<img class="logo" src="${br.logo}" onerror="this.style.display='none';" style="left:52.24pt;top:1.28pt;width:189.48pt;height:43.52pt;">`;
    } else if (window.LOGO_SVG) {
      rec({ t: "logoVec" });
      h += `<svg class="logo" xmlns="http://www.w3.org/2000/svg" viewBox="${window.LOGO_SVG.viewBox}" style="position:absolute;left:52.24pt;top:1.28pt;width:189.48pt;height:43.52pt;">${window.LOGO_SVG.paths}</svg>`;
    }
    const contactB = [55.72, 65.56, 75.43, 84.79, 94.78];   // 原稿实测基线
    [br.phone, br.website, br.inquiriesLabel, br.addrLine1, br.addrLine2].forEach((line, i) => {
      if (!line) return;
      if (i === 3 && window.BAKED && window.BAKED.addrLine1) { h += bakedText(window.BAKED.addrLine1, window.BAKED.addrLine1.y); return; }
      const bold = i === 1;
      h += T(contactB[i], line, { x: 73.7, fam: bold ? P.F_ABold : P.F_AR, size: 7.2, weight: bold ? 700 : 400, color: P.DARK });
    });
    const meta = data.meta || {};
    h += T(18.0, meta.tray || "", { x: 252, fam: P.F_TR, size: 10, color: "#ffffff", extra: "white-space:nowrap;" });
    h += T(18.0, meta.sheet || "", { x: 324, fam: P.F_TR, size: 10, color: "#ffffff", extra: "white-space:nowrap;" });

    // 账户汇总面板
    const px = 378, pr = 594;
    h += rect(px, 75.8, pr - px, 13, P.BLUE);
    h += T(85.92, "ACCOUNT BALANCE SUMMARY", { x: 385.2, fam: P.F_AB, size: 9, weight: 700, color: "#fff" });
    h += vline(px, 88.8, 99.6, P.BLACK, 0.48); h += vline(pr, 88.8, 99.6, P.BLACK, 0.48);
    h += T(98.88, "ID", { x: 385.2, fam: P.F_AB, size: 9, weight: 700, color: P.BLACK });
    h += T(98.88, "Account", { x: 405.36, fam: P.F_AB, size: 9, weight: 700, color: P.BLACK });
    h += T(98.64, "Balance", { x: 532.32, fam: P.F_AB, size: 9, weight: 700, color: P.BLACK });
    let rowTop = 99.6;
    m.accounts.forEach(A => {
      const b = rowTop + 10.8;
      h += T(b, String(A.src.id), { x: 385.2, fam: P.F_EX, size: 9, color: P.BLACK });
      h += T(b, A.src.name, { x: 405.36, fam: P.F_EX, size: 9, color: P.BLACK });
      h += T(b + 0.24, "$", { x: 532.32, fam: P.F_EX, size: 9, color: P.BLACK });
      h += T(b, fmtAmt(A.ending), { right: 590.37, fam: P.F_EX, size: 9, color: P.BLACK });
      rowTop += 11.52;
    });
    const panelBottom = rowTop - 11.52 + 10.8 + 6.48;   // 末行基线 + 6.48（原稿 128.4）
    h += vline(px, 99.6, panelBottom, P.BLACK, 0.48);
    h += vline(pr, 99.6, panelBottom, P.BLACK, 0.48);
    h += hline(px, pr, panelBottom, P.BLACK, 0.48);

    // 客户地址
    // 地址行距原稿为交替 11.52 / 11.28
    let addrB = 179.28;
    (data.customer.lines || []).forEach((line, i) => {
      if (line) h += T(addrB, line, { x: 108, fam: P.F_EX, size: 10, color: P.BLACK });
      addrB += (i % 2 === 0) ? 11.52 : 11.28;
    });
    return h;
  }

  /* ---------- 块渲染 ---------- */
  function blockHTML(m, data, b) {
    switch (b.type) {
      case "secHead": return secHeadHTML(b.A, b.y);
      case "repHead": return repHeadHTML(b.y);
      case "beginRow": return beginRowHTML(b.A, b.y, data);
      case "row": return rowHTML(b.r, b.y, b.h);
      case "note": return noteHTML(b.A, b.y, b.h);
      case "endRow": return endRowHTML(b.A, b.y, data);
      case "tableEnd": return hline(P.LEFT, P.RIGHT, b.y, P.BLACK, 0.48);
      case "disclosure": return disclosureHTML(data.disclosure, b);
      case "reporting": return reportingHTML(m, data, b.y);
    }
    return "";
  }

  const COLHEADS = y => {
    let h = T(y + 10.6, "Posting Date", { center: (P.COLS[0] + P.COLS[1]) / 2, fam: P.F_AB, size: 8, weight: 700, color: P.BLACK });
    h += T(y + 10.6, "Transaction Description", { x: DESC_X, fam: P.F_AB, size: 8, weight: 700, color: P.BLACK });
    h += T(y + 10.6, "Deposit", { center: (P.COLS[2] + P.COLS[3]) / 2, fam: P.F_AB, size: 8, weight: 700, color: P.BLACK });
    h += T(y + 10.6, "Withdrawal", { center: (P.COLS[3] + P.COLS[4]) / 2, fam: P.F_AB, size: 8, weight: 700, color: P.BLACK });
    h += T(y + 10.6, "Balance", { center: (P.COLS[4] + P.COLS[5]) / 2, fam: P.F_AB, size: 8, weight: 700, color: P.BLACK });
    return h;
  };

  function secHeadHTML(A, y0) {
    const L = P.LEFT, R = P.RIGHT;
    let h = rect(L, y0, R - L, RH.BAR, P.BLUE);
    h += T(y0 + 10.08, `${A.src.name} (ID ${A.src.id})`, { x: 29.52, fam: P.F_AB, size: 11, weight: 700, color: "#fff" });
    const st = y0 + RH.BAR;
    h += rect(L, st, P.RIGHT2 - L, RH.STRIP, P.GRAY);
    const cols = P.STRIP_COLS;
    const labels = [
      ["Beginning", "Balance", 0], ["Total", `Withdrawals (${A.nWd})`, 1], ["Total", `Deposits (${A.nDep})`, 1],
      ["Ending", "Balance", 0], ["Annual Percentage", "Yield Earned", 0], ["Dividends", "Earned", 0], ["YTD Dividends"],
    ];
    labels.forEach(lab => {
      const i = labels.indexOf(lab), c = (cols[i] + cols[i + 1]) / 2;
      if (lab.length === 3) {
        h += T(st + 9.6, lab[0], { center: c, fam: P.F_AB, size: 8, weight: 700, color: P.BLACK });
        h += T(st + (lab[2] ? 18.72 : 18.96), lab[1], { center: c, fam: P.F_AB, size: 8, weight: 700, color: P.BLACK });
      } else {
        h += T(st + 14.16, lab[0], { center: c, fam: P.F_AB, size: 8, weight: 700, color: P.BLACK });
      }
    });
    const stripV = stripValues(A);
    stripV.forEach((v, i) => {
      if (v !== null) h += T(st + 33.12, v, { center: (cols[i] + cols[i + 1]) / 2, fam: P.F_EX, size: 9, color: P.BLACK });
    });
    const vTop = st + RH.STRIP, vBot = vTop + RH.VALUES, hBot = vBot + RH.HEADER;
    h += hline(L, P.RIGHT2, vTop, P.BLACK, 0.48);
    h += hline(L, P.RIGHT2, vBot, P.BLACK, 0.48);
    for (const x of cols) h += vline(x, st, vBot, P.BLACK, 0.48);
    h += rect(L, vBot, P.RIGHT2 - L, RH.HEADER, P.GRAY);
    h += hline(L, P.RIGHT2, hBot, P.BLACK, 0.48);
    h += COLHEADS(vBot);
    for (const x of P.COLS) h += vline(x, hBot, hBot + RH.TICKS, P.BLACK, 0.48);
    return h;
  }

  function stripValues(A) {
    const s = A.src;
    const f = (v) => v === null || v === undefined || v === "" ? "–" : v;
    return [
      fmtUSD(A.beginning), fmtUSD(A.totWd), fmtUSD(A.totDep), fmtUSD(A.ending),
      f(s.apy), f(typeof s.dividendsEarned === "number" ? fmtUSD(s.dividendsEarned) : s.dividendsEarned),
      f(typeof s.ytdDividends === "number" ? fmtUSD(s.ytdDividends) : s.ytdDividends),
    ];
  }

  function repHeadHTML(y) {
    /* 续页重复表头带：灰底 + 底线 + 列刻线；表头带上方不画横线（与首页区段头一致） */
    let h = rect(P.LEFT, y, P.RIGHT2 - P.LEFT, RH.HEADER, P.GRAY);
    h += hline(P.LEFT, P.RIGHT2, y + RH.HEADER, P.BLACK, 0.48);
    h += COLHEADS(y);
    for (const x of P.COLS) h += vline(x, y + RH.HEADER, y + RH.HEADER + RH.TICKS, P.BLACK, 0.48);
    return h;
  }

  function bodyVLines(y, h) {
    let s = "";
    for (const x of P.COLS) s += vline(x, y, y + h, P.BLACK, 0.48);
    return s;
  }

  function beginRowHTML(A, y, data) {
    let h = bodyVLines(y, RH.BEGIN);
    const b = y + 7.2, st = data.statement;
    h += T(b, A.src.beginDate || st.periodStart, { center: (P.COLS[0] + P.COLS[1]) / 2, fam: P.F_AB, size: 8, weight: 700, color: P.BLACK });
    h += T(b + 0.24, "BEGINNINGBALANCE", { x: DESC_X, fam: P.F_AB, size: 8, weight: 700, color: P.BLACK });
    h += T(b, fmtUSDSpace(A.beginning), { right: 591.07, fam: P.F_AB, size: 8, weight: 700, color: P.BLACK });
    h += hline(P.COLS[0], P.COLS[5], y + RH.BEGIN, P.SEP, 0.48);
    return h;
  }

  function rowHTML(r, y, hRow) {
    const t = r.t, b = y + 7.2;
    let h = bodyVLines(y, hRow);
    h += T(b, t.date, { center: (P.COLS[0] + P.COLS[1]) / 2, fam: P.F_EX, size: 8, color: P.BLACK });
    r.lines.forEach((ln, i) => h += T(b + i * RH.LINE, ln, { x: i === 0 ? DESC_X : DESC_X2, fam: P.F_EX, size: 8, color: P.BLACK }));
    if (t.dir === "withdrawal") {
      h += T(b + 0.96, "–", { x: (P.COLS[2] + P.COLS[3]) / 2 - 0.96, fam: P.F_EX, size: 9, color: P.BLACK });
      h += T(b + 0.96, "--------", { x: (P.COLS[2] + P.COLS[3]) / 2 + 4.08, fam: P.F_EX, size: 8, color: "#ffffff" });   // 原稿空格占位白字
      h += T(b, fmtAmt(-Math.abs(t.amount || 0)), { right: 532.77, fam: P.F_EX, size: 8, color: P.BLACK });
    } else {
      h += T(b, fmtAmt(Math.abs(t.amount || 0)), { right: 475.17, fam: P.F_EX, size: 8, color: P.BLACK });
      h += T(b + 0.96, "–", { x: (P.COLS[3] + P.COLS[4]) / 2 - 0.96, fam: P.F_EX, size: 9, color: P.BLACK });
      h += T(b + 0.96, "--------", { x: (P.COLS[3] + P.COLS[4]) / 2 + 4.08, fam: P.F_EX, size: 8, color: "#ffffff" });   // 原稿空格占位白字
    }
    h += T(b, fmtAmt(r.bal), { right: 591.07, fam: P.F_EX, size: 8, color: P.BLACK });
    h += hline(P.COLS[0], P.COLS[5], y + hRow, P.SEP, 0.48);
    return h;
  }

  function noteHTML(A, y, hRow) {
    let h = bodyVLines(y, hRow);
    const b = y + 7.2;
    A.noteLines.forEach((ln, i) => h += T(b + i * RH.LINE, ln, { x: i === 0 ? DESC_X : DESC_X2, fam: P.F_EX, size: 8, color: P.BLACK }));
    h += hline(P.COLS[0], P.COLS[5], y + hRow, P.SEP, 0.48);
    return h;
  }

  function endRowHTML(A, y, data) {
    let h = bodyVLines(y, RH.END);
    const b = y + 7.2, st = data.statement;
    h += T(b, A.src.endDate || st.periodEnd, { center: (P.COLS[0] + P.COLS[1]) / 2, fam: P.F_AB, size: 8, weight: 700, color: P.BLACK });
    h += T(b, "ENDING BALANCE", { x: DESC_X, fam: P.F_AB, size: 8, weight: 700, color: P.BLACK });
    h += T(b, fmtUSDSpace(A.ending), { right: 591.07, fam: P.F_AB, size: 8, weight: 700, color: P.BLACK });
    return h;
  }

  /* ---------- 披露框（正文两端对齐） ---------- */
  function disclosureHTML(disc, b) {
    const top = b.y, bottom = top + b.h;
    let h = vline(P.LEFT, top, bottom, P.BLACK, 0.48) + vline(P.RIGHT, top, bottom, P.BLACK, 0.48);
    h += hline(P.LEFT, P.RIGHT, bottom, P.BLACK, 0.48);
    if (!b.attached) h += hline(P.LEFT, P.RIGHT, top, P.BLACK, 0.48);
    if (window.BAKED && window.BAKED.disclosure) {
      const D = window.BAKED.disclosure;
      h += bakedText(D.heading, top + D.heading.y);
      for (const ln of D.lines) h += bakedText(ln, top + ln.y);
      return h;
    }
    const L = disclosureLayout(disc);
    const PARA_W = 546.3;
    L.headingLines.forEach((ln, i) => {
      h += T(top + L.headingBaseline + i * 9.36, ln, { x: 29.52, fam: P.F_AB, size: 8, weight: 700, color: P.BLACK });
    });
    for (const p of L.paras) {
      const x0 = 29.52, indent = p.isB ? 9.36 : 0;   // 悬挂缩进：首行 +9.36，续行回 29.52
      if (p.isB) h += T(top + p.first, "•", { x: 29.52, fam: P.F_EX, size: 8, color: P.BLACK });
      const PARA_W2 = 546.3;
      rec({ t: "para", x: x0, indent, first: top + p.first, lines: p.lines, lineH: 9.36, ws: DISC_WS,
            avail: PARA_W2 - indent, restAvail: PARA_W2, f: P.F_EX, z: 8, c: P.BLACK });
      h += `<div class="para" style="left:${x0}pt;top:${top + p.first - P.ASC * 8}pt;width:${PARA_W2}pt;${indent ? "text-indent:" + indent + "pt;" : ""}line-height:9.36pt;word-spacing:${DISC_WS}pt;text-align:justify;font-family:${P.F_EX};font-size:8pt;color:${P.BLACK};">${esc(p.text)}</div>`;
    }
    return h;
  }

  /* ---------- 年度报告区段 ---------- */
  function reportingHTML(m, data, y0) {
    const rep = data.reporting;
    let h = rect(P.LEFT, y0, P.RIGHT2 - P.LEFT, 14.4, P.BLUE);
    h += T(y0 + 10.8, `REPORTING INFORMATION FOR ${rep.year}:`, { x: 29.52, fam: P.F_AB, size: 11, weight: 700, color: "#fff" });
    const bt = y0 + 14.4, bb = bt + 50.4;
    h += vline(P.LEFT, bt, bb, P.BLACK, 0.48) + vline(P.RIGHT2, bt, bb, P.BLACK, 0.48);
    h += hline(P.LEFT, P.RIGHT2, bb, P.BLACK, 0.48);
    h += T(bt + 15.12, rep.label, { x: 92.16, fam: P.F_EX, size: 8, color: P.BLACK });
    const val = rep.valueOverride != null && rep.valueOverride !== "" ? rep.valueOverride : fmtUSDSpace(m.ytdTotal);
    h += T(bt + 14.16, val, { x: 250.56, fam: P.F_EX, size: 8, color: P.BLACK });
    const img = rep.promoUrl || "assets/promo-logo.jpg";   // 默认 = 原稿第2页位图 LOGO 复刻
    rec({ t: "img", src: img, x: 371.28, y: bt + 5.52, w: 168.24, h: 37.68 });
    h += `<img class="promo" src="${img}" onerror="if(this.src.indexOf('promo-logo.jpg')<0)this.src='assets/promo-logo.jpg';" style="left:371.28pt;top:${bt + 5.52}pt;width:168.24pt;height:37.68pt;">`;
    return h;
  }

  /* ---------- API ---------- */
  global.StatementEngine = {
    CONST: P, RH, render, wrap, tw,
    fmtAmt, fmtUSD, fmtUSDSpace, derive, disclosureHeight,
  };
})(window);
