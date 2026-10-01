// 89-undo
// 全局撤回：把「会影响成品结果」的编辑状态存成快照，最多 20 条

// ============================================================
// 全局撤回
// ============================================================
// 一次快照 = 影响成品的全部编辑状态：
//     局部噪点修正的操作序列 + 方块选择 + 图片调整 + 算法/抖动/尺寸表单 + 同色分配策略
//
// 为什么是「快照整个状态」而不是「记住每一步做了什么」：
//   操作序列本身已经记录了修正步骤，但方块选择、图片调整、算法这些同样会改变成品。
//   存整份状态，撤回就是「回到之前那一刻」，不用担心漏记了哪类改动。
//
// 上限 20 条：超出就把最旧的一条挤掉（那份状态再也回不去，但当前结果不受影响）。
const UNDO = {
    stack: [],
    index: -1,          // 当前处在 stack 的第几条
    max: 20,
    restoring: false,   // 正在回滚：期间不要再记录新快照
};

const UNDO_FORM_IDS = ["mp-algo", "mp-dither", "mp-strength", "mp-size-mode",
    "mp-fit", "mp-grid-x", "mp-grid-y", "mp-max-size"];

/** 当前编辑状态的一份深拷贝 */
function undoSnapshot() {
    const o = {
        ops: JSON.parse(JSON.stringify((typeof RP !== "undefined" && RP.ops) || [])),
        blocks: (typeof PAL !== "undefined" && PAL.loaded && PAL.selected)
            ? Array.from(PAL.selected) : null,
        adjust: Object.assign({}, ADJ),
        form: {},
        alloc: (typeof CFG !== "undefined") ? (CFG.alloc || "random") : "random",
    };
    UNDO_FORM_IDS.forEach(id => {
        const el = $(id);
        if (el) o.form[id] = el.value;
    });
    return o;
}

function undoSignature(o) {
    return JSON.stringify([o.ops, o.blocks, o.adjust, o.form, o.alloc]);
}

/**
 * 记录一条状态。在一次「离散的编辑动作」完成之后调用。
 * 和当前状态一模一样就不重复记，免得来回拖滑块刷出一堆空步骤。
 */
function undoPush() {
    if (UNDO.restoring) return;
    const snap = undoSnapshot();
    const sig = undoSignature(snap);
    if (UNDO.index >= 0 && UNDO.stack[UNDO.index]
        && undoSignature(UNDO.stack[UNDO.index]) === sig) {
        return;
    }
    // 在历史中间做了新动作 -> 丢掉后面的重做分支
    UNDO.stack = UNDO.stack.slice(0, UNDO.index + 1);
    UNDO.stack.push(snap);
    while (UNDO.stack.length > UNDO.max) UNDO.stack.shift();
    UNDO.index = UNDO.stack.length - 1;
    undoUpdateUI();
}

/** 把一条快照写回界面（不触发新的记录） */
function undoRestore(snap) {
    if (!snap) return;
    UNDO.restoring = true;
    try {
        if (typeof RP !== "undefined") {
            RP.ops = JSON.parse(JSON.stringify(snap.ops || []));
            RP.pending = null;
        }
        // 方块选择：palApply 会顺带刷新面板；record=false 避免又记一条
        if (snap.blocks && typeof palApply === "function"
            && typeof PAL !== "undefined" && PAL.loaded) {
            palApply(snap.blocks.filter(id => PAL.valid.has(id)), false);
        }
        ADJ_KEYS.forEach(k => { ADJ[k] = 0; });
        Object.assign(ADJ, snap.adjust || {});
        adjSyncLabels();
        UNDO_FORM_IDS.forEach(id => {
            const el = $(id);
            if (el && snap.form && snap.form[id] !== undefined) el.value = snap.form[id];
        });
        if (typeof onSizeModeChange === "function") onSizeModeChange();
        const sv = $("mp-strength-val");
        if (sv) sv.textContent = $("mp-strength").value;
        CFG.alloc = snap.alloc || "random";
        if ($("pal-alloc")) $("pal-alloc").value = CFG.alloc;
        if (typeof rpSyncUI === "function") rpSyncUI();
    } finally {
        UNDO.restoring = false;
    }
    schedulePreview(0);
    undoUpdateUI();
}

function undoStep() {
    if (UNDO.index <= 0) return false;
    UNDO.index--;
    undoRestore(UNDO.stack[UNDO.index]);
    setStatus("已撤回（还剩 " + UNDO.index + " 步可退）");
    return true;
}

function redoStep() {
    if (UNDO.index >= UNDO.stack.length - 1) return false;
    UNDO.index++;
    undoRestore(UNDO.stack[UNDO.index]);
    setStatus("已重做（第 " + (UNDO.index + 1) + "/" + UNDO.stack.length + " 步）");
    return true;
}

/** 换图片 / 恢复默认时清空历史 */
function undoReset() {
    UNDO.stack = [];
    UNDO.index = -1;
    undoUpdateUI();
}

function undoUpdateUI() {
    const b = $("rp-undo"), r = $("rp-redo"), t = $("undo-state");
    const canU = UNDO.index > 0;
    const canR = UNDO.index >= 0 && UNDO.index < UNDO.stack.length - 1;
    if (b) { b.disabled = !canU; b.title = canU ? "回到上一步" : "没有可撤回的步骤"; }
    if (r) { r.disabled = !canR; r.title = canR ? "重新做下一步" : "没有可重做的步骤"; }
    if (t) {
        t.textContent = UNDO.stack.length
            ? ("第 " + (UNDO.index + 1) + " / " + UNDO.stack.length + " 步（最多 " + UNDO.max + " 步）")
            : "还没有可撤回的编辑";
    }
}

$("rp-undo").addEventListener("click", () => undoStep());
$("rp-redo").addEventListener("click", () => redoStep());

// Ctrl+Z / Ctrl+Shift+Z（也接受 Ctrl+Y）
document.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    const tag = (e.target && e.target.tagName) || "";
    if (/^(INPUT|TEXTAREA)$/.test(tag) && e.target.type !== "range") return;
    const k = e.key.toLowerCase();
    if (k === "z" && !e.shiftKey) {
        e.preventDefault();
        undoStep();
    } else if ((k === "z" && e.shiftKey) || k === "y") {
        e.preventDefault();
        redoStep();
    }
});
