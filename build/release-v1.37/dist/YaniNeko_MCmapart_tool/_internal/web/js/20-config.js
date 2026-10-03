// 20-config
// 配置持久化 + 主题（黑夜模式）

        // ============================================================
        // 配置持久化（自动记住上次用的设置）
        // ============================================================
        const CFG_KEY = "mapart.config.v2";

        const CFG = {
            persist: true,
            theme: "auto",          // light | dark | auto
            icons: true,
            hex: true,
            iconPx: 16,
            previewSide: 384,
            exitOnClose: true,      // 关掉网页后自动结束服务进程
            soloPreview: false,     // 隐藏原图、放大预览
            alloc: "random",        // 同色多选时的分配策略
            prio: {},               // 每个颜色组的优先方块 {hex: id}
            blocks: null,           // null = 用服务端默认
            adjust: {},             // 曝光/亮度/...
            form: {},               // 算法/抖动/尺寸等表单
        };

        function cfgLoad() {
            let raw = null;
            try { raw = localStorage.getItem(CFG_KEY); } catch (e) { return; }
            if (!raw) return;
            try {
                const o = JSON.parse(raw);
                if (!o || typeof o !== "object") return;
                if (typeof o.persist === "boolean") CFG.persist = o.persist;
                if (!CFG.persist) return;       // 关掉记忆就不再恢复
                ["theme", "icons", "hex", "iconPx", "previewSide",
                    "exitOnClose", "soloPreview", "alloc", "prio",
                    "blocks", "adjust", "form"].forEach(k => {
                        if (o[k] !== undefined && o[k] !== null) CFG[k] = o[k];
                    });
            } catch (e) { /* 配置损坏就当没有 */ }
        }

        let cfgTimer = null;

        function cfgSave(now) {
            if (!CFG.persist) return;
            if (cfgTimer) clearTimeout(cfgTimer);
            const write = () => {
                cfgTimer = null;
                try { localStorage.setItem(CFG_KEY, JSON.stringify(CFG)); } catch (e) { }
            };
            if (now) write();
            else cfgTimer = setTimeout(write, 400);
        }

        function cfgReset() {
            try { localStorage.removeItem(CFG_KEY); } catch (e) { }
        }


        // ============================================================
        // 主题（黑夜模式）
        // ============================================================
        const mqDark = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

        function applyTheme() {
            const dark = CFG.theme === "dark" || (CFG.theme === "auto" && mqDark && mqDark.matches);
            document.body.classList.toggle("dark", !!dark);
            const meta = document.querySelector('meta[name="theme-color"]');
            if (meta) meta.setAttribute("content", dark ? "#0B0D10" : "#EDF0F4");
        }

        if (mqDark && mqDark.addEventListener) {
            mqDark.addEventListener("change", () => { if (CFG.theme === "auto") applyTheme(); });
        }
