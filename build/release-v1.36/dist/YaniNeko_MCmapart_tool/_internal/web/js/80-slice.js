// 80-slice
// 投影切分

        // ============================================================
        // 投影切分
        // ============================================================
        const SL = { file: null, taskId: null, files: [], base: "slice" };

        bindDropZone("sl-drop", "sl-file", (f) => {
            SL.file = f;
            showFileInfo("sl", f);
            setStatus(`已选择：${f.name}`);
        }, ".litematic");

        $("sl-file-clear").addEventListener("click", (e) => {
            e.stopPropagation();
            SL.file = null;
            clearFileInfo("sl");
            setStatus("就绪");
        });

        $("sl-run").addEventListener("click", async () => {
            if (!SL.file) { alert("请先上传 .litematic 文件"); return; }
            const btn = $("sl-run");
            btn.disabled = true;
            $("sl-save-sec").hidden = true;
            SL.resultBlob = null;
            const logEl = $("sl-log");
            logEl.textContent = "正在上传…";
            setStatus("切分中…");
            setBadge("处理中", "running");

            const fd = new FormData();
            fd.append("file", SL.file);
            fd.append("cols", $("sl-cols").value);
            fd.append("rows", $("sl-rows").value);
            fd.append("filename", SL.file.name || "");

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

        /**
         * 保存切分结果：优先让用户选一个文件夹一次性写入，
         * 不支持目录选择器的浏览器就退化成逐个下载。
         */
        $("sl-save").addEventListener("click", async () => {
            if (!SL.files || !SL.files.length) return;
            const btn = $("sl-save");
            btn.disabled = true;
            setStatus("正在保存 " + SL.files.length + " 个文件…");
            try {
                const blobs = [];
                for (let i = 0; i < SL.files.length; i++) {
                    const res = await fetch(`/api/slice/file/${SL.taskId}/${i}`);
                    if (!res.ok) throw new Error("第 " + (i + 1) + " 个文件下载失败");
                    blobs.push(await res.blob());
                }

                if (window.showDirectoryPicker) {
                    try {
                        const dir = await window.showDirectoryPicker({ mode: "readwrite" });
                        for (let i = 0; i < blobs.length; i++) {
                            const fh = await dir.getFileHandle(SL.files[i].name, { create: true });
                            const w = await fh.createWritable();
                            await w.write(blobs[i]);
                            await w.close();
                        }
                        setStatus(`已保存 ${blobs.length} 个文件到所选文件夹`);
                        setBadge(`${blobs.length} 个文件 · 已保存`, "ok");
                        return;
                    } catch (e) {
                        if (e && e.name === "AbortError") { setStatus("已取消保存"); return; }
                        // 目录选择器不可用 -> 退化成逐个下载
                    }
                }

                for (let i = 0; i < blobs.length; i++) {
                    const url = URL.createObjectURL(blobs[i]);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = SL.files[i].name;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    setTimeout(() => URL.revokeObjectURL(url), 3000);
                    await new Promise(r => setTimeout(r, 220));
                }
                setStatus(`已触发 ${blobs.length} 个文件下载（浏览器可能会问一次是否允许多文件下载）`);
                setBadge(`${blobs.length} 个文件 · 已保存`, "ok");
            } catch (e) {
                setStatus("保存失败：" + e.message);
                setBadge("失败", "err");
            } finally {
                btn.disabled = false;
            }
        });
