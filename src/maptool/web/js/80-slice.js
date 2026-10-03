// 80-slice
// 投影切分：按 128×128 自动推荐切割方式 + 切割效果预览 + 并行保存

        // ============================================================
        // 投影切分
        // ============================================================
        // SL.plan  —— 当前生效的切割方案（行/列/每块边界），预览和实际切分共用
        // SL.key   —— 服务器缓存的上传内容指纹，切分时回传，不用再上传一遍
        const SL = {
            file: null, key: null, base: "slice",
            plan: null, cells: [], stats: [],
            taskId: null, files: [], loading: false, reqSeq: 0,
        };

        const SL_PREVIEW_MAX = 460;   // 预览画布的最大显示边长（CSS 像素）

        // ---------- 参数 ----------
        function slMode() {
            const el = document.querySelector('input[name="sl-mode"]:checked');
            return el ? el.value : "auto";
        }

        function slMaxSize() {
            const v = parseInt($("sl-max-side").value, 10);
            if (!v || v < 16) return 128;
            return Math.min(v, 1024);
        }

        function slManualGrid() {
            const c = parseInt($("sl-cols").value, 10) || 1;
            const r = parseInt($("sl-rows").value, 10) || 1;
            return [Math.max(1, Math.min(256, c)), Math.max(1, Math.min(256, r))];
        }

        /**
         * 手动网格的建议下限。
         *
         * 只是**提示**，不替用户改刀数 —— 用户常按自己地图的编号切，
         * 写了 2×3 就该是 2×3。服务端也这么认，所以这里只把下限写进
         * input 的 min 属性，并在超标时给一句话提醒。
         */
        function slSuggestMin() {
            if (!SL.plan) return null;
            const m = slMaxSize();
            const minC = Math.max(1, Math.ceil(SL.plan.sx / m));
            const minR = Math.max(1, Math.ceil(SL.plan.sz / m));
            $("sl-cols").min = minC;
            $("sl-rows").min = minR;
            return [minC, minR];
        }

        /** 手动切得比「每块最大边长」还大时提醒一句（但不改用户的刀数）。 */
        function slWarnOver(plan) {
            const box = $("sl-plan-warn");
            if (!box) return;
            const over = (plan && plan.over) || [];
            if (!over.length) { box.hidden = true; return; }
            const min = slSuggestMin();
            box.hidden = false;
            box.textContent =
                `⚠ 有块超过每块最大边长：${over.map(o => o[0] + " 方向 " + o[1] + " 格").join("、")}`
                + `。想要每块不超过，至少 ${min[0]} 列 × ${min[1]} 行。`;
        }

        /** 交给服务端的切分参数：手动模式才带 cols/rows */
        function slParams() {
            const p = { max_size: slMaxSize() };
            if (slMode() === "manual") {
                const [c, r] = slManualGrid();
                p.cols = c; p.rows = r;
            }
            return p;
        }

        // ---------- 上传 ----------
        bindDropZone("sl-drop", "sl-file", (f) => {
            SL.file = f;
            SL.key = null;
            SL.taskId = null;
            SL.files = [];
            showFileInfo("sl", f);
            $("sl-save-sec").hidden = true;
            slRefreshPreview();
        }, ".litematic");

        $("sl-file-clear").addEventListener("click", (e) => {
            e.stopPropagation();
            SL.file = null;
            SL.key = null;
            SL.plan = null;
            SL.stats = [];
            clearFileInfo("sl");
            $("sl-size").hidden = true;
            $("sl-plan-sec").hidden = true;
            $("sl-run-sec").hidden = true;
            $("sl-save-sec").hidden = true;
            $("sl-prev-empty").hidden = false;
            $("sl-stage").hidden = true;
            $("sl-summary").hidden = true;
            $("sl-stats-sec").hidden = true;
            $("sl-reco").hidden = true;
            setStatus("就绪");
        });

        // ---------- 预览 ----------
        const slRecoApply = $("sl-reco-apply");
        if (slRecoApply) {
            slRecoApply.addEventListener("click", () => {
                // 推荐值塞进输入框并切到手动模式，用户还能继续微调
                const rec = SL.recoGrid;
                if (rec) {
                    $("sl-cols").value = rec[0];
                    $("sl-rows").value = rec[1];
                    slSetMode("manual");
                    slRefreshPreview();
                }
            });
        }

        function slSetMode(mode) {
            document.querySelectorAll('input[name="sl-mode"]').forEach(el => {
                el.checked = (el.value === mode);
            });
            $("sl-manual").hidden = (mode !== "manual");
        }

        document.querySelectorAll('input[name="sl-mode"]').forEach(el => {
            el.addEventListener("change", () => {
                $("sl-manual").hidden = slMode() !== "manual";
                slRefreshPreview();
            });
        });

        ["sl-cols", "sl-rows", "sl-max-side"].forEach(id => {
            const el = $(id);
            if (!el) return;
            el.addEventListener("change", () => {
                if (id === "sl-max-side") {
                    // 改上限时如果本来在手动模式，列行数会显得对不上，直接回自动
                    slSetMode(slMode() === "manual" ? "manual" : "auto");
                }
                slRefreshPreview();
            });
        });

        let slTimer = null;
        function slRefreshPreview(delay) {
            if (!SL.file) return;
            if (slTimer) clearTimeout(slTimer);
            slTimer = setTimeout(slLoadPreview, delay === undefined ? 180 : delay);
        }

        async function slLoadPreview() {
            if (!SL.file || SL.loading) return;
            const seq = ++SL.reqSeq;
            SL.loading = true;
            $("sl-log").textContent = "正在生成切割预览…";
            setStatus("生成预览中…");

            const fd = new FormData();
            fd.append("file", SL.file);
            fd.append("filename", SL.file.name || "");
            const p = slParams();
            Object.keys(p).forEach(k => fd.append(k, p[k]));

            try {
                const res = await fetch("/api/slice/preview", { method: "POST", body: fd });
                const data = await res.json();
                if (seq !== SL.reqSeq) return;      // 有更新的请求了，丢弃旧结果
                if (!data.ok) throw new Error(data.msg || "预览失败");
                slApplyPreview(data);
            } catch (e) {
                if (seq !== SL.reqSeq) return;
                setStatus("预览失败：" + e.message);
                $("sl-log").textContent = "预览失败：" + e.message;
            } finally {
                if (seq === SL.reqSeq) SL.loading = false;
            }
        }

        function slApplyPreview(data) {
            SL.key = data.key;
            SL.base = data.base || "slice";
            SL.plan = data.plan;
            SL.stats = data.cell_stats || [];
            SL.recoGrid = [data.recommend.cols, data.recommend.rows];

            // 手动模式下的输入框跟随服务端实际采用的列/行数
            if (slMode() === "manual") {
                $("sl-cols").value = data.plan.cols;
                $("sl-rows").value = data.plan.rows;
            }
            slSuggestMin();
            slWarnOver(data.plan);

            $("sl-size").hidden = false;
            $("sl-size").textContent =
                `投影内容：${data.sx} × ${data.sz} 格（X × Z）`;
            $("sl-plan-sec").hidden = false;
            $("sl-run-sec").hidden = false;
            $("sl-stats-sec").hidden = false;

            // 推荐条
            $("sl-reco").hidden = false;
            $("sl-reco-txt").textContent = data.text;
            $("sl-reco-apply").textContent =
                (data.recommend.cols === data.plan.cols &&
                 data.recommend.rows === data.plan.rows) ? "已是推荐" : "套用推荐";

            // 手动模式下的输入框跟着服务端实际用的边界回填
            if (slMode() === "manual") {
                $("sl-cols").value = data.plan.cols;
                $("sl-rows").value = data.plan.rows;
            }
            slSuggestMin();
            slWarnOver(data.plan);

            // 缩略图
            const empty = $("sl-prev-empty"), stage = $("sl-stage");
            const img = $("sl-thumb");
            // 先让容器可见，再挂 onload：图在 display:none 的父节点里会立刻
            // 完成加载，之后量到的尺寸是 0，网格就画不出来且不会再触发一次。
            empty.hidden = !!data.preview_url;
            stage.hidden = !data.preview_url;
            if (data.preview_url) {
                img.onload = () => requestAnimationFrame(slDrawGrid);
                img.onerror = () => { setStatus("缩略图加载失败"); };
                const url = data.preview_url + "?t=" + Date.now();
                if (img.getAttribute("src") !== url) img.src = url;
                // onload 可能已经错过了（同一个 key 再预览一次），补画一次
                requestAnimationFrame(slDrawGrid);
            }

            // 概览
            const sum = $("sl-summary");
            sum.hidden = false;
            const tw = Math.max(...data.plan.cells.map(c => c.w));
            const th = Math.max(...data.plan.cells.map(c => c.h));
            sum.innerHTML =
                `<span class="sl-chip">共 <b>${data.plan.count}</b> 块</span>`
                + `<span class="sl-chip">有内容 <b>${data.filled}</b> 块</span>`
                + `<span class="sl-chip">最大一块 <b>${tw}×${th}</b></span>`
                + `<span class="sl-chip">单元格 ${data.plan.cols} 列 × ${data.plan.rows} 行</span>`
                + `<span class="sl-chip">预览 <b>${data.preview_w}×${data.preview_h}</b> 像素（1 方块 = 1 像素）</span>`;

            slRenderStats();
            setStatus(`预览就绪：将切成 ${data.plan.count} 块`);
            $("sl-log").textContent =
                `预览完成：${data.plan.cols} 列 × ${data.plan.rows} 行 = ${data.plan.count} 块，`
                + `最大 ${tw}×${th}，有内容 ${data.filled} 块`;

            $("sl-save-sec").hidden = true;
            SL.files = [];
        }

        function slRenderStats() {
            const wrap = $("sl-cells");
            wrap.innerHTML = "";
            SL.stats.forEach((s, i) => {
                const el = document.createElement("div");
                el.className = "sl-cell" + (s.blocks === 0 ? " is-empty" : "");
                el.dataset.index = i;
                el.innerHTML =
                    `<span class="sl-cell-rc">r${s.row}c${s.col}</span>`
                    + `<span class="sl-cell-wh">${s.w}×${s.h}</span>`
                    + `<span class="sl-cell-n">${s.blocks.toLocaleString()} 方块</span>`;
                el.addEventListener("mouseenter", () => slHighlight(i));
                el.addEventListener("mouseleave", () => slHighlight(-1));
                wrap.appendChild(el);
            });
        }

        let slHover = -1;
        function slHighlight(i) {
            slHover = i;
            document.querySelectorAll(".sl-cell").forEach(el => {
                el.classList.toggle("is-hover", Number(el.dataset.index) === i);
            });
            slDrawGrid();
        }

        /**
         * 把切割线画在预览图上。
         *
         * 预览图是**全分辨率**的：一个方块 = 一个像素（服务端逐像素渲染，
         * 不做下采样）。所以这里 kx/kz 是把「方块坐标」映射到「画布设备像素」
         * 的缩放比，画布尺寸始终跟图片的显示尺寸 1:1，格线才不会偏。
         */
        function slDrawGrid() {
            const img = $("sl-thumb"), cv = $("sl-grid");
            const stage = $("sl-stage");
            if (!SL.plan || !img.naturalWidth || stage.hidden) return;

            const sw = img.clientWidth, sh = img.clientHeight;
            // 容器被隐藏（切到别的页签）时量到 0，直接跳过，等 resize/再预览补画
            if (!sw || !sh) return;

            const dpr = Math.min(2, window.devicePixelRatio || 1);
            const W = Math.round(sw * dpr), H = Math.round(sh * dpr);
            if (cv.width !== W || cv.height !== H) {
                cv.width = W;
                cv.height = H;
            }
            cv.style.width = sw + "px";
            cv.style.height = sh + "px";
            const ctx = cv.getContext("2d");
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            ctx.clearRect(0, 0, sw, sh);

            const plan = SL.plan;
            const kx = sw / plan.sx, kz = sh / plan.sz;
            // 吸附到画布内的像素中心：floor 保证最右/最下那条不会落到画布外
            const snapX = (v) => Math.min(Math.floor(v * kx), W - 1) + .5;
            const snapZ = (v) => Math.min(Math.floor(v * kz), H - 1) + .5;

            const px = plan.x_bounds.map((v, i) =>
                (i === plan.x_bounds.length - 1) ? W - .5 : snapX(v));
            const pz = plan.z_bounds.map((v, i) =>
                (i === plan.z_bounds.length - 1) ? H - .5 : snapZ(v));

            // 高亮的块
            if (slHover >= 0 && SL.stats[slHover]) {
                const s = SL.stats[slHover];
                ctx.fillStyle = "rgba(255, 199, 0, .26)";
                ctx.fillRect(px[plan.x_bounds.indexOf(s.x0)],
                             pz[plan.z_bounds.indexOf(s.z0)],
                             s.w * (W / plan.sx), s.h * (H / plan.sz));
            }

            // 切割线。
            // 1px 的线要落在像素中心（x.5）才清晰；坐标用 floor 而不是 +0.5，
            // 否则最右边那条会落到 W（画布最后一格是 W-1），被裁掉看不见。
            ctx.strokeStyle = "rgba(255, 60, 60, .95)";
            ctx.lineWidth = 1;
            ctx.beginPath();
            for (let i = 0; i < px.length; i++) {
                ctx.moveTo(px[i], 0); ctx.lineTo(px[i], H);
            }
            for (let i = 0; i < pz.length; i++) {
                ctx.moveTo(0, pz[i]); ctx.lineTo(W, pz[i]);
            }
            ctx.stroke();

            // 块编号 + 尺寸（格子够大才画，不然糊成一团）。
            // 字号按 dpr 放大 —— setTransform 已经把绘制缩放了，这里要的是
            // 「设备像素」的字号，10 * dpr 才等于画面上 10 CSS px。
            const cw = (px[1] - px[0]);
            const ch = (pz[1] - pz[0]);
            if (cw >= 52 && ch >= 36) {
                const fam = getComputedStyle(document.body).fontFamily;
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                SL.stats.forEach((s, i) => {
                    const xi = plan.x_bounds.indexOf(s.x0);
                    const zi = plan.z_bounds.indexOf(s.z0);
                    const x0 = px[xi], z0 = pz[zi];
                    const cx = x0 + s.w * (W / plan.sx) / 2;
                    const cy = z0 + s.h * (H / plan.sz) / 2;
                    const hi = (i === slHover);
                    const label = `${s.row},${s.col}`;
                    ctx.font = "600 " + Math.round(10 * dpr) + "px " + fam;
                    ctx.lineWidth = 3 * dpr;
                    ctx.strokeStyle = "rgba(0,0,0,.75)";
                    ctx.fillStyle = hi ? "rgba(255,255,255,.95)" : "rgba(255,255,255,.80)";
                    ctx.strokeText(label, cx, cy);
                    ctx.fillText(label, cx, cy);
                    if (hi && cw >= 108 && ch >= 68) {
                        const sub = `${s.w}×${s.h}`;
                        ctx.font = "600 " + Math.round(9 * dpr) + "px " + fam;
                        ctx.strokeText(sub, cx, cy + 12 * dpr);
                        ctx.fillText(sub, cx, cy + 12 * dpr);
                    }
                });
            }
        }

        window.addEventListener("resize", () => { slDrawGrid(); });

        // ---------- 切分 ----------
        $("sl-run").addEventListener("click", async () => {
            if (!SL.file) { alert("请先上传 .litematic 文件"); return; }
            if (!SL.key) { alert("预览还没生成完，稍等一下"); return; }
            const btn = $("sl-run");
            btn.disabled = true;
            $("sl-save-sec").hidden = true;
            SL.files = [];
            const logEl = $("sl-log");
            logEl.textContent = "正在启动切分任务…";
            setStatus("切分中…");
            setBadge("处理中", "running");

            const fd = new FormData();
            fd.append("key", SL.key);              // 内容已在服务端缓存，不再上传
            fd.append("filename", SL.file.name || "");
            const p = slParams();
            Object.keys(p).forEach(k => fd.append(k, p[k]));

            try {
                const res = await fetch("/api/slice/process", { method: "POST", body: fd });
                const data = await res.json();
                if (!data.ok) throw new Error(data.msg || "启动失败");
                SL.taskId = data.task_id;
                pollTask(data.task_id, logEl, async (st) => {
                    btn.disabled = false;
                    if (st.error) {
                        setStatus("切分失败");
                        setBadge("失败", "err");
                        return;
                    }
                    const r = st.result || {};
                    SL.files = r.files || [];
                    SL.base = r.base || "slice";
                    const kb = ((r.size || 0) / 1024).toFixed(1);
                    setStatus(`切分完成：${r.count} 个投影文件，共 ${kb} KB`);
                    setBadge(`${r.count} 个文件 · 完成`, "ok");
                    $("sl-save-name").textContent =
                        `将保存 ${SL.files.length} 个文件：${SL.files.slice(0, 3).map(f => f.name).join("、")}`
                        + (SL.files.length > 3 ? " …" : "");
                    $("sl-save-sec").hidden = false;
                });
            } catch (e) {
                btn.disabled = false;
                setStatus("失败：" + e.message);
                setBadge("失败", "err");
            }
        });

        // ---------- 保存 ----------
        /**
         * 并发把切分结果拉下来。
         *
         * 以前是一个接一个 await 拉，64 个文件光往返就够慢的；改成固定并发
         * 同时拉几个，并且**边拉边写目录**（不再全部攒在内存里等最后一起写）。
         */
        async function slFetchAll(onProgress) {
            const total = SL.files.length;
            const blobs = new Array(total);
            let done = 0, next = 0, failed = 0;
            const CONC = 6;

            async function worker() {
                while (true) {
                    const i = next++;
                    if (i >= total) return;
                    try {
                        const res = await fetch(`/api/slice/file/${SL.taskId}/${i}`);
                        if (!res.ok) throw new Error("HTTP " + res.status);
                        blobs[i] = await res.blob();
                    } catch (e) {
                        failed++;
                        blobs[i] = null;
                    }
                    done++;
                    if (onProgress) onProgress(done, total);
                }
            }
            await Promise.all(Array.from({ length: Math.min(CONC, total) }, worker));
            if (failed) throw new Error(`${failed} 个文件下载失败`);
            return blobs;
        }

        $("sl-save").addEventListener("click", async () => {
            if (!SL.files || !SL.files.length) return;
            const btn = $("sl-save");
            btn.disabled = true;
            const total = SL.files.length;
            setStatus(`正在保存 ${total} 个文件…`);

            try {
                // 能选目录就边拉边写，内存里不留整份
                let dir = null;
                if (window.showDirectoryPicker) {
                    try {
                        dir = await window.showDirectoryPicker({ mode: "readwrite" });
                    } catch (e) {
                        if (e && e.name === "AbortError") { setStatus("已取消保存"); return; }
                        dir = null;   // 选择器不可用 -> 走下载
                    }
                }

                if (dir) {
                    const handles = [];
                    for (let i = 0; i < total; i++) {
                        handles.push(await dir.getFileHandle(SL.files[i].name, { create: true }));
                    }
                    const blobs = await slFetchAll((d) => {
                        if (d % 4 === 0 || d === total) {
                            setStatus(`正在保存 ${d}/${total} 个文件…`);
                        }
                    });
                    for (let i = 0; i < total; i++) {
                        const w = await handles[i].createWritable();
                        await w.write(blobs[i]);
                        await w.close();
                    }
                    setStatus(`已保存 ${total} 个文件到所选文件夹`);
                    setBadge(`${total} 个文件 · 已保存`, "ok");
                    return;
                }

                const blobs = await slFetchAll((d) => {
                    if (d % 4 === 0 || d === total) {
                        setStatus(`正在下载 ${d}/${total} 个文件…`);
                    }
                });
                for (let i = 0; i < total; i++) {
                    const url = URL.createObjectURL(blobs[i]);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = SL.files[i].name;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    setTimeout(() => URL.revokeObjectURL(url), 3000);
                    await new Promise(r => setTimeout(r, 120));
                }
                setStatus(`已触发 ${total} 个文件下载（浏览器可能会问一次是否允许多文件下载；`
                          + `被拦了就改用「打包成 zip 下载」）`);
                setBadge(`${total} 个文件 · 已保存`, "ok");
            } catch (e) {
                setStatus("保存失败：" + e.message);
                setBadge("失败", "err");
            } finally {
                btn.disabled = false;
            }
        });

        $("sl-zip").addEventListener("click", async () => {
            if (!SL.taskId) { alert("请先完成一次切分"); return; }
            setStatus("正在打包 zip…");
            const a = document.createElement("a");
            a.href = `/api/slice/zip/${SL.taskId}`;
            a.download = `${SL.base}_投影切分.zip`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            setStatus("已开始下载 zip");
        });

        // 页面初始化时把模式收起来
        slSetMode("auto");
