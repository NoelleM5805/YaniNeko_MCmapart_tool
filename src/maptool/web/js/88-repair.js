// 88-repair
// 局部噪点修正：先选受害者方块 -> 圈选区 -> 按按钮才执行

// ============================================================
// 局部噪点修正
// ============================================================
// 工作流（刻意做成三步，避免一拖就改坏）：
//     ① 选受害者方块（从预览图拾取，或从色块列表里挑）
//     ② 在预览图上圈出选区
//     ③ 点「执行降噪」才真正修改
//
// 数据模型：RP.ops 是一串**操作**，服务端每次都从抖动结果出发按顺序重放。
// 好处：
//   · 预览和最终生成用同一串操作，修改结果一致
//   · 撤回 = 去掉最后一个操作重新渲染（见 89-undo.js）
//   · 改了调色板 / 抖动算法之后，操作序列照样对得上
//
// 坐标一律用**归一化坐标**（0~1，相对真实图像），这样：
//   · 预览被缩小过、生成是全尺寸，圈到的是同一块地方
//   · 覆盖层可以直接跟着预览图的缩放/平移一起变换（见 rpApplyTransform）
const RP = {
    open: false,            // 是否处于编辑模式（= CFG.soloPreview「隐藏原图」）
    tool: "lasso",          // lasso | brush
    target: null,           // 受害者方块色号，必须先选
    strength: 5,            // 修复强度 -> 邻域半径
    brushHex: null,
    brushSize: 6,
    realW: 128,             // 上一次预览对应的成品尺寸
    realH: 128,
    picking: false,         // 取色模式（拾取受害者/笔刷颜色）
    showOverlay: true,      // 是否在预览图上画选区轮廓（隐藏 ≠ 删除，修改结果不受影响）
    pending: null,          // 正在拖、还没执行的套索 [[x,y],...]
    ops: [],                // 操作序列（文档本体）
    canvas: null,
    ro: null,
    windowBound: false,
    drawing: null,
    painting: false,
    lastPt: null,
    lastInfo: null,
    // 预览图的像素缓存，用于在本地高亮「受害者方块」
    px: null,               // Uint8ClampedArray
    pxW: 0, pxH: 0, pxSrc: "",
};

// 高亮的洋红（和预览图上受害者标记保持一致）
const RP_HL = [255, 0, 200];

// ------------------------------------------------------------ 请求片段
/** 把操作序列塞进预览 / 生成请求里 */
function rpSpread() {
    if (!RP.ops.length) return {};
    return { repair: { ops: JSON.parse(JSON.stringify(RP.ops)) } };
}

// ------------------------------------------------------------ 配色调参镜像
// 专注模式把主区藏起来了，而「颜色识别 / 抖动算法 / 抖动比例」都在主区里。
// 这里在左边栏做一份同步控件：选项直接从主区的 select 拷过来，
// 改动写回主区的控件并派发事件 —— 这样预览、配置记忆、撤回都不用在两边各写一遍。
function rpBuildMirrors() {
    [["mp-algo", "fx-algo"], ["mp-dither", "fx-dither"]].forEach(([src, dst]) => {
        const s = $(src), d = $(dst);
        if (!s || !d) return;
        d.innerHTML = s.innerHTML;
        d.value = s.value;
        d.addEventListener("change", () => {
            s.value = d.value;
            s.dispatchEvent(new Event("change", { bubbles: true }));
        });
        s.addEventListener("change", () => { d.value = s.value; });
    });

    const ss = $("mp-strength"), ds = $("fx-strength");
    if (ss && ds) {
        ds.value = ss.value;
        const sv = $("fx-strength-v");
        if (sv) sv.textContent = ss.value;
        ds.addEventListener("input", () => {
            ss.value = ds.value;
            ss.dispatchEvent(new Event("input", { bubbles: true }));
            if (sv) sv.textContent = ds.value;
        });
        ds.addEventListener("change", () => {
            ss.dispatchEvent(new Event("change", { bubbles: true }));
        });
        ss.addEventListener("input", () => {
            ds.value = ss.value;
            if (sv) sv.textContent = ss.value;
        });
    }
}
function rpSetOpen(on) {
    RP.open = !!on;
    const sec = $("rp-sec");
    if (sec) sec.hidden = !RP.open;
    const fx = $("fx-sec");
    if (fx) fx.hidden = !RP.open;      // 配色调参也只在专注模式下露出来
    if (!RP.open) {
        rpDetach();                    // 退出编辑时把画布摘掉：
        RP.pending = null;             // 否则会挡住「点击放大」
        RP.picking = false;
        RP.painting = false;
        RP.drawing = null;
        RP.buffer = null;
    } else {
        rpAttach();
        setTimeout(() => { rpAttach(); rpRedraw(); }, 60);
    }
}

