// 30-adjust
// 图片调整 12 项

        // ============================================================
        // 图片调整（左侧边栏）
        // ============================================================
        const ADJ_KEYS = ["exposure", "contrast", "saturation", "brightness",
            "highlights", "shadows", "temperature", "tint",
            "sharpen", "clarity", "dispersion", "vignette"];
        const ADJ_UNIPOLAR = ["sharpen", "clarity", "vignette"];
        const ADJ = {};
        ADJ_KEYS.forEach(k => ADJ[k] = 0);

        function adjActive() {
            return ADJ_KEYS.some(k => ADJ[k] !== 0);
        }

        function adjSyncLabels() {
            ADJ_KEYS.forEach(k => {
                const el = $("adj-" + k + "-v");
                if (el) el.textContent = (ADJ[k] > 0 ? "+" : "") + ADJ[k];
                const r = $("adj-" + k);
                if (r && r.value !== String(ADJ[k])) r.value = ADJ[k];
            });
        }

        function adjSet(key, val) {
            ADJ[key] = Math.max(-100, Math.min(100, parseInt(val, 10) || 0));
            if (ADJ_UNIPOLAR.includes(key) && ADJ[key] < 0) ADJ[key] = 0;
            adjSyncLabels();
            CFG.adjust = Object.assign({}, ADJ);
            cfgSave();
            schedulePreview(220);
            setStatus("图片调整：" + (adjActive()
                ? ADJ_KEYS.filter(k => ADJ[k]).map(k => k + " " + ADJ[k]).join("，")
                : "无"));
        }

        ADJ_KEYS.forEach(k => {
            const r = $("adj-" + k);
            if (!r) return;
            r.addEventListener("input", () => adjSet(k, r.value));
        });

        $("adj-reset").addEventListener("click", () => {
            ADJ_KEYS.forEach(k => ADJ[k] = 0);
            adjSyncLabels();
            CFG.adjust = {};
            cfgSave();
            schedulePreview(120);
            setStatus("图片调整已恢复默认");
        });
