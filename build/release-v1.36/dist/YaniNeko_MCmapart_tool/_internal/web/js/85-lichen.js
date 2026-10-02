// 85-lichen
// Glow Lichen 面属性批量修改

        // ============================================================
        // Glow Lichen
        // ============================================================
        const LC = { file: null, taskId: null, resultBlob: null, resultName: "modified.litematic" };
        const FACE_LABELS = {
            down: "下 (down)", up: "上 (up)",
            north: "北 (north)", south: "南 (south)",
            east: "东 (east)", west: "西 (west)"
        };
        const FACE_KEYS = ["down", "up", "north", "south", "east", "west"];

        FACE_KEYS.forEach(k => {
            const div = document.createElement("label");
            div.className = "face-item" + (k === "down" ? " checked" : "");
            div.dataset.face = k;
            div.innerHTML = `<input type="checkbox" ${k === "down" ? "checked" : ""}>
                   <span class="box"></span>${FACE_LABELS[k]}`;
            const cb = div.querySelector("input");
            cb.addEventListener("change", () => div.classList.toggle("checked", cb.checked));
            $("lc-faces").appendChild(div);
        });

        $("lc-faces").parentElement.querySelectorAll(".quick-row [data-only]").forEach(b => {
            b.addEventListener("click", () => {
                const face = b.dataset.only;
                document.querySelectorAll("#lc-faces .face-item").forEach(el => {
                    const on = el.dataset.face === face;
                    el.querySelector("input").checked = on;
                    el.classList.toggle("checked", on);
                });
            });
        });
        $("lc-faces").parentElement.querySelectorAll(".quick-row [data-all]").forEach(b => {
            b.addEventListener("click", () => {
                const on = b.dataset.all === "1";
                document.querySelectorAll("#lc-faces .face-item").forEach(el => {
                    el.querySelector("input").checked = on;
                    el.classList.toggle("checked", on);
                });
            });
        });

        bindDropZone("lc-drop", "lc-file", (f) => {
            LC.file = f;
            showFileInfo("lc", f);
            setStatus(`已选择：${f.name}`);
        }, ".litematic");

        $("lc-file-clear").addEventListener("click", (e) => {
            e.stopPropagation();
            LC.file = null;
            clearFileInfo("lc");
            setStatus("就绪");
        });

        $("lc-run").addEventListener("click", async () => {
            if (!LC.file) { alert("请先上传 .litematic 文件"); return; }
            const btn = $("lc-run");
            btn.disabled = true;
            $("lc-save-sec").hidden = true;
            LC.resultBlob = null;
            const logEl = $("lc-log");
            logEl.textContent = "正在上传…";
            setStatus("处理中…");
            setBadge("处理中", "running");

            const faces = {};
            document.querySelectorAll("#lc-faces .face-item").forEach(el => {
                faces[el.dataset.face] = el.querySelector("input").checked;
            });

            const fd = new FormData();
            fd.append("file", LC.file);
            fd.append("faces", JSON.stringify(faces));
            fd.append("waterlogged", "false");

            try {
                const res = await fetch("/api/lichen/process", { method: "POST", body: fd });
                const data = await res.json();
                if (!data.ok) throw new Error(data.msg || "启动失败");
                LC.taskId = data.task_id;
                pollTask(data.task_id, logEl, async (st) => {
                    btn.disabled = false;
                    if (st.error) {
                        setStatus("处理失败");
                        setBadge("失败", "err");
                        return;
                    }
                    const r = st.result || {};
                    setStatus(`完成：替换 ${r.replaced} 个`);
                    setBadge(`${r.replaced} 替换 · 完成`, "ok");
                    try {
                        const dres = await fetch(`/api/download/${LC.taskId}`);
                        if (dres.ok) {
                            LC.resultBlob = await dres.blob();
                            $("lc-save-sec").hidden = false;
                        }
                    } catch (e) { console.error(e); }
                });
            } catch (e) {
                btn.disabled = false;
                setStatus("失败：" + e.message);
                setBadge("失败", "err");
            }
        });

        $("lc-save").addEventListener("click", async () => {
            if (!LC.resultBlob) return;
            const name = await saveBlob(LC.resultBlob, LC.resultName);
            if (name) {
                setStatus("已保存：" + name);
                setBadge("已保存", "ok");
            }
        });