/** 显示 / 隐藏预览图上的选区轮廓（不改变任何修改内容） */
function rpSetOverlay(on, silent) {
    RP.showOverlay = !!on;
    const b = $("rp-hide");
    if (b) {
        b.textContent = RP.showOverlay ? "👁 隐藏选区" : "👁 显示选区";
        b.classList.toggle("off", !RP.showOverlay);
    }
    rpRedraw();
    if (!silent) {
        setStatus(RP.showOverlay ? "已显示选区轮廓" : "已隐藏选区轮廓（修改内容不受影响）");
    }
}

function rpSyncUI() {
    const t = $("rp-target-hex");
    if (t) t.textContent = RP.target || "未选择";
    const sw = $("rp-target-dot");
    if (sw) {
        sw.style.background = RP.target || "transparent";
        sw.hidden = !RP.target;
    }
    const bt = $("rp-exec");
    if (bt) {
        const ok = !!(RP.target && RP.pending);
        bt.disabled = !ok;
        bt.title = !RP.target ? "先选受害者方块"
            : (!RP.pending ? "先在预览图上圈出选区" : "按这里才会真正修改");
    }
    const bf = $("rp-fill"), br = $("rp-revert");
    [bf, br].forEach(b => { if (b) b.disabled = !RP.pending; });
    const cb = $("rp-clear-sel");
    if (cb) cb.disabled = !RP.pending;
    const sc = $("rp-color");
    if (sc) { sc.style.background = RP.brushHex || "#888"; }
    const sh = $("rp-color-hex");
    if (sh) sh.textContent = RP.brushHex || "—";
    document.querySelectorAll("#rp-pal .rp-chip").forEach((c) => {
        c.classList.toggle("sel", c.dataset.hex === RP.brushHex);
        c.classList.toggle("target", c.dataset.hex === RP.target);
    });
    rpRedraw();
}

function rpStat(html) {
    const el = $("rp-stat");
    if (el) el.innerHTML = html;
}

function rpSetTarget(hex, why) {
    RP.target = hex;
    if (why) setStatus(why);
    rpSyncUI();
    rpRedraw();
}

function rpSetBrushColor(hex) {
    RP.brushHex = hex;
    rpSyncUI();
}

// ------------------------------------------------------------ 画布
function rpPreviewImg() {
    const box = $("mp-preview-pal");
    return box ? box.querySelector("img") : null;
}

function rpEnsureCanvas() {
    const box = $("mp-preview-pal");
    if (!box || !RP.open) return null;
    const img = box.querySelector("img");
    if (!img) return null;
    if (RP.canvas && RP.canvas.isConnected && RP.canvas.parentNode === box) {
        return RP.canvas;
    }
    const cv = document.createElement("canvas");
    cv.className = "rp-canvas";
    cv.id = "rp-canvas";
    box.appendChild(cv);
    RP.canvas = cv;
    rpBindCanvas();
    return cv;
}

function rpDetach() {
    if (RP.canvas && RP.canvas.parentNode) RP.canvas.parentNode.removeChild(RP.canvas);
    RP.canvas = null;
    if (RP.ro) { try { RP.ro.disconnect(); } catch (e) { } RP.ro = null; }
}

