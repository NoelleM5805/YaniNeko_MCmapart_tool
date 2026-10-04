// 86-projection-repair
// 投影噪点修改：输入 .litematic，输出修改后的 .litematic。
// 预览是「1 个方块 = 1 个像素」的调色板索引画布；操作序列由后端重放。

const PR = {
    key: "", filename: "", width: 0, height: 0,
    pixels: null, palette: [], keys: [], labels: [],
    ops: [], target: null, brushKey: null,
    tool: "lasso", brushSize: 6, strength: 5,
    pending: null, drawing: null,
    painting: false, lastPt: null, strokePoints: [],
    pendingStrokes: [], picking: false,
};

const PRZ = {
    scale: 1, tx: 0, ty: 0,
    dragging: false, sx: 0, sy: 0, stx: 0, sty: 0,
};

function prLog(msg) {
    const el = $("pr-log");
    if (!el) return;
    el.textContent = msg;
}

function prHexRgb(hex) {
    const h = String(hex || "").replace("#", "");
    return [parseInt(h.slice(0, 2), 16) || 0,
            parseInt(h.slice(2, 4), 16) || 0,
            parseInt(h.slice(4, 6), 16) || 0];
}

function prDecode(data) {
    const w = parseInt(data.pixels_width, 10) || 0;
    const h = parseInt(data.pixels_height, 10) || 0;
    if (!w || !h || !data.pixels_b64) return null;
    const bin = atob(data.pixels_b64);
    const n = w * h;
    const pixels = new Uint16Array(n);
    if ((data.pixels_encoding || "u16le") === "u8") {
        for (let i = 0; i < n; i++) pixels[i] = bin.charCodeAt(i) & 255;
    } else {
        for (let i = 0; i < n; i++) {
            pixels[i] = (bin.charCodeAt(i * 2) & 255)
                | ((bin.charCodeAt(i * 2 + 1) & 255) << 8);
        }
    }
    return {
        width: w, height: h, pixels: pixels,
        palette: data.palette || [],
        keys: data.palette_keys || [],
        labels: data.palette_labels || [],
    };
}

function prLoadPixelData(data) {
    const pc = prDecode(data);
    if (!pc) throw new Error("投影预览数据不完整");
    PR.width = pc.width; PR.height = pc.height;
    PR.pixels = pc.pixels; PR.palette = pc.palette;
    PR.keys = pc.keys; PR.labels = pc.labels;
    const cv = $("pr-canvas"), ov = $("pr-overlay");
    cv.width = PR.width; cv.height = PR.height;
    ov.width = PR.width; ov.height = PR.height;
    prRender();
    prDrawOverlay();
    prBuildPalette();
    prSyncUI();
}

