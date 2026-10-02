// 50-usage
// 底部方块用量统计

        // ============================================================
        // 底部方块用量统计
        // ============================================================
        function renderUsage(counts, total, note) {
            const list = $("mp-usage-list");
            const sum = $("mp-usage-sum");
            list.textContent = "";
            if (!counts || !counts.length) {
                list.innerHTML = '<div class="usage-empty">没有可统计的数据</div>';
                sum.textContent = "生成或预览后显示";
                return;
            }
            const max = counts[0].count || 1;
            const frag = document.createDocumentFragment();
            counts.forEach(c => {
                const row = document.createElement("div");
                row.className = "usage-item";
                row.title = `${c.label} · ${c.hex} · ${c.id}`;

                const icon = document.createElement("span");
                icon.className = "pal-icon";
                icon.style.display = CFG.icons ? "" : "none";
                const blk = findBlock(c.id);
                if (blk) {
                    icon.style.backgroundPosition =
                        `calc(var(--icon-px) * ${-blk.cx}) calc(var(--icon-px) * ${-blk.cy})`;
                }

                const bar = document.createElement("span");
                bar.className = "usage-bar";
                bar.style.width = Math.max(2, Math.round(c.count / max * 34)) + "px";
                bar.style.background = c.hex;

                const name = document.createElement("span");
                name.className = "usage-name";
                name.textContent = c.label;

                const cnt = document.createElement("span");
                cnt.className = "usage-cnt";
                cnt.textContent = c.count.toLocaleString();

                const pct = document.createElement("span");
                pct.className = "usage-pct";
                pct.textContent = (c.percent != null ? c.percent : 0).toFixed(1) + "%";

                row.append(icon, bar, name, cnt, pct);
                frag.appendChild(row);
            });
            list.appendChild(frag);
            sum.textContent = (note ? note + " · " : "")
                + `共 ${counts.length} 种方块 · ${total.toLocaleString()} 个`;
        }

        const BLOCK_LOOKUP = {};
        function findBlock(id) {
            return BLOCK_LOOKUP[id];
        }
