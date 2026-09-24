// 88-repair
// 局部噪点修正（手动抖动修补）：套索选区 + S 识别受害者 + 强度修补，以及画笔涂抹

// ============================================================
// 局部噪点修正
// ============================================================
// 数据全部以「归一化坐标」（0~1，相对真实图像）保存，所以：
//   · 预览图被缩小过也能正确圈中同一片区域
//   · 生成时按成品尺寸换算，和预览看到的是同一块地方
const RP = {
    mode: "lasso",          // lasso | brush
    lassos: [],             // [{points:[[x,y]...], level:2..4, hex:null}]
    strokes: [],            // [{x,y,r,hex}]
    identify: false,        // true = 只高亮受害者，不改结果
    level: 2,
    brushHex: null,         // 笔刷颜色（调色板里必须存在）
    brushSizePx: 6,         // 笔刷半径，单位是「成品方块数」
    realW: 128,             // 上一次预览对应的成品宽度（用来把方块数换成归一化半径）
    picking: false,         // 取色模式
    canvas: null,
    wrap: null,
    ro: null,               // ResizeObserver（图片显示尺寸变了要重新对齐画布）
    windowBound: false,     // window 上的 mouseup 只绑一次，别随画布重建累积
    drawing: null,          // 正在画的套索（canvas 像素坐标）
    painting: false,
    lastPt: null,           // 上一次落笔位置（用于插值，快速拖动也不断线）
    hasResult: false,       // 服务端有没有返回过 repair 信息
    lastInfo: null,
};

const RP_LEVEL_DESC = {
    2: "轻度修复：只改「主色明显更合适」的方块（主色近至少 2 倍），抖动感保留最多。",
    3: "中度修复：改「主色更合适」的方块（主色近至少 1.33 倍）。",
    4: "强力修复：改掉全部受害者（主色不比当前色差的都改）。渐变该有的层次会保留。",
};

// ------------------------------------------------------------ 数据 -> 请求
function rpActive() {
    return RP.lassos.length > 0 || RP.strokes.length > 0;
}

/** 拼出要发给后端的 repair 字段；没有修正内容时返回 null。 */
function rpPayload() {
    if (!rpActive() && !RP.identify) return null;
    const r = {
        identify: !!RP.identify,
        lassos: RP.lassos.map(l => ({
            points: l.points.map(p => [p[0], p[1]]),
            level: l.level,
            hex: l.hex || null,
        })),
        strokes: RP.strokes.map(s => ({ x: s.x, y: s.y, r: s.r, hex: s.hex })),
    };
    if (!r.lassos.length && !r.strokes.length && !r.identify) return null;
    return r;
}

/** 插进预览 / 生成请求里用的展开片段。 */
function rpSpread() {
    const p = rpPayload();
    return p ? { repair: p } : {};
}

// ------------------------------------------------------------ 画布
function rpPreviewImg() {
    const box = $("mp-preview-pal");
    return box ? box.querySelector("img") : null;
}

/**
 * 每次预览图重绘（box.innerHTML 被换掉）之后都要重新挂一次画布。
 *
 * 画布是 .preview-box 的绝对定位子元素，直接盖在 <img> 上面 ——
 * 刻意**不用包一层 wrapper**：.preview-box 是 flex 容器，多一层
 * inline-block 会和 img 上的 max-width:100% 形成循环依赖，图片尺寸会被压得很小。
 *
 * 另外：刚 innerHTML 出 <img> 的时候它还没完成布局，这时量出来的尺寸是错的
 * （画布会变成 1×1，后面所有归一化坐标全乱），所以既等 load 事件，
 * 也挂一个 ResizeObserver 兜底（专注模式 / 窗口缩放都会改变图片显示尺寸）。
 */
function rpAttach() {
    const box = $("mp-preview-pal");
    if (!box) return;
    const img = box.querySelector("img");
    if (!img) { RP.canvas = null; return; }
    if (RP.canvas && RP.canvas.isConnected && RP.canvas.parentNode === box) {
        rpResize();
        rpRedraw();
        return;
    }
    const cv = document.createElement("canvas");
    cv.className = "rp-canvas" + (RP.mode === "brush" ? " brush" : "");
    cv.id = "rp-canvas";
    box.appendChild(cv);
    RP.canvas = cv;

    const sync = () => { rpResize(); rpRedraw(); };
    if (img.complete && img.naturalWidth) {
        sync();
    } else {
        img.addEventListener("load", sync, { once: true });
    }
    if (window.ResizeObserver) {
        if (RP.ro) { try { RP.ro.disconnect(); } catch (e) { } }
        RP.ro = new ResizeObserver(sync);
        RP.ro.observe(img);
        RP.ro.observe(box);
    }
    // 布局还没算完的话，下一帧 / 稍后再量
    setTimeout(sync, 0);
    setTimeout(sync, 150);

    rpBindCanvas();
    sync();
}