/**
 * 每次预览图重绘（box.innerHTML 被换掉）之后都要重新挂一次画布。
 *
 * 画布是 .preview-box 的绝对定位子元素，**布局盒和图片完全一致**，
 * 并且套用和图片一模一样的 CSS transform —— 这样专注模式下滚轮缩放、
 * 拖动平移时，选区会跟着图片一起动，相对位置始终不变。
 */
function rpAttach() {
    const img = rpPreviewImg();
    if (!img || !RP.open) { if (!RP.open) rpDetach(); return; }
    rpEnsureCanvas();
    const sync = () => { rpResize(); rpApplyTransform(); rpRedraw(); };
    if (img.complete && img.naturalWidth) {
        sync();
    } else {
        img.addEventListener("load", sync, { once: true });
    }
    if (window.ResizeObserver && !RP.ro) {
        RP.ro = new ResizeObserver(sync);
        RP.ro.observe(img);
        const box = $("mp-preview-pal");
        if (box) RP.ro.observe(box);
    }
    // 布局可能还没算完，下一帧 / 稍后再量
    setTimeout(sync, 0);
    setTimeout(sync, 150);
    sync();
}

/** 画布布局盒 = 图片布局盒（不含 transform） */
function rpResize() {
    const cv = RP.canvas, img = rpPreviewImg();
    if (!cv || !img) return;
    const lw = img.offsetWidth, lh = img.offsetHeight;
    if (lw < 2 || lh < 2) return;
    cv.style.left = img.offsetLeft + "px";
    cv.style.top = img.offsetTop + "px";
    cv.style.width = lw + "px";
    cv.style.height = lh + "px";
    const dpr = window.devicePixelRatio || 1;
    const bw = Math.max(2, Math.round(lw * dpr));
    const bh = Math.max(2, Math.round(lh * dpr));
    if (cv.width !== bw || cv.height !== bh) { cv.width = bw; cv.height = bh; }
}

/** 让覆盖层跟着预览图的变换走（专注模式里由 focusApply 调用） */
function rpApplyTransform() {
    const cv = RP.canvas, img = rpPreviewImg();
    if (!cv || !img) return;
    cv.style.transform = img.style.transform || "";
}

/** 画布是否已经量好了 */
function rpReady() {
    const cv = RP.canvas;
    return !!(cv && cv.offsetWidth > 2 && cv.offsetHeight > 2);
}

/** 鼠标位置 -> 归一化坐标。getBoundingClientRect 已含 transform，缩放平移都自动跟得上 */
function rpNormFromEvent(e) {
    const cv = RP.canvas;
    if (!cv || !rpReady()) return null;
    const r = cv.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return null;
    const t = (e.touches && e.touches[0]) ? e.touches[0] : e;
    const x = (t.clientX - r.left) / r.width;
    const y = (t.clientY - r.top) / r.height;
    return [Math.max(0, Math.min(1, x)), Math.max(0, Math.min(1, y))];
}

function rpCanvasXY(nx, ny) {
    const cv = RP.canvas;
    return [nx * cv.offsetWidth, ny * cv.offsetHeight];
}

