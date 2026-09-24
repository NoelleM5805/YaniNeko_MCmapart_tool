// 70-mapart
// 地图画生成：上传 / 实时预览 / 生成 / 保存

        // ============================================================
        // 地图画生成
        // ============================================================
        const MP = {
            file: null, sid: null, imageInfo: null,
            taskId: null, resultBlob: null, busy: false,
            resultName: "mapart.litematic",
            previewSeq: 0, previewTimer: null,
            lastPreviewSrc: "", lastOriginalSrc: "",
            finalUrl: "",
        };

        /** 由输入的图片名推出默认输出名：photo.png -> photo.litematic */
        function defaultOutName(fileName) {
            if (!fileName) return "mapart.litematic";
            let base = String(fileName).split(/[\\/]/).pop();
            base = base.replace(/\.litematic$/i, "");
            if (!/\.litematic$/i.test(base)) base = base.replace(/\.[^.]+$/, "");
            base = base.replace(/[\\/:*?"<>|\x00-\x1f]/g, "_").trim().replace(/^\.+|\s+$/g, "");
            if (!base) return "mapart.litematic";
            if (base.length > 80) base = base.slice(0, 80);
            return base + ".litematic";
        }

        bindDropZone("mp-drop", "mp-file", async (f) => {
            if (!f.type.startsWith("image/")) {
                alert("请选择图片文件");
                return;
            }
            MP.file = f;
            showFileInfo("mp", f);
            setStatus("正在上传图片…");
            setBadge("上传中", "running");

            const fd = new FormData();
            fd.append("file", f);
            try {
                const res = await fetch("/api/mapart/upload", { method: "POST", body: fd });
                const data = await res.json();
                if (!data.ok) throw new Error(data.msg || "上传失败");
                MP.sid = data.sid;
                MP.imageInfo = data;
                renderRatios(data.recommendations);
                renderOriginal(data.original);
                MP.lastOriginalSrc = data.original;
                setStatus(`已上传：${data.width}×${data.height}`);
                setBadge("图片就绪", "ok");
                schedulePreview(80);
            } catch (e) {
                setStatus("上传失败：" + e.message);
                setBadge("失败", "err");
            }
        }, null);

        $("mp-file-clear").addEventListener("click", (e) => {
            e.stopPropagation();
            MP.file = null; MP.sid = null; MP.imageInfo = null;
            MP.lastPreviewSrc = ""; MP.lastOriginalSrc = "";
            clearFileInfo("mp");
            $("mp-ratios").innerHTML = '<div class="empty">上传图片后自动分析并给出推荐</div>';
            $("mp-preview-orig").innerHTML = '<div class="placeholder">原图缩略图</div>';
            $("mp-preview-pal").innerHTML = '<div class="placeholder">调色板预览</div>' +
                '<span class="updating">更新中…</span>' +
                '<span class="zoom-hint">点击放大</span>';
            setStatus("就绪");
            setBadge("—");
        });

        function renderRatios(recs) {
            const el = $("mp-ratios");
            el.innerHTML = "";
            if (!recs || !recs.length) {
                el.innerHTML = '<div class="empty">无法分析比例</div>';
                return;
            }
            recs.forEach((r) => {
                const err = (Math.exp(r.diff) - 1) * 100;
                const b = document.createElement("button");
                b.className = "ratio-btn";
                b.innerHTML = `<div class="ratio-title">${r.x}×${r.y}  (${r.px_w}×${r.px_h})</div>
                   <div class="ratio-sub">偏差 ≈ ${err.toFixed(1)}%</div>`;
                b.addEventListener("click", () => {
                    $("mp-size-mode").value = "grid";
                    onSizeModeChange();
                    $("mp-grid-x").value = r.x;
                    $("mp-grid-y").value = r.y;
                    document.querySelectorAll("#mp-ratios .ratio-btn").forEach(el =>
                        el.classList.toggle("selected", el === b));
                    schedulePreview(0);
                });
                el.appendChild(b);
            });
        }

        function renderOriginal(dataUrl) {
            const box = $("mp-preview-orig");
            box.innerHTML =
                `<img class="zoomable" src="${dataUrl}" alt="原图" title="点击放大">
     <div class="caption">原图</div>
     <span class="zoom-hint">点击放大</span>`;
            box.querySelector("img").addEventListener("click", () => openModal(dataUrl));
        }

        function schedulePreview(delay = 350) {
            if (!MP.sid) return;
            if (PAL.loaded && PAL.selected.size === 0) {
                setStatus("未选择任何方块，请先在「方块选择」里勾选");
                return;
            }
            if (MP.previewTimer) clearTimeout(MP.previewTimer);
            MP.previewTimer = setTimeout(previewMap, delay);
        }

        async function previewMap() {
            MP.previewTimer = null;
            if (!MP.sid) return;

            const seq = ++MP.previewSeq;
            const box = $("mp-preview-pal");
            box.classList.add("updating");

            const payload = {
                sid: MP.sid,
                size_mode: $("mp-size-mode").value,
                grid_x: parseInt($("mp-grid-x").value, 10) || 1,
                grid_y: parseInt($("mp-grid-y").value, 10) || 1,
                max_size: parseInt($("mp-max-size").value, 10) || 128,
                fit_mode: $("mp-fit").value,
                algo: $("mp-algo").value,
                dither: $("mp-dither").value,
                strength: parseInt($("mp-strength").value, 10) || 0,
                preview_side: CFG.previewSide,
                ...(PAL.loaded ? { blocks: palPayload() } : {}),
                alloc: CFG.alloc || "random",
                ...(adjActive() ? { adjust: ADJ } : {}),
            };

            try {
                const res = await fetch("/api/mapart/preview", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                });
                const data = await res.json();

                if (seq !== MP.previewSeq) return;
                if (!data.ok) throw new Error(data.msg || "预览失败");

                MP.lastPreviewSrc = data.preview;
                const used = (data.colors_used || []).length;
                box.innerHTML =
                    `<img class="zoomable" src="${data.preview}" alt="预览" title="点击放大">
       <div class="pal-meta">${data.width} × ${data.height} 方块 · 预览 ${data.preview_width}×${data.preview_height}</div>
       <div class="pal-meta">实际用色 ${used} / 可用 ${data.groups} 种 · 已选 ${PAL.selected.size} 个方块</div>
       <span class="updating">更新中…</span>
       <span class="zoom-hint">点击放大</span>`;
                box.classList.remove("updating");

                box.querySelector("img").addEventListener("click", () => {
                    if (focusOn()) return;          // 专注模式下点图是拖动，不弹放大框
                    if (MP.lastPreviewSrc) openModal(MP.lastPreviewSrc);
                });

                // 预览图被重绘了，把当前的缩放/位移重新贴上去，别让用户的视角丢失
                if (focusOn()) {
                    focusAttach();
                    setTimeout(() => {
                        if (!focusOn()) return;
                        if (!FOCUS.fitted) { focusFit(); FOCUS.fitted = true; }
                        else focusApply();
                    }, 20);
                }

                renderUsage(data.counts, data.total_blocks,
                    data.estimated ? "预览估算" : "预览");
                setStatus(`预览：${data.width}×${data.height}，共 ${data.blocks} 方块，用色 ${used} 种`);
                setBadge(`${data.blocks} 方块 · 预览`, "ok");
            } catch (e) {
                if (seq !== MP.previewSeq) return;
                box.classList.remove("updating");
                setStatus("预览失败：" + e.message);
            }
        }

        ["mp-size-mode", "mp-grid-x", "mp-grid-y", "mp-max-size", "mp-fit",
            "mp-algo", "mp-dither", "mp-strength"].forEach(id => {
                const el = $(id);
                el.addEventListener("input", () => schedulePreview(350));
                el.addEventListener("change", () => schedulePreview(150));
            });

        function onSizeModeChange() {
            const mode = $("mp-size-mode").value;
            $("mp-grid-fields").style.display = mode === "grid" ? "" : "none";
            $("mp-max-fields").style.display = mode === "maxsize" ? "" : "none";
        }
        $("mp-size-mode").addEventListener("change", () => {
            onSizeModeChange();
            schedulePreview(150);
        });

        $("mp-strength").addEventListener("input", () => {
            $("mp-strength-val").textContent = $("mp-strength").value;
        });

        $("mp-generate").addEventListener("click", async () => {
            if (!MP.sid) { alert("请先上传图片"); return; }
            if (PAL.loaded && PAL.selected.size === 0) {
                alert("请先在「方块选择」里至少勾选一个方块");
                return;
            }
            MP.busy = true;
            syncGenerateBtn();
            $("mp-save-sec").hidden = true;
            $("mp-final-sec").hidden = true;
            MP.resultBlob = null;
            clearFinalImage();
            const logEl = $("mp-log");
            logEl.textContent = "正在启动生成任务…";
            setStatus("生成中…");
            setBadge("处理中", "running");

            const outName = defaultOutName(MP.file && MP.file.name);

            const payload = {
                sid: MP.sid,
                size_mode: $("mp-size-mode").value,
                grid_x: parseInt($("mp-grid-x").value, 10) || 1,
                grid_y: parseInt($("mp-grid-y").value, 10) || 1,
                max_size: parseInt($("mp-max-size").value, 10) || 128,
                fit_mode: $("mp-fit").value,
                algo: $("mp-algo").value,
                dither: $("mp-dither").value,
                strength: parseInt($("mp-strength").value, 10) || 0,
                filename: outName,
                ...(PAL.loaded ? { blocks: palPayload() } : {}),
                alloc: CFG.alloc || "random",
                ...(adjActive() ? { adjust: ADJ } : {}),
            };

            try {
                const res = await fetch("/api/mapart/generate", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                });
                const data = await res.json();
                if (!data.ok) throw new Error(data.msg || "启动失败");
                MP.taskId = data.task_id;
                pollTask(data.task_id, logEl, async (st) => {
                    MP.busy = false;
                    syncGenerateBtn();
                    if (st.error) {
                        setStatus("生成失败");
                        setBadge("失败", "err");
                        return;
                    }
                    const r = st.result || {};
                    setStatus(`完成：${r.width}×${r.height}，${r.placed} 方块`);
                    setBadge(`${r.placed} 方块 · 完成`, "ok");
                    renderUsage(r.counts, r.total_blocks || r.placed, "已生成");
                    try {
                        const dres = await fetch(`/api/download/${MP.taskId}`);
                        if (dres.ok) {
                            MP.resultBlob = await dres.blob();
                            // 文件名默认跟输入图片一致（后端已按同一规则清洗过）
                            MP.resultName = r.filename || outName;
                            $("mp-save-name").textContent = "文件名：" + MP.resultName;
                            $("mp-save-sec").hidden = false;
                        }
                    } catch (e) { console.error(e); }
                    if (r.has_image) await showFinalImage(MP.taskId, r);
                });
            } catch (e) {
                MP.busy = false;
                syncGenerateBtn();
                setStatus("失败：" + e.message);
                setBadge("失败", "err");
            }
        });

        $("mp-save").addEventListener("click", async () => {
            if (!MP.resultBlob) return;
            const name = await saveBlob(MP.resultBlob, MP.resultName || "mapart.litematic");
            if (name) {
                setStatus("已保存：" + name);
                setBadge("已保存", "ok");
            }
        });

        // ---- 导出前的全尺寸最终效果预览 ----
        function clearFinalImage() {
            if (MP.finalUrl) {
                URL.revokeObjectURL(MP.finalUrl);
                MP.finalUrl = "";
            }
            $("mp-final-sec").hidden = true;
            $("mp-final-box").innerHTML =
                '<div class="placeholder">生成后在这里显示</div>' +
                '<span class="zoom-hint">点击放大</span>';
        }

        async function showFinalImage(taskId, r) {
            try {
                const res = await fetch(`/api/result-image/${taskId}`, { cache: "no-store" });
                if (!res.ok) throw new Error("HTTP " + res.status);
                const blob = await res.blob();
                if (MP.finalUrl) URL.revokeObjectURL(MP.finalUrl);
                MP.finalUrl = URL.createObjectURL(blob);

                const box = $("mp-final-box");
                box.innerHTML =
                    `<img class="zoomable" src="${MP.finalUrl}" alt="最终效果"
                          title="点击放大">
                     <div class="pal-meta">${r.width} × ${r.height} 方块 · 1:1 全尺寸</div>
                     <span class="zoom-hint">点击放大</span>`;
                box.querySelector("img").addEventListener("click", () => {
                    if (MP.finalUrl) openModal(MP.finalUrl);
                });
                $("mp-final-sec").hidden = false;
                $("mp-final-hint").textContent =
                    `按真实方块数量 1:1 渲染（${r.width}×${r.height}），`
                    + `用到 ${(r.counts || []).length} 种方块。点击图片放大后可逐个确认方块分布。`;
            } catch (e) {
                console.warn("最终效果预览加载失败：", e);
            }
        }