function rpResize() {
    const cv = RP.canvas, img = rpPreviewImg();
    const box = $("mp-preview-pal");
    if (!cv || !img || !box) return;
    const ir = img.getBoundingClientRect();
    const br = box.getBoundingClientRect();
    // 图片还没布局好就先不动，等 load / ResizeObserver 再来，
    // 免得把一个 1×1 的画布当成“量好了”，后面坐标全错
    if (ir.width < 2 || ir.height < 2) return;
    const left = ir.left - br.left + box.scrollLeft;
    const top = ir.top - br.top + box.scrollTop;
    cv.style.left = left + "px";
    cv.style.top = top + "px";
    cv.style.width = ir.width + "px";
    cv.style.height = ir.height + "px";
    const w = Math.round(ir.width), h = Math.round(ir.height);
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
}

/** 归一化坐标 -> 画布像素坐标 */
function rpToCanvas(x, y) {
    const cv = RP.canvas;
    return [x * cv.width, y * cv.height];
}

/** 画布像素坐标 -> 归一化坐标（夹在 0~1，退化尺寸时返回 null） */
function rpToNorm(px, py) {
    const cv = RP.canvas;
    if (!cv || cv.width < 2 || cv.height < 2) return null;
    return [Math.max(0, Math.min(1, px / cv.width)),
            Math.max(0, Math.min(1, py / cv.height))];
}

function rpEventPos(e) {
    const r = RP.canvas.getBoundingClientRect();
    const t = (e.touches && e.touches[0]) ? e.touches[0] : e;
    return [t.clientX - r.left, t.clientY - r.top];
}

function rpRedraw() {
    const cv = RP.canvas;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, cv.width, cv.height);

    // 已确定的套索
    ctx.lineWidth = 2;
    RP.lassos.forEach((l) => {
        ctx.beginPath();
        l.points.forEach((p, i) => {
            const [x, y] = rpToCanvas(p[0], p[1]);
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.closePath();
        ctx.fillStyle = "rgba(59,125,221,.20)";
        ctx.fill();
        ctx.strokeStyle = l === RP.lassos[RP.lassos.length - 1]
            ? "rgba(59,125,221,1)" : "rgba(59,125,221,.65)";
        ctx.stroke();
    });

    // 正在拖的套索
    if (RP.drawing && RP.drawing.length > 1) {
        ctx.beginPath();
        RP.drawing.forEach((p, i) => {
            if (i === 0) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]);
        });
        ctx.strokeStyle = "rgba(255,140,0,.95)";
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 4]);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.arc(RP.drawing[0][0], RP.drawing[0][1], 4, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(255,140,0,1)";
        ctx.fill();
    }

    // 笔画：淡色描出涂过的地方（实际颜色在服务端返回的预览图里）
    if (RP.strokes.length) {
        ctx.setLineDash([3, 3]);
        ctx.strokeStyle = "rgba(255,0,200,.55)";
        ctx.lineWidth = 1;
        RP.strokes.forEach((s) => {
            const [x, y] = rpToCanvas(s.x, s.y);
            const r = s.r * cv.width;
            ctx.beginPath();
            ctx.arc(x, y, Math.max(1, r), 0, Math.PI * 2);
            ctx.stroke();
        });
        ctx.setLineDash([]);
    }
}