function prRender() {
    const cv = $("pr-canvas");
    if (!cv || !PR.pixels) return;
    const ctx = cv.getContext("2d");
    ctx.imageSmoothingEnabled = false;
    const img = ctx.createImageData(PR.width, PR.height);
    const d = img.data;
    const rgbs = PR.palette.map(prHexRgb);
    for (let i = 0; i < PR.pixels.length; i++) {
        const c = rgbs[PR.pixels[i]] || [255, 0, 200];
        const o = i * 4;
        d[o] = c[0]; d[o + 1] = c[1]; d[o + 2] = c[2]; d[o + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
}

function prDrawOverlay() {
    const ov = $("pr-overlay");
    if (!ov) return;
    const ctx = ov.getContext("2d");
    ctx.clearRect(0, 0, ov.width, ov.height);
    if (PR.pending && PR.pending.length > 1) {
        ctx.strokeStyle = "rgba(255,140,0,.95)";
        ctx.fillStyle = "rgba(255,140,0,.12)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        PR.pending.forEach((p, i) => {
            const x = p[0] * ov.width, y = p[1] * ov.height;
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.closePath(); ctx.fill(); ctx.stroke();
    }
    if (PR.drawing && PR.drawing.length > 1) {
        ctx.strokeStyle = "rgba(255,140,0,.95)";
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        PR.drawing.forEach((p, i) => {
            const x = p[0] * ov.width, y = p[1] * ov.height;
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.stroke(); ctx.setLineDash([]);
    }
}

function prApplyTransform() {
    const t = `translate(${PRZ.tx}px, ${PRZ.ty}px) scale(${PRZ.scale})`;
    const cv = $("pr-canvas"), ov = $("pr-overlay");
    if (cv) cv.style.transform = t;
    if (ov) ov.style.transform = t;
}

function prFit() {
    const stage = $("pr-stage"), cv = $("pr-canvas");
    if (!stage || !cv || !cv.width || !cv.height) return;
    const aw = Math.max(40, stage.clientWidth - 32);
    const ah = Math.max(40, stage.clientHeight - 32);
    const s = Math.min(aw / cv.width, ah / cv.height);
    PRZ.scale = Math.max(0.05, Math.min(16, s));
    PRZ.tx = 0; PRZ.ty = 0;
    prApplyTransform();
}

function prZoomAt(factor, cx, cy) {
    const cv = $("pr-canvas");
    if (!cv) return;
    const ns = Math.max(0.05, Math.min(16, PRZ.scale * factor));
    const r = cv.getBoundingClientRect();
    const dx = cx - (r.left + r.width / 2);
    const dy = cy - (r.top + r.height / 2);
    const k = ns / PRZ.scale;
    PRZ.tx -= dx * (k - 1);
    PRZ.ty -= dy * (k - 1);
    PRZ.scale = ns;
    prApplyTransform();
}

function prBuildPalette() {
    const el = $("pr-pal");
    if (!el) return;
    el.textContent = "";
    PR.keys.forEach((key, i) => {
        if (i === 0) return; // 空气
        const b = document.createElement("button");
        b.type = "button";
        b.className = "rp-chip";
        b.dataset.key = key;
        b.style.background = PR.palette[i] || "#808080";
        b.title = PR.labels[i] || key;
        b.addEventListener("click", (e) => {
            if (PR.tool === "brush") {
                PR.brushKey = key;
                prLog("笔刷方块：" + (PR.labels[i] || key));
            } else {
                PR.target = key;
                prLog("目标方块：" + (PR.labels[i] || key));
            }
            prSyncUI();
        });
        el.appendChild(b);
    });
    prSyncUI();
}

function prSyncUI() {
    document.querySelectorAll("#pr-pal .rp-chip").forEach((c) => {
        c.classList.toggle("sel", c.dataset.key === PR.brushKey);
        c.classList.toggle("target", c.dataset.key === PR.target);
    });
    const ex = $("pr-exec"), fl = $("pr-fill"), rv = $("pr-revert");
    if (ex) ex.disabled = !(PR.target && PR.pending);
    if (fl) fl.disabled = !PR.pending;
    if (rv) rv.disabled = !PR.pending;
    const cs = $("pr-clear-sel");
    if (cs) cs.disabled = !PR.pending;
    const ab = $("pr-apply-brush"), cb = $("pr-cancel-brush");
    if (ab) {
        ab.disabled = PR.pendingStrokes.length === 0;
        ab.textContent = PR.pendingStrokes.length
            ? `✅ 应用画笔修改（${PR.pendingStrokes.length} 笔）`
            : "✅ 应用画笔修改";
    }
    if (cb) cb.disabled = PR.pendingStrokes.length === 0;
    const pk = $("pr-pick");
    if (pk) pk.classList.toggle("picking", !!PR.picking);
}

function prSetTool(tool) {
    PR.tool = tool;
    PR.picking = false;
    document.querySelectorAll(".rp-tab[data-prtool]").forEach((b) => {
        b.classList.toggle("active", b.dataset.prtool === tool);
    });
    const a = $("pr-pane-lasso"), b = $("pr-pane-brush");
    if (a) a.hidden = tool !== "lasso";
    if (b) b.hidden = tool !== "brush";
    prSyncUI();
}

function prPointFromEvent(e) {
    const ov = $("pr-overlay");
    const r = ov.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return null;
    const t = (e.touches && e.touches[0]) ? e.touches[0] : e;
    const nx = Math.max(0, Math.min(1, (t.clientX - r.left) / r.width));
    const ny = Math.max(0, Math.min(1, (t.clientY - r.top) / r.height));
    return { nx, ny, x: Math.floor(nx * PR.width), y: Math.floor(ny * PR.height) };
}

function prPick(e) {
    const p = prPointFromEvent(e);
    if (!p || !PR.pixels) return;
    const idx = PR.pixels[p.y * PR.width + p.x] | 0;
    const key = PR.keys[idx];
    if (!key || idx === 0) {
        prLog("这个位置是空气，没有可拾取的方块");
        return;
    }
    if (PR.tool === "brush") {
        PR.brushKey = key;
        prLog("笔刷方块：" + (PR.labels[idx] || key));
    } else {
        PR.target = key;
        prLog("目标方块：" + (PR.labels[idx] || key));
    }
    PR.picking = false;
    prSyncUI();
}

function prSetPixel(x, y, key) {
    const pi = PR.indexByKey(key);
    if (pi <= 0 || x < 0 || y < 0 || x >= PR.width || y >= PR.height) return;
    PR.pixels[y * PR.width + x] = pi;
}

PR.indexByKey = function (key) {
    return PR.keys.indexOf(key);
};

function prApplyBrushLocal(x, y, key, size) {
    // 和服务端 _brush_mask 完全一致：大小为 N 时就是 N×N，
    // 偶数尺寸不能写成 -N/2..+N/2（那会变成 N+1 格）。
    const ox = (size - 1) >> 1, oy = (size - 1) >> 1;
    for (let dy = 0; dy < size; dy++) {
        for (let dx = 0; dx < size; dx++) {
            prSetPixel(x - ox + dx, y - oy + dy, key);
        }
    }
    prRender();
}

function prLineBlocks(x0, y0, x1, y1) {
    const out = [];
    let dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0);
    const sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    let err = dx + dy;
    for (let guard = 0; guard < 8192; guard++) {
        out.push([x0, y0]);
        if (x0 === x1 && y0 === y1) break;
        const e2 = 2 * err;
        if (e2 >= dy) { err += dy; x0 += sx; }
        if (e2 <= dx) { err += dx; y0 += sy; }
    }
    return out;
}

function prExecDenoise() {
    if (!PR.target || !PR.pending) return;
    PR.ops.push({ kind: "denoise", target: PR.target,
                  strength: PR.strength, lasso: PR.pending.slice() });
    PR.pending = null; PR.drawing = null;
    prApplyOps("正在降噪…");
}

function prExecFill() {
    if (!PR.pending) return;
    const key = PR.target || PR.brushKey;
    if (!key) return;
    PR.ops.push({ kind: "fill", key: key, lasso: PR.pending.slice() });
    PR.pending = null; PR.drawing = null;
    prApplyOps("正在填充…");
}

function prExecRevert() {
    if (!PR.pending) return;
    PR.ops.push({ kind: "revert", lasso: PR.pending.slice() });
    PR.pending = null; PR.drawing = null;
    prApplyOps("正在还原…");
}

function prApplyOps(msg) {
    if (msg) prLog(msg);
    fetch("/api/projection-repair/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: PR.key, filename: PR.filename, ops: PR.ops }),
    }).then(r => r.json()).then((data) => {
        if (!data.ok) throw new Error(data.msg || "应用失败");
        prLoadPixelData(data);
        prLog(`已应用 ${PR.ops.length} 个操作`);
    }).catch((e) => prLog("失败：" + e.message));
}

function prApplyBrush() {
    if (!PR.pendingStrokes.length) return;
    PR.pendingStrokes.forEach((s) => {
        PR.ops.push({ kind: "brush", key: s.key, size: s.size, points: s.points });
    });
    PR.pendingStrokes = [];
    prApplyOps("正在提交画笔修改…");
}

function prCancelBrush() {
    PR.pendingStrokes = [];
    prApplyOps("已取消未提交的画笔修改");
}

function prFinishBrushStroke() {
    if (!PR.strokePoints.length || !PR.brushKey) {
        PR.painting = false; PR.lastPt = null; PR.strokePoints = [];
        prSyncUI();
        return;
    }
    const pts = PR.strokePoints.map(([x, y]) => [(x + 0.5) / PR.width,
                                                 (y + 0.5) / PR.height]);
    PR.pendingStrokes.push({ key: PR.brushKey, size: PR.brushSize, points: pts });
    PR.painting = false; PR.lastPt = null; PR.strokePoints = [];
    prSyncUI();
    prLog(`已暂存 ${PR.pendingStrokes.length} 笔画笔，点「应用画笔修改」提交`);
}

function prBindOverlay() {
    const ov = $("pr-overlay");
    if (!ov || ov.dataset.bound) return;
    ov.dataset.bound = "1";
    ov.addEventListener("mousedown", (e) => {
        // 右键拖动：自由平移预览图。左键留给取色 / 套索 / 画笔。
        if (e.button === 2) {
            e.preventDefault();
            PRZ.dragging = true;
            PRZ.sx = e.clientX; PRZ.sy = e.clientY;
            PRZ.stx = PRZ.tx; PRZ.sty = PRZ.ty;
            return;
        }
        if (e.button !== 0) return;
        const p = prPointFromEvent(e);
        if (!p) return;
        e.preventDefault();
        if (PR.picking || e.altKey) {
            prPick(e);
            return;
        }
        if (PR.tool === "lasso") {
            if (!PR.target) { prLog("请先点一个目标方块"); return; }
            PR.drawing = [[p.nx, p.ny]];
            prDrawOverlay();
        } else {
            if (!PR.brushKey) { prLog("请先点一个笔刷方块"); return; }
            PR.painting = true;
            PR.lastPt = [p.x, p.y];
            PR.strokePoints = [[p.x, p.y]];
            prApplyBrushLocal(p.x, p.y, PR.brushKey, PR.brushSize);
        }
    });
    ov.addEventListener("mousemove", (e) => {
        const p = prPointFromEvent(e);
        if (!p) return;
        if (PR.drawing) {
            PR.drawing.push([p.nx, p.ny]);
            prDrawOverlay();
        } else if (PR.painting) {
            if (PR.lastPt && PR.lastPt[0] === p.x && PR.lastPt[1] === p.y) return;
            const line = prLineBlocks(PR.lastPt[0], PR.lastPt[1], p.x, p.y);
            line.forEach(([x, y]) => {
                PR.strokePoints.push([x, y]);
                prApplyBrushLocal(x, y, PR.brushKey, PR.brushSize);
            });
            PR.lastPt = [p.x, p.y];
        }
    });
    window.addEventListener("mousemove", (e) => {
        if (!PRZ.dragging) return;
        PRZ.tx = PRZ.stx + (e.clientX - PRZ.sx);
        PRZ.ty = PRZ.sty + (e.clientY - PRZ.sy);
        prApplyTransform();
    });

    window.addEventListener("mouseup", () => {
        if (PRZ.dragging) {
            PRZ.dragging = false;
            return;
        }
        if (PR.drawing) {
            const keep = [];
            PR.drawing.forEach((p) => {
                if (!keep.length || Math.hypot(p[0] - keep[keep.length - 1][0],
                                               p[1] - keep[keep.length - 1][1]) > 0.004) {
                    keep.push(p);
                }
            });
            PR.pending = keep.length >= 3 ? keep : null;
            PR.drawing = null;
            prDrawOverlay();
            prSyncUI();
        }
        if (PR.painting) prFinishBrushStroke();
    });
    ov.addEventListener("touchstart", (e) => {
        if (PR.tool !== "lasso") return;
        e.preventDefault();
        const p = prPointFromEvent(e);
        if (p) { PR.drawing = [[p.nx, p.ny]]; prDrawOverlay(); }
    }, { passive: false });
    ov.addEventListener("touchmove", (e) => {
        if (!PR.drawing) return;
        e.preventDefault();
        const p = prPointFromEvent(e);
        if (p) { PR.drawing.push([p.nx, p.ny]); prDrawOverlay(); }
    }, { passive: false });
    ov.addEventListener("touchend", () => {
        if (PR.drawing) {
            PR.pending = PR.drawing.length >= 3 ? PR.drawing : null;
            PR.drawing = null; prDrawOverlay(); prSyncUI();
        }
    });

    const stage = $("pr-stage");
    if (stage) {
        stage.addEventListener("wheel", (e) => {
            e.preventDefault();
            prZoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX, e.clientY);
        }, { passive: false });
        stage.addEventListener("contextmenu", (e) => e.preventDefault());
        stage.addEventListener("dblclick", (e) => {
            e.preventDefault();
            prFit();
        });
    }
}

function prResetUI() {
    PR.key = ""; PR.filename = ""; PR.ops = []; PR.pending = null;
    PR.pendingStrokes = []; PR.drawing = null; PR.pixels = null;
    $("pr-stage").hidden = true;
    $("pr-empty").hidden = false;
    $("pr-tools-sec").hidden = true;
    $("pr-run-sec").hidden = true;
    prLog("等待上传…");
}

bindDropZone("pr-drop", "pr-file", async (f) => {
    if (!f.name.toLowerCase().endsWith(".litematic")) {
        alert("请选择 .litematic 投影文件");
        return;
    }
    prResetUI();
    PR.filename = f.name;
    $("pr-file-name").textContent = f.name;
    $("pr-file-size").textContent = (f.size / 1024).toFixed(1) + " KB";
    $("pr-file-info").hidden = false;
    prLog("正在读取投影…");
    const fd = new FormData();
    fd.append("file", f);
    try {
        const res = await fetch("/api/projection-repair/load", { method: "POST", body: fd });
        const data = await res.json();
        if (!data.ok) throw new Error(data.msg || "读取失败");
        PR.key = data.key;
        PR.filename = data.filename || f.name;
        prLoadPixelData(data);
        $("pr-empty").hidden = true;
        $("pr-stage").hidden = false;
        $("pr-tools-sec").hidden = false;
        $("pr-run-sec").hidden = false;
        $("pr-size-info").textContent = `投影尺寸：${data.width} × ${data.height} 方块`;
        prLog(`已载入：${data.width}×${data.height}，${data.blocks} 个方块`);
        setTimeout(() => { prFit(); }, 30);
    } catch (e) {
        prLog("失败：" + e.message);
    }
}, null);

$("pr-file-clear").addEventListener("click", (e) => {
    e.stopPropagation();
    prResetUI();
    clearFileInfo("pr");
});

document.querySelectorAll(".rp-tab[data-prtool]").forEach((b) => {
    b.addEventListener("click", () => prSetTool(b.dataset.prtool));
});

$("pr-pick").addEventListener("click", () => {
    PR.picking = !PR.picking;
    prLog(PR.picking ? "在预览图上点一下取目标方块" : "已取消取色");
    prSyncUI();
});

$("pr-level").addEventListener("input", () => {
    PR.strength = parseInt($("pr-level").value, 10) || 5;
    $("pr-level-v").textContent = String(PR.strength);
});

$("pr-size").addEventListener("input", () => {
    PR.brushSize = parseInt($("pr-size").value, 10) || 6;
    $("pr-size-v").textContent = String(PR.brushSize);
});

$("pr-exec").addEventListener("click", prExecDenoise);
$("pr-fill").addEventListener("click", prExecFill);
$("pr-revert").addEventListener("click", prExecRevert);
$("pr-clear-sel").addEventListener("click", () => {
    PR.pending = null; prDrawOverlay(); prSyncUI();
});
$("pr-apply-brush").addEventListener("click", prApplyBrush);
$("pr-cancel-brush").addEventListener("click", prCancelBrush);
$("pr-undops").addEventListener("click", () => {
    if (!PR.ops.length) return;
    PR.ops.pop();
    prApplyOps("已撤回一步");
});

$("pr-save").addEventListener("click", async () => {
    prLog("正在生成修改后的投影…");
    try {
        const res = await fetch("/api/projection-repair/process", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key: PR.key, filename: PR.filename, ops: PR.ops }),
        });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.msg || "生成失败");
        }
        const blob = await res.blob();
        const base = PR.filename.replace(/\.litematic$/i, "");
        const name = base + "_repair.litematic";
        if (typeof saveBlob === "function") await saveBlob(blob, name);
        else {
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = name;
            a.click();
        }
        prLog("已生成：" + name);
    } catch (e) {
        prLog("失败：" + e.message);
    }
});

prBindOverlay();
prSyncUI();
