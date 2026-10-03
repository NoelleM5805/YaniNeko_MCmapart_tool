// 00-util
// 通用工具：$ / 徽标 / 拖放 / 保存 / 任务轮询

        // ============================================================
        // 通用工具
        // ============================================================
        function $(id) { return document.getElementById(id); }

        function formatSize(bytes) {
            if (bytes < 1024) return bytes + " B";
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
            return (bytes / 1024 / 1024).toFixed(2) + " MB";
        }

        function setBadge(text, cls) {
            const b = $("badge");
            b.textContent = text;
            b.className = "badge" + (cls ? " " + cls : "");
        }

        function setStatus(text) {
            $("status").textContent = text;
        }

        document.querySelectorAll(".tab").forEach(btn => {
            btn.addEventListener("click", () => {
                const target = btn.dataset.tab;
                document.querySelectorAll(".tab").forEach(b =>
                    b.classList.toggle("active", b === btn));
                document.querySelectorAll(".tab-panel").forEach(p =>
                    p.classList.toggle("active", p.dataset.panel === target));
            });
        });

        document.addEventListener("dragover", e => e.preventDefault());
        document.addEventListener("drop", e => e.preventDefault());

        function bindDropZone(dropId, inputId, onFile, acceptExt) {
            const drop = $(dropId);
            const input = $(inputId);
            drop.addEventListener("click", e => {
                if (e.target.closest(".file-clear")) return;
                input.click();
            });
            input.addEventListener("change", () => {
                if (input.files[0]) onFile(input.files[0]);
            });
            ["dragenter", "dragover"].forEach(ev => {
                drop.addEventListener(ev, e => {
                    e.preventDefault(); e.stopPropagation();
                    drop.classList.add("drag-over");
                });
            });
            ["dragleave", "drop"].forEach(ev => {
                drop.addEventListener(ev, e => {
                    e.preventDefault(); e.stopPropagation();
                    drop.classList.remove("drag-over");
                });
            });
            drop.addEventListener("drop", e => {
                const f = e.dataTransfer.files[0];
                if (!f) return;
                if (acceptExt && !f.name.toLowerCase().endsWith(acceptExt)) {
                    if (!confirm(`文件 "${f.name}" 不是 ${acceptExt}，仍要继续吗？`)) return;
                }
                onFile(f);
            });
        }

        function showFileInfo(prefix, file) {
            $(prefix + "-file-name").textContent = file.name;
            $(prefix + "-file-size").textContent = formatSize(file.size);
            $(prefix + "-file-info").hidden = false;
            $(prefix + "-drop").classList.add("has-file");
        }

        function clearFileInfo(prefix) {
            $(prefix + "-file-info").hidden = true;
            $(prefix + "-drop").classList.remove("has-file");
        }

        async function saveBlob(blob, filename) {
            if (window.showSaveFilePicker) {
                try {
                    const ext = filename.split(".").pop();
                    const handle = await window.showSaveFilePicker({
                        suggestedName: filename,
                        types: [{
                            description: ext.toUpperCase() + " 文件",
                            accept: { "application/octet-stream": ["." + ext] }
                        }]
                    });
                    const w = await handle.createWritable();
                    await w.write(blob);
                    await w.close();
                    return handle.name;
                } catch (e) {
                    if (e.name === "AbortError") return null;
                }
            }
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url; a.download = filename;
            document.body.appendChild(a); a.click();
            document.body.removeChild(a);
            setTimeout(() => URL.revokeObjectURL(url), 2000);
            return filename;
        }

        function pollTask(taskId, logEl, onDone) {
            let lastLen = 0;
            async function step() {
                try {
                    const res = await fetch(`/api/status/${taskId}`);
                    const data = await res.json();
                    if (!data.ok) throw new Error(data.msg);
                    if (data.logs.length !== lastLen) {
                        logEl.textContent = data.logs.join("\n");
                        logEl.scrollTop = logEl.scrollHeight;
                        lastLen = data.logs.length;
                    }
                    if (data.done) { onDone(data); return; }
                    setTimeout(step, 500);
                } catch (e) {
                    logEl.textContent += "\n[轮询错误] " + e;
                    setTimeout(step, 1500);
                }
            }
            step();
        }