// ------------------------------------------------------------ 交互
function rpBindCanvas() {
    const cv = RP.canvas;
    if (!cv || cv.dataset.bound) return;
    cv.dataset.bound = "1";

    cv.addEventListener("mousedown", (e) => {
        if (RP.picking || e.altKey) { rpPickColor(e); return; }
        e.preventDefault();
        if (RP.mode === "lasso") {
            RP.drawing = [rpEventPos(e)];
            rpRedraw();
        } else {
            RP.painting = true;
            RP.lastPt = null;
            rpPaintAt(e);
        }
    });

    cv.addEventListener("mousemove", (e) => {
        if (RP.drawing) {
            RP.drawing.push(rpEventPos(e));
            rpRedraw();
        } else if (RP.painting) {
            rpPaintAt(e);
        }
    });

    cv.addEventListener("touchstart", (e) => {
        if (RP.mode !== "lasso") return;
        e.preventDefault();
        RP.drawing = [rpEventPos(e)];
        rpRedraw();
    }, { passive: false });
    cv.addEventListener("touchmove", (e) => {
        if (!RP.drawing) return;
        e.preventDefault();
        RP.drawing.push(rpEventPos(e));
        rpRedraw();
    }, { passive: false });
    cv.addEventListener("touchend", () => rpFinishDrag());

    // 抬起鼠标 / 手指要绑在 window 上（拖到画布外面也要能收尾）。
    // 这个只能绑一次：每次预览重绘都会换一块新画布、重新调 rpBindCanvas，
    // 绑在 window 上的话会一次次累积，越攒越多。
    if (!RP.windowBound) {
        RP.windowBound = true;
        window.addEventListener("mouseup", () => rpFinishDrag());
    }
}

/** 收尾：把正在拖的套索定下来，或结束一次涂抹 */
function rpFinishDrag() {
    if (RP.drawing) {
        const norm = RP.drawing.map(p => rpToNorm(p[0], p[1])).filter(Boolean);
        RP.drawing = null;
        if (norm.length >= 3) {
            // 简单抽稀：挨太近的点去掉，避免多边形点数爆炸
            const keep = [norm[0]];
            for (const p of norm.slice(1)) {
                const q = keep[keep.length - 1];
                if (Math.hypot(p[0] - q[0], p[1] - q[1]) > 0.004) keep.push(p);
            }
            if (keep.length >= 3) {
                RP.lassos.push({ points: keep, level: RP.level, hex: null });
                RP.identify = false;
                rpRedraw();
                rpStat("已圈出选区，按 S 识别受害者");
                rpRequest(true);
            } else {
                rpStat('<span class="rp-warn">圈得太小了</span>，再拖大一点');
                rpRedraw();
            }
        } else {
            rpStat('<span class="rp-warn">预览图还没准备好</span>，稍等一下再圈');
            rpRedraw();
        }
    }
    if (RP.painting) {
        RP.painting = false;
        RP.lastPt = null;
        rpRequest(true);
    }
}

/** 笔刷半径（方块数）-> 归一化半径。归一化是相对**成品尺寸**，不是预览尺寸，
 *  这样预览被缩小过、生成是全尺寸，两处涂到的是同一块地方。 */
function rpBrushRadius() {
    const r = RP.brushSizePx / Math.max(1, RP.realW);
    return Math.max(0.0005, Math.min(0.5, r));
}

/** 预览返回后记下成品宽度（previewMap 里调用） */
function rpNoteSize(w) {
    if (w && w > 0) RP.realW = w;
}

/** 在归一化坐标处点一笔，并和上一笔之间补点（快速拖动不断线） */
function rpPaintAt(e) {
    if (!RP.brushHex) { rpStat('<span class="rp-warn">先选一个笔刷颜色</span>'); return; }
    const [px, py] = rpEventPos(e);
    const nrm = rpToNorm(px, py);
    if (!nrm) { rpStat('<span class="rp-warn">预览图还没准备好</span>'); return; }
    const [x, y] = nrm;
    const r = rpBrushRadius();
    const pt = { x: x, y: y };

    if (RP.lastPt) {
        const d = Math.hypot(pt.x - RP.lastPt.x, pt.y - RP.lastPt.y);
        const step = Math.max(r * 0.5, 0.002);
        const n = Math.min(60, Math.floor(d / step));
        for (let i = 1; i <= n; i++) {
            const t = i / (n + 1);
            RP.strokes.push({
                x: RP.lastPt.x + (pt.x - RP.lastPt.x) * t,
                y: RP.lastPt.y + (pt.y - RP.lastPt.y) * t,
                r: r, hex: RP.brushHex,
            });
        }
    }
    RP.strokes.push({ x: pt.x, y: pt.y, r: r, hex: RP.brushHex });
    RP.lastPt = pt;
    if (RP.strokes.length > 4000) RP.strokes = RP.strokes.slice(-4000);
    rpRedraw();
    rpStat(`涂抹中… 已落 ${RP.strokes.length} 笔`);
}