// ------------------------------------------------------------ 绘制
function rpDrawPoly(ctx, pts, stroke, fill, dash) {
    if (pts.length < 2) return;
    ctx.beginPath();
    pts.forEach((p, i) => {
        const [x, y] = rpCanvasXY(p[0], p[1]);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    if (!dash) ctx.closePath();
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    ctx.strokeStyle = stroke;
    ctx.setLineDash(dash || []);
    ctx.stroke();
    ctx.setLineDash([]);
}

function rpRedraw() {
    const cv = RP.canvas;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cv.offsetWidth, cv.offsetHeight);

    // 「隐藏选区」：只把轮廓收起来，操作序列和修改结果一个字节都不动。
    // 已执行的操作轮廓会一直留在图上，做几步之后就很挡视线，所以给个开关。
    if (!RP.showOverlay) return;

    // 已执行的套索
    RP.ops.forEach((op) => {
        if (op.kind === "brush") {
            ctx.setLineDash([3, 3]);
            ctx.strokeStyle = "rgba(59,125,221,.45)";
            ctx.lineWidth = 1;
            const px = Math.max(1, (op.rx || 0) * cv.offsetWidth);
            const py = Math.max(1, (op.ry || op.rx || 0) * cv.offsetHeight);
            op.points.forEach((p) => {
                const [x, y] = rpCanvasXY(p[0], p[1]);
                ctx.beginPath();
                ctx.ellipse(x, y, px, py, 0, 0, Math.PI * 2);
                ctx.stroke();
            });
            ctx.setLineDash([]);
        } else if (op.lasso) {
            rpDrawPoly(ctx, op.lasso, "rgba(59,125,221,.75)", "rgba(59,125,221,.12)");
        }
    });

    // 正在拖的套索
    if (RP.pending) {
        rpDrawPoly(ctx, RP.pending, "rgba(255,140,0,.95)", "rgba(255,140,0,.12)",
                   [5, 4]);
    } else if (RP.drawing && RP.drawing.length > 1) {
        rpDrawPoly(ctx, RP.drawing, "rgba(255,140,0,.95)", null, [5, 4]);
    }

    // 本地高亮：选区里「已经是受害者方块」的像素
    if (RP.target && RP.pending) rpHighlightVictims(ctx);
}

/** 预览图的像素缓存（每个预览只解码一次） */
function rpEnsurePixels() {
    const img = rpPreviewImg();
    if (!img || !img.naturalWidth) return null;
    if (RP.px && RP.pxSrc === img.src) return RP.px;
    const c = document.createElement("canvas");
    c.width = img.naturalWidth;
    c.height = img.naturalHeight;
    const g = c.getContext("2d");
    g.drawImage(img, 0, 0);
    let d;
    try {
        d = g.getImageData(0, 0, c.width, c.height);
    } catch (e) {
        return null;
    }
    RP.px = d.data;
    RP.pxW = c.width;
    RP.pxH = c.height;
    RP.pxSrc = img.src;
    return RP.px;
}

function rpHexToRgb(hex) {
    const h = (hex || "").replace("#", "");
    if (h.length !== 6) return null;
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16),
            parseInt(h.slice(4, 6), 16)];
}

/**
 * 在本地把选区里「当前正好是受害者方块」的像素标成洋红。
 * 完全在前端做，所以一拖就能看见，不用等服务端。
 */
function rpHighlightVictims(ctx) {
    const px = rpEnsurePixels();
    const tgt = rpHexToRgb(RP.target);
    if (!px || !tgt) return;
    const cv = RP.canvas;
    const W = cv.offsetWidth, H = cv.offsetHeight;
    if (W < 2 || H < 2) return;

    // 归一化多边形，用于判断像素在不在选区里
    const poly = RP.pending;
    const xs = poly.map(p => p[0]), ys = poly.map(p => p[1]);
    const x0 = Math.max(0, Math.floor(Math.min(...xs) * W));
    const x1 = Math.min(W, Math.ceil(Math.max(...xs) * W));
    const y0 = Math.max(0, Math.floor(Math.min(...ys) * H));
    const y1 = Math.min(H, Math.ceil(Math.max(...ys) * H));
    if (x1 <= x0 || y1 <= y0) return;

    const inside = rpPointInPoly(poly);
    const stepX = RP.pxW / W, stepY = RP.pxH / H;
    ctx.fillStyle = `rgb(${RP_HL[0]},${RP_HL[1]},${RP_HL[2]})`;
    let n = 0;
    for (let y = y0; y < y1; y++) {
        const iy = Math.min(RP.pxH - 1, Math.floor((y + 0.5) * stepY));
        for (let x = x0; x < x1; x++) {
            const ix = Math.min(RP.pxW - 1, Math.floor((x + 0.5) * stepX));
            const o = (iy * RP.pxW + ix) * 4;
            if (px[o] !== tgt[0] || px[o + 1] !== tgt[1] || px[o + 2] !== tgt[2]) continue;
            if (!inside((x + 0.5) / W, (y + 0.5) / H)) continue;
            ctx.fillRect(x, y, Math.max(1, stepX), Math.max(1, stepY));
            n++;
        }
    }
    return n;
}

