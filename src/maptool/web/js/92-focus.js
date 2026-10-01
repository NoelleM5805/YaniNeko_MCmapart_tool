// 92-focus
// 自由预览（专注模式）

        // ============================================================
        // 自由预览（专注模式）
        //   开启：只留左侧「图片调整」栏，其余界面隐藏；预览图可自由缩放拖动
        //   复原：回到原样，重新显示原图
        // ============================================================
        const FOCUS = {
            scale: 1, tx: 0, ty: 0,
            dragging: false, sx: 0, sy: 0, stx: 0, sty: 0,
            savedScroll: 0, fitted: false, attached: false,
        };

        function focusOn() {
            return document.body.classList.contains("focus-mode");
        }

        function focusImg() {
            const box = $("mp-preview-pal");
            return box ? box.querySelector("img") : null;
        }

        function focusApply() {
            const img = focusImg();
            if (!img) return;
            img.style.transform =
                `translate(${FOCUS.tx}px, ${FOCUS.ty}px) scale(${FOCUS.scale})`;
            // 修正的选区覆盖层要跟着图片一起变换，否则一缩放就错位
            if (typeof rpApplyTransform === "function") rpApplyTransform();
        }

        function focusResetTransform() {
            FOCUS.scale = 1; FOCUS.tx = 0; FOCUS.ty = 0;
            FOCUS.dragging = false;
            focusApply();
        }

        /** 让图片刚好铺满可视区域（进入专注模式时自动做一次） */
        function focusFit() {
            const img = focusImg(), box = $("mp-preview-pal");
            if (!img || !box || !img.naturalWidth) return;
            const pad = 28;
            const aw = Math.max(40, box.clientWidth - pad);
            const ah = Math.max(40, box.clientHeight - pad);
            const s = Math.min(aw / img.naturalWidth, ah / img.naturalHeight);
            FOCUS.scale = Math.max(0.05, Math.min(16, s));
            FOCUS.tx = 0; FOCUS.ty = 0;
            focusApply();
        }

        function focusZoomAt(factor, cx, cy) {
            const img = focusImg();
            if (!img) return;
            const ns = Math.max(0.05, Math.min(16, FOCUS.scale * factor));
            const r = img.getBoundingClientRect();
            const dx = cx - (r.left + r.width / 2);
            const dy = cy - (r.top + r.height / 2);
            const k = ns / FOCUS.scale;
            FOCUS.tx -= dx * (k - 1);
            FOCUS.ty -= dy * (k - 1);
            FOCUS.scale = ns;
            focusApply();
        }

        /** 左栏宽度/位置可能随窗口变化，这里算出专注模式下左栏该贴在哪 */
        function updateFocusLeft() {
            const app = document.querySelector(".app");
            if (!app) return;
            const left = Math.max(12, Math.round(app.getBoundingClientRect().left) + 26);
            document.documentElement.style.setProperty("--focus-left", left + "px");
        }

        /** 只在真正进入专注模式时才需要，绑一次即可（用事件委托，图片被重绘也不影响） */
        function focusAttach() {
            if (FOCUS.attached) return;
            FOCUS.attached = true;
            const box = $("mp-preview-pal");
            if (!box) return;

            box.addEventListener("wheel", (e) => {
                if (!focusOn()) return;
                e.preventDefault();
                focusZoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX, e.clientY);
            }, { passive: false });

            // 右键拖动预览图（左键留给套索 / 画笔）。
            // 覆盖画布正好盖在图片上，所以左键落在画布上时交给工具，
            // 只有右键（或没有覆盖画布时的左键）才用来平移。
            box.addEventListener("contextmenu", (e) => {
                if (focusOn()) e.preventDefault();
            });

            box.addEventListener("mousedown", (e) => {
                if (!focusOn()) return;
                const img = focusImg();
                if (!img) return;
                const onOverlay = typeof RP !== "undefined" && RP && RP.open
                    && RP.canvas && e.target === RP.canvas;
                const pan = (e.button === 2) || (e.button === 0 && !onOverlay);
                if (!pan) return;
                e.preventDefault();
                FOCUS.dragging = true;
                FOCUS.sx = e.clientX; FOCUS.sy = e.clientY;
                FOCUS.stx = FOCUS.tx; FOCUS.sty = FOCUS.ty;
                img.classList.add("grabbing");
            });

            window.addEventListener("mousemove", (e) => {
                if (!FOCUS.dragging) return;
                FOCUS.tx = FOCUS.stx + (e.clientX - FOCUS.sx);
                FOCUS.ty = FOCUS.sty + (e.clientY - FOCUS.sy);
                focusApply();
            });

            window.addEventListener("mouseup", () => {
                if (!FOCUS.dragging) return;
                FOCUS.dragging = false;
                const img = focusImg();
                if (img) img.classList.remove("grabbing");
            });

            // 双击复位视图
            box.addEventListener("dblclick", (e) => {
                if (!focusOn()) return;
                e.preventDefault();
                focusFit();
            });

            // 触摸拖动
            box.addEventListener("touchstart", (e) => {
                if (!focusOn() || e.touches.length !== 1) return;
                const t = e.touches[0];
                FOCUS.dragging = true;
                FOCUS.sx = t.clientX; FOCUS.sy = t.clientY;
                FOCUS.stx = FOCUS.tx; FOCUS.sty = FOCUS.ty;
            }, { passive: true });

            box.addEventListener("touchmove", (e) => {
                if (!FOCUS.dragging || e.touches.length !== 1) return;
                const t = e.touches[0];
                FOCUS.tx = FOCUS.stx + (t.clientX - FOCUS.sx);
                FOCUS.ty = FOCUS.sty + (t.clientY - FOCUS.sy);
                focusApply();
            }, { passive: true });

            box.addEventListener("touchend", () => { FOCUS.dragging = false; });

            window.addEventListener("resize", () => {
                if (focusOn()) updateFocusLeft();
            });
        }

        function focusEnter() {
            FOCUS.savedScroll = window.scrollY || 0;
            updateFocusLeft();
            document.body.classList.add("focus-mode");
            $("focus-bar").hidden = false;
            focusAttach();
            FOCUS.fitted = false;
            // 用定时器而不是 requestAnimationFrame：无头浏览器里 rAF 可能完全不触发
            setTimeout(() => {
                if (focusOn() && !FOCUS.fitted) { focusFit(); FOCUS.fitted = true; }
            }, 20);
        }

        function focusExit() {
            document.body.classList.remove("focus-mode");
            $("focus-bar").hidden = true;
            focusResetTransform();
            FOCUS.fitted = false;
            window.scrollTo(0, FOCUS.savedScroll || 0);
        }

        // 隐藏原图 / 复原
        function applySoloPreview() {
            const row = $("mp-preview-row");
            if (row) row.classList.toggle("solo", !!CFG.soloPreview);
            const sw = $("set-solo-preview");
            if (sw) sw.classList.toggle("on", !!CFG.soloPreview);
            if (CFG.soloPreview) {
                if (!focusOn()) focusEnter();
            } else if (focusOn()) {
                focusExit();
            }
            // 「局部噪点修正」只在隐藏原图（专注模式）下开放：
            // 关掉时左边栏不显示这一块，预览图上的覆盖画布也摘掉，
            // 点击预览图恢复成原来的「点击放大」。
            if (typeof rpSetOpen === "function") rpSetOpen(!!CFG.soloPreview);
        }

        $("set-solo-preview").addEventListener("click", () => {
            CFG.soloPreview = !CFG.soloPreview;
            cfgSave(true);
            applySoloPreview();
        });

        $("focus-restore").addEventListener("click", () => {
            CFG.soloPreview = false;
            cfgSave(true);
            applySoloPreview();
            setStatus("已复原：重新显示原图");
        });

        // ESC 也能退出专注模式
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && focusOn()) {
                CFG.soloPreview = false;
                cfgSave(true);
                applySoloPreview();
            }
        });