/** 从预览图上吸取颜色：读的是服务端渲染出来的预览像素 */
function rpPickColor(e) {
    const img = rpPreviewImg();
    if (!img || !RP.canvas) return;
    e.preventDefault();
    const [px, py] = rpEventPos(e);
    const nx = px / RP.canvas.width, ny = py / RP.canvas.height;
    const c = document.createElement("canvas");
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    const ctx = c.getContext("2d");
    ctx.drawImage(img, 0, 0);
    let d;
    try {
        d = ctx.getImageData(Math.floor(nx * c.width), Math.floor(ny * c.height), 1, 1).data;
    } catch (err) {
        rpStat('<span class="rp-warn">取色失败：' + err.message + "</span>");
        return;
    }
    const hex = "#" + [d[0], d[1], d[2]]
        .map(v => v.toString(16).padStart(2, "0")).join("").toUpperCase();
    if (!PAL.groups || !PAL.groups.length) return;
    // 预览颜色一定来自调色板，但像素可能有抗锯齿；找最近的调色板颜色
    let best = null, bestD = Infinity;
    PAL.groups.forEach((g) => {
        const dr = g.rgb[0] - d[0], dg = g.rgb[1] - d[1], db = g.rgb[2] - d[2];
        const dist = dr * dr + dg * dg + db * db;
        if (dist < bestD) { bestD = dist; best = g; }
    });
    if (!best) return;
    rpSetBrushColor(best.hex);
    RP.picking = false;
    if (RP.canvas) RP.canvas.classList.remove("picking");
    setStatus("已取色：" + best.hex + (bestD > 0 ? "（贴到最近的调色板颜色）" : ""));
}

// ------------------------------------------------------------ UI 同步
function rpSetBrushColor(hex) {
    RP.brushHex = hex;
    const sw = $("rp-color");
    if (sw) { sw.style.background = hex; sw.hidden = false; }
    const hx = $("rp-color-hex");
    if (hx) hx.textContent = hex;
    document.querySelectorAll("#rp-pal .rp-chip").forEach((c) => {
        c.classList.toggle("sel", c.dataset.hex === hex);
    });
}

function rpStat(html) {
    const el = $("rp-stat");
    if (el) el.innerHTML = html;
}

function rpLevelLabel(level) {
    const el = $("rp-level-desc");
    if (el) el.textContent = RP_LEVEL_DESC[level] || "";
    const v = $("rp-level-v");
    if (v) v.textContent = String(level);
}

/** 切换套索 / 画笔页签。silent=true 时不改状态栏（初始化时用，别把「就绪」顶掉）。 */
function rpSetMode(mode, silent) {
    RP.mode = mode;
    document.querySelectorAll(".rp-tab").forEach((b) => {
        b.classList.toggle("active", b.dataset.rpmode === mode);
    });
    const a = $("rp-pane-lasso"), b = $("rp-pane-brush");
    if (a) a.hidden = mode !== "lasso";
    if (b) b.hidden = mode !== "brush";
    if (RP.canvas) RP.canvas.classList.toggle("brush", mode === "brush");
    rpRedraw();
    if (silent) return;
    setStatus(mode === "lasso"
        ? "套索：在预览图上拖一圈圈出区域，按 S 识别受害者"
        : "画笔：按住涂抹；Alt+点击取色");
}

function rpBuildPalette() {
    const el = $("rp-pal");
    if (!el || !PAL.groups) return;
    el.textContent = "";
    const frag = document.createDocumentFragment();
    PAL.groups.forEach((g) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "rp-chip";
        b.dataset.hex = g.hex;
        b.style.background = g.hex;
        b.title = g.hex + "　" + g.blocks.map(x => x.label).slice(0, 3).join(" / ")
            + (g.blocks.length > 3 ? " …" : "");
        b.addEventListener("click", () => rpSetBrushColor(g.hex));
        if (g.hex === RP.brushHex) b.classList.add("sel");
        frag.appendChild(b);
    });
    el.appendChild(frag);
}