/** 判断点是否在多边形内（射线法） */
function rpPointInPoly(poly) {
    const n = poly.length;
    return function (x, y) {
        let inside = false;
        for (let i = 0, j = n - 1; i < n; j = i++) {
            const xi = poly[i][0], yi = poly[i][1];
            const xj = poly[j][0], yj = poly[j][1];
            if (((yi > y) !== (yj > y))
                && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) {
                inside = !inside;
            }
        }
        return inside;
    };
}

// ------------------------------------------------------------ 交互
function rpBindCanvas() {
    const cv = RP.canvas;
    if (!cv || cv.dataset.bound) return;
    cv.dataset.bound = "1";

    cv.addEventListener("mousedown", (e) => {
        // 右键留给「拖动预览图」（见 92-focus.js），这里不处理
        if (e.button !== 0) return;
        if (RP.picking || e.altKey) { rpPick(e); return; }
        // 隐藏状态下还要画东西的话，先自动显示回来，免得「拖了半天什么都看不见」
        if (!RP.showOverlay && !RP.picking) {
            rpSetOverlay(true, true);
            setStatus("已自动显示选区轮廓");
        }
        if (RP.tool === "lasso") {
            if (!RP.target) {
                rpStat('<span class="rp-warn">先选受害者方块</span>：'
                    + '点「从预览图拾取」再点一下预览，或从下面色块里挑一个');
                setStatus("先选受害者方块，再圈选区");
                return;
            }
            e.preventDefault();
            RP.drawing = [rpNormFromEvent(e)].filter(Boolean);
            rpRedraw();
        } else {
            if (!RP.brushHex) { rpStat('<span class="rp-warn">先选笔刷颜色</span>'); return; }
            e.preventDefault();
            RP.painting = true;
            RP.lastPt = null;
            rpPaintAt(e);
        }
    });

    cv.addEventListener("mousemove", (e) => {
        if (RP.drawing) {
            const n = rpNormFromEvent(e);
            if (n) { RP.drawing.push(n); rpRedraw(); }
        } else if (RP.painting) {
            rpPaintAt(e);
        }
    });

    cv.addEventListener("touchstart", (e) => {
        if (RP.tool !== "lasso" || !RP.target) return;
        e.preventDefault();
        const n = rpNormFromEvent(e);
        if (n) { RP.drawing = [n]; rpRedraw(); }
    }, { passive: false });
    cv.addEventListener("touchmove", (e) => {
        if (!RP.drawing) return;
        e.preventDefault();
        const n = rpNormFromEvent(e);
        if (n) { RP.drawing.push(n); rpRedraw(); }
    }, { passive: false });
    cv.addEventListener("touchend", () => rpFinishDrag());

    // 抬起鼠标绑在 window 上（拖出画布也要能收尾）；只绑一次，别随画布重建累积
    if (!RP.windowBound) {
        RP.windowBound = true;
        window.addEventListener("mouseup", () => rpFinishDrag());
    }
}

/** 收尾：套索定下来（只是「待执行」，不改任何东西），或结束一次涂抹 */
function rpFinishDrag() {
    if (RP.drawing) {
        const norm = RP.drawing;
        RP.drawing = null;
        const keep = [];
        norm.forEach((p) => {
            if (!keep.length
                || Math.hypot(p[0] - keep[keep.length - 1][0],
                              p[1] - keep[keep.length - 1][1]) > 0.004) {
                keep.push(p);
            }
        });
        if (keep.length >= 3) {
            RP.pending = keep;
            rpStat(`选好了 <b>${keep.length}</b> 个点，点「✅ 执行降噪」才会真正修改`);
        } else {
            rpStat('<span class="rp-warn">圈得太小了</span>，再拖大一点');
        }
        rpSyncUI();
    }
    if (RP.painting) {
        RP.painting = false;
        RP.lastPt = null;
        rpCommitBrush();
    }
}

