// 60-settings
// 设置面板 + 表单值 ⇄ 配置

        // ============================================================
        // 设置面板
        // ============================================================
        function switchSet(el, on) {
            el.classList.toggle("on", !!on);
        }

        function setInfo() {
            $("set-info").innerHTML = CFG.persist
                ? "配置已自动保存在浏览器本地，刷新后自动恢复。"
                : '<span style="color:#c47f00">配置记忆已关闭，刷新后会回到默认值。</span>';
        }

        function bindSwitch(id, key, onChange) {
            const el = $(id);
            switchSet(el, CFG[key]);
            el.addEventListener("click", () => {
                CFG[key] = !CFG[key];
                switchSet(el, CFG[key]);
                cfgSave(true);
                if (onChange) onChange(CFG[key]);
            });
        }

        $("set-theme").addEventListener("change", () => {
            CFG.theme = $("set-theme").value;
            cfgSave(true);
            applyTheme();
        });

        $("set-icon-px").addEventListener("change", () => {
            CFG.iconPx = parseInt($("set-icon-px").value, 10) || 16;
            cfgSave(true);
            applyDisplayOpts();
        });

        $("set-preview-side").addEventListener("change", () => {
            CFG.previewSide = parseInt($("set-preview-side").value, 10) || 384;
            cfgSave(true);
            schedulePreview(120);
        });

        bindSwitch("set-icons", "icons", () => { applyDisplayOpts(); });
        bindSwitch("set-hex", "hex", () => { applyDisplayOpts(); });
        bindSwitch("set-persist", "persist", () => { setInfo(); cfgSave(true); });
        bindSwitch("set-exit-on-close", "exitOnClose", () => { keepaliveConnect(); });

        // ============================================================
        // 页面保活：关掉网页后让服务端自己退出
        //   用 SSE 长连接而不是定时 ping —— 后台标签页的定时器会被浏览器
        //   限流（可能一分钟才跑一次），长连接则随页面关闭立刻断开。
        // ============================================================
        const KA = { src: null };

        function keepaliveConnect() {
            if (KA.src) {
                KA.src.close();
                KA.src = null;
            }
            if (!window.EventSource) return;
            try {
                KA.src = new EventSource(
                    "/api/keepalive?exit_on_close=" + (CFG.exitOnClose ? 1 : 0));
            } catch (e) {
                console.warn("保活连接建立失败：", e);
            }
        }

        // 关页面时顺手断掉，让服务端尽快察觉（不断也行，连接会自己断）
        window.addEventListener("pagehide", () => {
            if (KA.src) { try { KA.src.close(); } catch (e) { } KA.src = null; }
        });

        $("set-reset").addEventListener("click", () => {
            if (!confirm("恢复默认配置？方块选择、图片调整和所有设置都会重置。")) return;
            cfgReset();
            location.reload();
        });

        // 表单值 <-> CFG.form
        const FORM_FIELDS = ["mp-algo", "mp-dither", "mp-strength", "mp-size-mode",
            "mp-fit", "mp-grid-x", "mp-grid-y", "mp-max-size"];

        function formCollect() {
            const o = {};
            FORM_FIELDS.forEach(id => { o[id] = $(id).value; });
            return o;
        }

        function formApply(o) {
            if (!o) return;
            FORM_FIELDS.forEach(id => {
                if (o[id] !== undefined && o[id] !== null) $(id).value = o[id];
            });
            $("mp-strength-val").textContent = $("mp-strength").value;
        }

        function formPersist() {
            CFG.form = formCollect();
            cfgSave();
        }

        function applySettingsToUI() {
            $("set-theme").value = CFG.theme;
            $("set-icon-px").value = String(CFG.iconPx);
            $("set-preview-side").value = String(CFG.previewSide);
            switchSet($("set-icons"), CFG.icons);
            switchSet($("set-hex"), CFG.hex);
            switchSet($("set-persist"), CFG.persist);
            switchSet($("set-exit-on-close"), CFG.exitOnClose);
            setInfo();
        }