/** 把服务端返回的 repair 诊断显示出来 */
function rpShowInfo(info) {
    RP.hasResult = true;
    RP.lastInfo = info;
    if (!info) { return; }
    if (info.identify) {
        const vic = info.victims || 0;
        const dom = info.dominant || "—";
        rpStat(vic
            ? `<b>识别到 ${vic} 个受害者方块</b>，主色 <b>${dom}</b><br>` +
              `洋红色高亮的就是它们。拖动强度滑块决定改回多少，然后点「应用修正」。`
            : `这个区域里<b>没有受害者</b>（没有方块被抖动改坏），不用修。`);
        return;
    }
    if (info.applied) {
        const parts = [];
        if (info.repaired) parts.push(`套索改回 <b>${info.repaired}</b> 个`);
        if (info.brush_pixels) parts.push(`画笔覆盖 <b>${info.brush_pixels}</b> 个`);
        rpStat(parts.join("，") + (info.dominant ? `　主色 <b>${info.dominant}</b>` : ""));
    } else if (info.note) {
        rpStat(info.note);
    }
}

// ------------------------------------------------------------ 触发预览
let rpTimer = null;
function rpRequest(now) {
    if (rpTimer) clearTimeout(rpTimer);
    if (now) { rpTimer = null; schedulePreview(0); return; }
    rpTimer = setTimeout(() => { rpTimer = null; schedulePreview(120); }, 120);
}

// ------------------------------------------------------------ 绑定
document.querySelectorAll(".rp-tab").forEach((b) => {
    b.addEventListener("click", () => rpSetMode(b.dataset.rpmode));
});

$("rp-level").addEventListener("input", () => {
    RP.level = parseInt($("rp-level").value, 10) || 2;
    rpLevelLabel(RP.level);
    // 强度跟着选区走：改一次就把已圈的区域都按新强度算
    RP.lassos.forEach((l) => { l.level = RP.level; });
    if (RP.lassos.length) rpRequest();
});

$("rp-size").addEventListener("input", () => {
    RP.brushSizePx = parseInt($("rp-size").value, 10) || 6;
    $("rp-size-v").textContent = String(RP.brushSizePx);
});

$("rp-identify").addEventListener("click", () => {
    if (!RP.lassos.length) { setStatus("先套索圈出一片区域"); return; }
    RP.identify = true;
    rpRequest(true);
    setStatus("正在识别受害者方块…");
});

$("rp-apply").addEventListener("click", () => {
    if (!RP.lassos.length && !RP.strokes.length) { setStatus("还没有任何修正内容"); return; }
    RP.identify = false;
    rpRequest(true);
    setStatus("正在应用修正…");
});

$("rp-clear-lasso").addEventListener("click", () => {
    RP.lassos = [];
    RP.identify = false;
    rpRedraw();
    rpStat("已清除选区");
    rpRequest(true);
});

$("rp-clear-strokes").addEventListener("click", () => {
    RP.strokes = [];
    rpRedraw();
    rpStat("已清除涂抹");
    rpRequest(true);
});

$("rp-pick").addEventListener("click", () => {
    RP.picking = !RP.picking;
    if (RP.canvas) RP.canvas.classList.toggle("picking", RP.picking);
    setStatus(RP.picking ? "取色：在预览图上点一下" : "已取消取色");
});

$("rp-reset").addEventListener("click", () => {
    RP.lassos = [];
    RP.strokes = [];
    RP.identify = false;
    rpRedraw();
    rpStat("已清除全部修正");
    rpRequest(true);
});

// 快捷键：S 识别受害者，B 切画笔，L 切套索，Esc 取消取色
document.addEventListener("keydown", (e) => {
    if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const k = e.key.toLowerCase();
    if (k === "s") {
        if (!RP.lassos.length) return;
        e.preventDefault();
        RP.identify = true;
        rpRequest(true);
        setStatus("正在识别受害者方块…");
    } else if (k === "b") {
        rpSetMode("brush");
    } else if (k === "l") {
        rpSetMode("lasso");
    } else if (e.key === "Escape" && RP.picking) {
        RP.picking = false;
        if (RP.canvas) RP.canvas.classList.remove("picking");
        setStatus("已取消取色");
    }
});

// 窗口大小变化时画布跟着图片重算
window.addEventListener("resize", () => { rpResize(); rpRedraw(); });

rpLevelLabel(RP.level);
rpSetMode("lasso", true);      // 初始化：别把状态栏的「就绪」顶掉