/** 一次涂抹 = 一个操作（整条轨迹一起提交） */
function rpCommitBrush() {
    const pts = (RP.buffer && RP.buffer.length) ? RP.buffer : null;
    RP.buffer = null;
    if (!pts || !pts.length || !RP.brushHex) { rpSyncUI(); return; }
    const rr = rpBrushRadii();
    RP.ops.push({ kind: "brush", hex: RP.brushHex,
                  rx: rr.rx, ry: rr.ry, points: pts });
    undoPush();
    rpRequest("画笔：" + pts.length + " 个落点，已涂 " + RP.brushHex);
}

/**
 * 笔刷半径 -> 归一化半径（两个分量）。
 * 单位是「成品方块数」，按各自轴归一化：成品上一个方块是正方形，
 * 预览可能和成品不同尺寸甚至被 stretch 拉过，所以 x/y 必须分开算。
 */
function rpBrushRadii() {
    const w = Math.max(1, RP.realW), h = Math.max(1, RP.realH);
    const cl = (v) => Math.max(0.0002, Math.min(0.5, v));
    return { rx: cl(RP.brushSize / w), ry: cl(RP.brushSize / h) };
}

function rpPaintAt(e) {
    if (!RP.brushHex) return;
    const n = rpNormFromEvent(e);
    if (!n) return;
    const rr = rpBrushRadii();
    if (!RP.buffer) RP.buffer = [];
    if (RP.lastPt) {
        const d = Math.hypot(n[0] - RP.lastPt[0], n[1] - RP.lastPt[1]);
        const step = Math.max(rr.rx * 0.5, 0.002);
        const cnt = Math.min(60, Math.floor(d / step));
        for (let i = 1; i <= cnt; i++) {
            const t = i / (cnt + 1);
            RP.buffer.push([RP.lastPt[0] + (n[0] - RP.lastPt[0]) * t,
                            RP.lastPt[1] + (n[1] - RP.lastPt[1]) * t]);
        }
    }
    RP.buffer.push(n);
    RP.lastPt = n;
    if (RP.buffer.length > 3000) RP.buffer = RP.buffer.slice(-3000);
    rpStat(`涂抹中… ${RP.buffer.length} 个落点（松开鼠标才会生效）`);
}

/** 从预览图上取色：读的是服务端渲染出来的预览像素 */
function rpPick(e) {
    e.preventDefault();
    const px = rpEnsurePixels();
    const n = rpNormFromEvent(e);
    if (!px || !n) { rpStat('<span class="rp-warn">预览图还没准备好</span>'); return; }
    const ix = Math.min(RP.pxW - 1, Math.floor(n[0] * RP.pxW));
    const iy = Math.min(RP.pxH - 1, Math.floor(n[1] * RP.pxH));
    const o = (iy * RP.pxW + ix) * 4;
    const rgb = [px[o], px[o + 1], px[o + 2]];
    // 预览颜色一定来自调色板，但缩放可能有抗锯齿 -> 取最近的调色板颜色
    let best = null, bestD = Infinity;
    (PAL.groups || []).forEach((g) => {
        const d = (g.rgb[0] - rgb[0]) ** 2 + (g.rgb[1] - rgb[1]) ** 2
            + (g.rgb[2] - rgb[2]) ** 2;
        if (d < bestD) { bestD = d; best = g; }
    });
    if (!best) return;
    const hex = best.hex;
    if (RP.pickFor === "brush") {
        rpSetBrushColor(hex);
        rpStat("笔刷颜色：" + hex);
    } else {
        rpSetTarget(hex, "受害者方块：" + hex
            + (bestD > 0 ? "（贴到最近的调色板颜色）" : ""));
        rpStat(`受害者方块锁定为 <b>${hex}</b>，现在可以在预览图上圈选区了`);
    }
    RP.picking = false;
    RP.pickFor = null;
    rpUpdatePickButtons();
}

