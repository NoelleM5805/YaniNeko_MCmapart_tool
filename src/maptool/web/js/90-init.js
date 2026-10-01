// 90-init
// 初始化：读配置、把配置贴到界面上

        // ============================================================
        // 初始化
        // ============================================================
        cfgLoad();
        applySettingsToUI();
        applyTheme();
        formApply(CFG.form);
        onSizeModeChange();

        ADJ_KEYS.forEach(k => {
            if (CFG.adjust && typeof CFG.adjust[k] === "number") {
                ADJ[k] = Math.max(-100, Math.min(100, CFG.adjust[k]));
            }
        });
        adjSyncLabels();

        // 表单改动后顺带记进配置（防抖，跟随预览节奏）
        // change 才算一次编辑，记一条可撤回的状态（见 89-undo.js）
        FORM_FIELDS.forEach(id => {
            const el = $(id);
            el.addEventListener("input", formPersist);
            el.addEventListener("change", formPersist);
            el.addEventListener("change", () => {
                if (typeof undoPush === "function") undoPush();
            });
        });

        // 同色分配策略
        $("pal-alloc").value = CFG.alloc || "random";
        refreshPresetSelect();
