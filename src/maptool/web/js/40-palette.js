// 40-palette
// 方块选择面板（按颜色分组 / 预设 / 置顶）

        // ============================================================
        // 方块选择（按颜色分组 · 色块 + 色号 + 图标）
        // ============================================================
        const PAL = {
            groups: [], defaults: [], valid: new Set(),
            selected: new Set(), loaded: false, undo: [],
        };

        const PRESET_KEY = "mapart.presets.v1";

        // 内置预设：按方块 ID 后缀归类，不需要后端参与
        const BUILTIN_PRESETS = [
            { name: "全部方块", pick: null },
            { name: "每色一个", pick: "defaults" },
            { name: "仅地毯", match: "_carpet" },
            { name: "仅混凝土", match: "_concrete" },
            { name: "仅陶瓦（含带釉）", match: "_terracotta" },
            { name: "仅带釉陶瓦", match: "_glazed_terracotta" },
        ];

        function loadPresets() {
            try {
                const raw = localStorage.getItem(PRESET_KEY);
                const o = raw ? JSON.parse(raw) : null;
                return (o && typeof o === "object") ? o : {};
            } catch (e) { return {}; }
        }

        function savePresets(o) {
            try { localStorage.setItem(PRESET_KEY, JSON.stringify(o)); } catch (e) { }
        }

        function presetIds(p) {
            if (!p) return null;
            if (p.pick === "defaults") return PAL.defaults.slice();
            if (p.match) return [...PAL.valid].filter(id => id.endsWith(p.match));
            if (Array.isArray(p.ids)) return p.ids.filter(id => PAL.valid.has(id));
            return [...PAL.valid];
        }

        function refreshPresetSelect() {
            const sel = $("pal-preset");
            const custom = loadPresets();
            const cur = sel.value;
            sel.innerHTML = '<option value="">套用预设…</option>';
            const g1 = document.createElement("optgroup");
            g1.label = "内置";
            BUILTIN_PRESETS.forEach((p, i) => {
                const o = document.createElement("option");
                o.value = "b:" + i;
                o.textContent = p.name;
                g1.appendChild(o);
            });
            sel.appendChild(g1);
            const names = Object.keys(custom);
            if (names.length) {
                const g2 = document.createElement("optgroup");
                g2.label = "我的预设";
                names.forEach(n => {
                    const o = document.createElement("option");
                    o.value = "c:" + n;
                    o.textContent = n;
                    g2.appendChild(o);
                });
                sel.appendChild(g2);
            }
            if ([...sel.options].some(o => o.value === cur)) sel.value = cur;
        }

        /** 按颜色组输出选中方块；组内优先项排最前，供后端决定"优先项" */
        function palPayload() {
            const out = [];
            PAL.groups.forEach(g => {
                const ids = g.blocks.filter(b => PAL.selected.has(b.id)).map(b => b.id);
                const prio = (CFG.prio || {})[g.hex];
                if (prio && ids.includes(prio)) {
                    ids.sort((a, b) => (a === prio ? -1 : b === prio ? 1 : 0));
                }
                out.push(...ids);
            });
            return out;
        }

        function palOpenPanel() {
            $("pal-overlay").hidden = false;
            $("pal-search").focus();
        }

        function palClosePanel() {
            $("pal-overlay").hidden = true;
        }

        $("pal-open").addEventListener("click", palOpenPanel);
        $("pal-close").addEventListener("click", palClosePanel);
        $("pal-done").addEventListener("click", palClosePanel);
        $("pal-overlay").addEventListener("click", (e) => {
            if (e.target === $("pal-overlay")) palClosePanel();
        });
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && !$("pal-overlay").hidden) palClosePanel();
        });

        $("pal-alloc").addEventListener("change", () => {
            CFG.alloc = $("pal-alloc").value;
            cfgSave(true);
            schedulePreview(200);
            setStatus("同色分配策略：" + $("pal-alloc").selectedOptions[0].textContent);
        });

        $("pal-preset").addEventListener("change", () => {
            const v = $("pal-preset").value;
            if (!v) return;
            let ids = null;
            if (v.startsWith("b:")) {
                ids = presetIds(BUILTIN_PRESETS[+v.slice(2)]);
            } else {
                const custom = loadPresets();
                ids = presetIds(custom[v.slice(2)]);
            }
            if (!ids || !ids.length) { setStatus("这个预设在当前方块表下没有可用方块"); return; }
            palApply(ids);
            setStatus("已套用预设：" + $("pal-preset").selectedOptions[0].textContent
                + "（" + ids.length + " 个方块）");
        });

        $("pal-preset-save").addEventListener("click", () => {
            if (!PAL.selected.size) { alert("当前没有选中任何方块"); return; }
            const name = (prompt("预设名称：", "我的配色") || "").trim();
            if (!name) return;
            const custom = loadPresets();
            custom[name] = { ids: palPayload() };
            savePresets(custom);
            refreshPresetSelect();
            $("pal-preset").value = "c:" + name;
            setStatus("已保存预设：" + name);
        });

        $("pal-preset-del").addEventListener("click", () => {
            const v = $("pal-preset").value;
            if (!v.startsWith("c:")) { alert("只能删除自己保存的预设"); return; }
            const name = v.slice(2);
            if (!confirm('删除预设「' + name + '」？')) return;
            const custom = loadPresets();
            delete custom[name];
            savePresets(custom);
            refreshPresetSelect();
            $("pal-preset").value = "";
            setStatus("已删除预设：" + name);
        });

        function palColorCount() {
            let n = 0;
            PAL.groups.forEach(g => {
                if (g.blocks.some(b => PAL.selected.has(b.id))) n++;
            });
            return n;
        }

        function syncGenerateBtn() {
            $("mp-generate").disabled =
                MP.busy || (PAL.loaded && PAL.selected.size === 0);
        }

        function palPushUndo() {
            PAL.undo.push([...PAL.selected]);
            if (PAL.undo.length > 30) PAL.undo.shift();
        }

        function palStat() {
            if (!PAL.loaded) return;
            const el = $("pal-count");
            const btn = $("pal-open-main");
            const prioN = Object.keys(CFG.prio || {}).length;
            const allocTxt = ($("pal-alloc") && $("pal-alloc").selectedOptions.length)
                ? $("pal-alloc").selectedOptions[0].textContent : "";
            if (PAL.selected.size === 0) {
                el.innerHTML = '<span class="warn">未选择任何方块 —— 至少勾一个才能生成</span>';
                btn.innerHTML = '<span class="warn">未选择任何方块</span>';
            } else {
                el.textContent = `已选 ${PAL.selected.size}/${PAL.valid.size} 个方块 · `
                    + `覆盖 ${palColorCount()} / ${PAL.groups.length} 种颜色`
                    + (prioN ? ` · ${prioN} 个优先项` : "");
                btn.textContent = `已选 ${PAL.selected.size}/${PAL.valid.size} 个方块`
                    + ` · ${palColorCount()} 种颜色`
                    + (allocTxt ? ` · ${allocTxt}` : "");
            }
            syncGenerateBtn();
        }

        function palGroupState(node, g) {
            const on = g.blocks.filter(b => PAL.selected.has(b.id)).length;
            node.classList.toggle("any", on > 0);
            node.classList.toggle("off", on === 0);
            node.querySelector(".pgroup-n").textContent = on + "/" + g.blocks.length;
        }

        function palBuildGroup(g) {
            const node = document.createElement("div");
            node.className = "pgroup";
            node.dataset.hex = g.hex;

            const head = document.createElement("div");
            head.className = "pgroup-head";

            const sw = document.createElement("span");
            sw.className = "pgroup-swatch";
            sw.style.background = g.hex;

            const hex = document.createElement("span");
            hex.className = "pgroup-hex";
            hex.textContent = g.hex;
            hex.title = `rgb(${g.rgb.join(", ")})`;

            const n = document.createElement("span");
            n.className = "pgroup-n";

            const arrow = document.createElement("span");
            arrow.className = "pgroup-arrow";
            arrow.textContent = "▶";

            head.append(sw, hex, n, arrow);
            head.addEventListener("click", () => node.classList.toggle("open"));
            node.appendChild(head);

            const body = document.createElement("div");
            body.className = "pgroup-body";

            const prio = (CFG.prio || {})[g.hex];
            const ordered = g.blocks.slice().sort((a, b) => {
                if (a.id === prio) return -1;
                if (b.id === prio) return 1;
                return 0;
            });

            ordered.forEach(b => {
                const lab = document.createElement("label");
                const on = PAL.selected.has(b.id);
                lab.className = "pblock" + (on ? " checked" : "")
                    + (b.id === prio ? " is-prio" : "");
                lab.dataset.id = b.id;
                lab.dataset.search = (b.label + " " + b.name_eng + " " + g.hex + " " + b.id)
                    .toLowerCase();
                lab.title = `${b.label} · ${g.hex} · ${b.id}`;

                const cb = document.createElement("input");
                cb.type = "checkbox";
                cb.checked = on;

                const box = document.createElement("span");
                box.className = "pblock-box";

                const icon = document.createElement("span");
                icon.className = "pal-icon";
                icon.style.backgroundPosition =
                    `calc(var(--icon-px) * ${-b.cx}) calc(var(--icon-px) * ${-b.cy})`;
                icon.style.display = CFG.icons ? "" : "none";

                const name = document.createElement("span");
                name.className = "pblock-name";
                name.textContent = b.label;

                const pin = document.createElement("button");
                pin.type = "button";
                pin.className = "pblock-pin";
                pin.textContent = b.id === prio ? "★" : "☆";
                pin.title = b.id === prio ? "取消优先" : "设为这个颜色的优先项";
                pin.addEventListener("click", (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    if (!CFG.prio) CFG.prio = {};
                    if (CFG.prio[g.hex] === b.id) delete CFG.prio[g.hex];
                    else CFG.prio[g.hex] = b.id;
                    cfgSave(true);
                    renderPalette();
                    applyPalFilter();
                    schedulePreview(250);
                    setStatus(CFG.prio[g.hex]
                        ? "已把「" + b.label + "」设为 " + g.hex + " 的优先项"
                        : "已取消 " + g.hex + " 的优先项");
                });

                lab.append(cb, box, icon, name, pin);
                cb.addEventListener("change", () => {
                    palPushUndo();
                    if (cb.checked) PAL.selected.add(b.id);
                    else PAL.selected.delete(b.id);
                    lab.classList.toggle("checked", cb.checked);
                    palGroupState(node, g);
                    palCommit();
                });
                body.appendChild(lab);
            });

            node.appendChild(body);
            palGroupState(node, g);
            return node;
        }

        function palCommit() {
            CFG.blocks = [...PAL.selected];
            cfgSave();
            palStat();
            schedulePreview(300);
        }

        function renderPalette() {
            const list = $("pal-list");
            list.textContent = "";
            const frag = document.createDocumentFragment();
            PAL.groups.forEach(g => frag.appendChild(palBuildGroup(g)));
            list.appendChild(frag);
            applyPalFilter();
        }

        function palApply(ids, record) {
            if (record !== false) palPushUndo();
            PAL.selected = new Set(ids);
            renderPalette();
            palCommit();
        }

        function applyPalFilter() {
            const q = $("pal-search").value.trim().toLowerCase();
            document.querySelectorAll("#pal-list .pgroup").forEach(node => {
                let hits = 0;
                node.querySelectorAll(".pblock").forEach(lab => {
                    const hit = !q || lab.dataset.search.includes(q);
                    lab.hidden = !hit;
                    if (hit) hits++;
                });
                node.hidden = hits === 0;
                if (q && hits > 0) node.classList.add("open");
            });
        }

        function applyDisplayOpts() {
            document.documentElement.style.setProperty("--icon-px", CFG.iconPx + "px");
            document.querySelectorAll(".pal-icon").forEach(el => {
                el.style.display = CFG.icons ? "" : "none";
            });
            document.querySelectorAll(".pgroup-hex").forEach(el => {
                el.style.display = CFG.hex ? "" : "none";
            });
        }

        $("pal-search").addEventListener("input", applyPalFilter);
        $("pal-all").addEventListener("click", () => palApply(PAL.valid));
        $("pal-none").addEventListener("click", () => palApply([]));
        $("pal-first").addEventListener("click", () => palApply(PAL.defaults));
        $("pal-undo").addEventListener("click", () => {
            if (!PAL.undo.length) { setStatus("没有可撤销的选择"); return; }
            const prev = PAL.undo.pop();
            PAL.selected = new Set(prev);
            renderPalette();
            palCommit();
            setStatus("已撤销到上一个方块选择");
        });

        async function loadPalette() {
            try {
                const res = await fetch("/api/palette");
                const data = await res.json();
                if (!data.ok) throw new Error(data.msg || "加载失败");

                PAL.groups = data.groups || [];
                PAL.defaults = data.defaults || [];
                PAL.valid = new Set();
                PAL.groups.forEach(g => g.blocks.forEach(b => {
                    PAL.valid.add(b.id);
                    BLOCK_LOOKUP[b.id] = b;
                }));

                const ic = data.icon || {};
                if (ic.cols) document.documentElement.style.setProperty("--icon-cols", ic.cols);
                if (ic.rows) document.documentElement.style.setProperty("--icon-rows", ic.rows);
                // 带版本号的贴图集地址：重新生成贴图集后地址会变，避免浏览器拿旧图配新坐标
                if (ic.url) {
                    document.documentElement.style.setProperty(
                        "--icon-url", 'url("' + ic.url + '")');
                }
                checkIconSheet(ic);

                // 恢复上次选择；里面已失效的方块会被剔掉
                const saved = Array.isArray(CFG.blocks)
                    ? CFG.blocks.filter(id => PAL.valid.has(id)) : null;
                PAL.selected = new Set(saved !== null ? saved : PAL.defaults);

                PAL.loaded = true;
                renderPalette();
                applyDisplayOpts();
                palStat();
                schedulePreview(0);
            } catch (e) {
                $("pal-count").innerHTML = '<span class="warn">方块表加载失败</span>';
                $("pal-open-main").innerHTML = '<span class="warn">方块表加载失败</span>';
                $("pal-list").innerHTML =
                    '<div class="pal-empty">方块表加载失败：' + e.message + '</div>';
                syncGenerateBtn();
            }
        }

        /**
         * 自检：实际加载到的贴图集尺寸必须等于 列数×格子 与 行数×格子。
         * 对不上说明浏览器缓存了旧图（图标会整体错位），给出明确提示。
         */
        function checkIconSheet(ic) {
            if (!ic.cols || !ic.rows || !ic.size) return;
            const probe = new Image();
            probe.onload = () => {
                const wantW = ic.cols * ic.size, wantH = ic.rows * ic.size;
                if (probe.naturalWidth === wantW && probe.naturalHeight === wantH) return;
                const msg = `图标贴图集尺寸不符：实际 ${probe.naturalWidth}×${probe.naturalHeight}，`
                    + `应为 ${wantW}×${wantH} —— 多半是浏览器缓存了旧图，请按 Ctrl+F5 强制刷新`;
                console.warn(msg);
                setStatus(msg);
                setBadge("图标需强刷", "err");
            };
            probe.src = ic.url || "/api/icons.png";
        }