function rpUpdatePickButtons() {
    ["rp-pick-target", "rp-pick-brush"].forEach((id) => {
        const b = $(id);
        if (b) b.classList.toggle("picking", !!RP.picking && RP.pickFor === id);
    });
    const cv = RP.canvas;
    if (cv) cv.style.cursor = RP.picking ? "copy"
        : (RP.tool === "brush" ? "cell" : "crosshair");
}

// ------------------------------------------------------------ 执行
let rpTimer = null;
function rpRequest(msg) {
    if (rpTimer) clearTimeout(rpTimer);
    if (msg) setStatus(msg);
    rpTimer = setTimeout(() => { rpTimer = null; schedulePreview(0); }, 30);
}

/** 把「待执行的选区」提交成一个操作 */
function rpExecDenoise() {
    if (!RP.target) { rpStat('<span class="rp-warn">先选受害者方块</span>'); return; }
    if (!RP.pending) { rpStat('<span class="rp-warn">先在预览图上圈出选区</span>'); return; }
    RP.ops.push({ kind: "denoise", target: RP.target,
                  strength: RP.strength, lasso: RP.pending.slice() });
    RP.pending = null;
    undoPush();
    rpSyncUI();
    rpRequest("正在降噪…（受害者 " + RP.target + "）");
}

function rpExecFill() {
    if (!RP.pending) { rpStat('<span class="rp-warn">先圈出选区</span>'); return; }
    const hex = RP.target || RP.brushHex;
    if (!hex) { rpStat('<span class="rp-warn">先选一个颜色</span>'); return; }
    RP.ops.push({ kind: "fill", hex: hex, lasso: RP.pending.slice() });
    RP.pending = null;
    undoPush();
    rpSyncUI();
    rpRequest("正在填充 " + hex + "…");
}

function rpExecRevert() {
    if (!RP.pending) { rpStat('<span class="rp-warn">先圈出选区</span>'); return; }
    RP.ops.push({ kind: "revert", lasso: RP.pending.slice() });
    RP.pending = null;
    undoPush();
    rpSyncUI();
    rpRequest("正在把选区内还原成不抖动的样子…");
}

function rpClearOps(msg) {
    if (!RP.ops.length) { rpStat("没有已执行的修正"); return; }
    RP.ops = [];
    RP.pending = null;
    undoPush();
    rpSyncUI();
    rpRequest(msg || "已清除全部修正");
}

// ------------------------------------------------------------ 服务端回执
function rpNoteSize(w, h) {
    if (w && w > 0) RP.realW = w;
    if (h && h > 0) RP.realH = h;
}

function rpShowInfo(info) {
    RP.lastInfo = info;
    if (!info) return;
    if (info.warnings && info.warnings.length) {
        rpStat('<span class="rp-warn">' + info.warnings.join("；") + "</span>");
        return;
    }
    if (!info.op_count) return;
    const parts = [];
    if (info.denoised) parts.push(`降噪 <b>${info.denoised}</b> 个`);
    if (info.filled) parts.push(`填充 <b>${info.filled}</b> 个`);
    if (info.reverted) parts.push(`还原 <b>${info.reverted}</b> 个`);
    if (info.brush_pixels) parts.push(`画笔 <b>${info.brush_pixels}</b> 个`);
    if (!parts.length) { rpStat("这一步没有改动任何方块"); return; }
    rpStat(`已执行 ${info.op_count} 个操作：` + parts.join("，"));
}

// ------------------------------------------------------------ 界面绑定
function rpSetTool(tool) {
    RP.tool = tool;
    document.querySelectorAll(".rp-tab").forEach((b) => {
        b.classList.toggle("active", b.dataset.rptool === tool);
    });
    const a = $("rp-pane-lasso"), b = $("rp-pane-brush");
    if (a) a.hidden = tool !== "lasso";
    if (b) b.hidden = tool !== "brush";
    rpSyncPaletteHint();
    rpUpdatePickButtons();
    rpRedraw();
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
        b.addEventListener("click", (e) => {
            // 点色块作用于**当前工具**：
            //   套索 -> 设为受害者方块；画笔 -> 设为笔刷颜色
            // 按住 Shift 则作用于另一个（想改另一个颜色时不用切页签）
            const wantBrush = (RP.tool === "brush") !== e.shiftKey;
            if (wantBrush) {
                rpSetBrushColor(g.hex);
                rpStat(`笔刷颜色设为 <b>${g.hex}</b>，在预览图上按住涂抹即可`);
            } else {
                rpSetTarget(g.hex, "受害者方块：" + g.hex);
                rpStat(`受害者方块锁定为 <b>${g.hex}</b>，现在可以圈选区了`);
            }
        });
        frag.appendChild(b);
    });
    el.appendChild(frag);
    rpSyncUI();
}

/** 色块列表的小标题跟着当前工具变，别让人猜点一下到底改的是哪个 */
function rpSyncPaletteHint() {
    const el = $("rp-pal-hint");
    if (!el) return;
    el.innerHTML = (RP.tool === "brush")
        ? '点色块 = 设为 <b>笔刷颜色</b>（Shift+点 = 设为受害者方块）'
        : '点色块 = 设为 <b>受害者方块</b>（Shift+点 = 设为笔刷颜色）';
}

function rpLevelLabel() {
    const v = $("rp-level-v");
    const r = Math.max(1, Math.floor(RP.strength / 2));
    if (v) v.textContent = String(RP.strength);
    const d = $("rp-level-desc");
    if (d) {
        d.textContent = `邻域半径 ${r}×${r}：只在「${r * 2 + 1}×${r * 2 + 1} 个格子」的
            范围里看谁是多数。数值越大，成团的杂色清得越干净，但也更容易抹掉细小纹样。`
            .replace(/\s+/g, "");
    }
}

document.querySelectorAll(".rp-tab").forEach((b) => {
    b.addEventListener("click", () => rpSetTool(b.dataset.rptool));
});

$("rp-level").addEventListener("input", () => {
    RP.strength = parseInt($("rp-level").value, 10) || 5;
    rpLevelLabel();
});

$("rp-size").addEventListener("input", () => {
    RP.brushSize = parseInt($("rp-size").value, 10) || 6;
    $("rp-size-v").textContent = String(RP.brushSize);
});

$("rp-pick-target").addEventListener("click", () => {
    RP.picking = true; RP.pickFor = "rp-pick-target";
    rpUpdatePickButtons();
    setStatus("在预览图上点一下，把它取作受害者方块");
});

$("rp-pick-brush").addEventListener("click", () => {
    RP.picking = true; RP.pickFor = "rp-pick-brush";
    rpUpdatePickButtons();
    setStatus("在预览图上点一下，取作笔刷颜色");
});

$("rp-target-clear").addEventListener("click", () => {
    RP.target = null;
    rpSyncUI();
    rpStat("已取消受害者方块");
});

$("rp-exec").addEventListener("click", rpExecDenoise);
$("rp-fill").addEventListener("click", rpExecFill);
$("rp-revert").addEventListener("click", rpExecRevert);
$("rp-clear-sel").addEventListener("click", () => {
    RP.pending = null;
    rpSyncUI();
    rpStat("已清除当前选区");
});
$("rp-hide").addEventListener("click", () => rpSetOverlay(!RP.showOverlay));
$("rp-clear-all").addEventListener("click", () => rpClearOps());

// 快捷键：Esc 取消取色
document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && RP.picking) {
        RP.picking = false;
        RP.pickFor = null;
        rpUpdatePickButtons();
        setStatus("已取消取色");
    }
});

window.addEventListener("resize", () => {
    if (!RP.open) return;
    rpResize(); rpApplyTransform(); rpRedraw();
});

rpLevelLabel();
rpSetTool("lasso");
rpSetOverlay(true, true);
rpSyncUI();
rpSetOpen(false);
