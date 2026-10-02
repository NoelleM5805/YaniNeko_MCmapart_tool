// 10-modal
// 预览图放大模态框（滚轮缩放 / 拖动）

        // ============================================================
        // 图片放大模态框
        // ============================================================
        const ZOOM = {
            scale: 1, tx: 0, ty: 0,
            dragging: false,
            startX: 0, startY: 0, startTx: 0, startTy: 0,
        };

        function applyZoomTransform() {
            const img = $("mp-modal-img");
            img.style.transform =
                `translate(${ZOOM.tx}px, ${ZOOM.ty}px) scale(${ZOOM.scale})`;
            $("mp-modal-zoom").textContent = Math.round(ZOOM.scale * 100) + "%";
        }

        function openModal(src) {
            const modal = $("mp-modal");
            const img = $("mp-modal-img");
            img.src = src;
            ZOOM.scale = 1; ZOOM.tx = 0; ZOOM.ty = 0;
            applyZoomTransform();
            modal.hidden = false;

            img.onload = () => {
                const maxW = window.innerWidth * 0.9;
                const maxH = window.innerHeight * 0.9;
                const fitScale = Math.min(1, maxW / img.naturalWidth, maxH / img.naturalHeight);
                if (fitScale < 1) {
                    ZOOM.scale = fitScale;
                    applyZoomTransform();
                }
                // Finally force 1:1 pixel display; use Fit to see the whole image.
                ZOOM.scale = 1; ZOOM.tx = 0; ZOOM.ty = 0;
                applyZoomTransform();
            };
        }

        function closeModal() {
            const modal = $("mp-modal");
            modal.hidden = true;
            const img = $("mp-modal-img");
            img.src = "";
            img.style.transform = "";
            ZOOM.dragging = false;
            img.classList.remove("grabbing");
        }

        function zoomAt(factor, clientX, clientY) {
            const img = $("mp-modal-img");
            const newScale = Math.max(0.1, Math.min(16, ZOOM.scale * factor));
            const rect = img.getBoundingClientRect();
            const cx = clientX - (rect.left + rect.width / 2);
            const cy = clientY - (rect.top + rect.height / 2);
            const ratio = newScale / ZOOM.scale;
            ZOOM.tx -= cx * (ratio - 1);
            ZOOM.ty -= cy * (ratio - 1);
            ZOOM.scale = newScale;
            applyZoomTransform();
        }

        function zoomCenter(factor) {
            zoomAt(factor, window.innerWidth / 2, window.innerHeight / 2);
        }

        function fitToScreen() {
            const img = $("mp-modal-img");
            if (!img.naturalWidth || !img.naturalHeight) return;
            const maxW = window.innerWidth * 0.9;
            const maxH = window.innerHeight * 0.9;
            ZOOM.scale = Math.min(maxW / img.naturalWidth, maxH / img.naturalHeight);
            ZOOM.tx = 0; ZOOM.ty = 0;
            applyZoomTransform();
        }

        function zoom100() {
            ZOOM.scale = 1; ZOOM.tx = 0; ZOOM.ty = 0;
            applyZoomTransform();
        }

        (function initModal() {
            const modal = $("mp-modal");
            const img = $("mp-modal-img");

            modal.addEventListener("click", (e) => {
                if (e.target === modal) closeModal();
            });
            $("mp-modal-close").addEventListener("click", closeModal);

            document.addEventListener("keydown", (e) => {
                if (modal.hidden) return;
                if (e.key === "Escape") closeModal();
                if (e.key === "+" || e.key === "=") zoomCenter(1.2);
                if (e.key === "-" || e.key === "_") zoomCenter(1 / 1.2);
                if (e.key === "0") fitToScreen();
            });

            modal.addEventListener("wheel", (e) => {
                e.preventDefault();
                const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
                zoomAt(factor, e.clientX, e.clientY);
            }, { passive: false });

            img.addEventListener("mousedown", (e) => {
                e.preventDefault();
                ZOOM.dragging = true;
                ZOOM.startX = e.clientX; ZOOM.startY = e.clientY;
                ZOOM.startTx = ZOOM.tx; ZOOM.startTy = ZOOM.ty;
                img.classList.add("grabbing");
            });
            window.addEventListener("mousemove", (e) => {
                if (!ZOOM.dragging) return;
                ZOOM.tx = ZOOM.startTx + (e.clientX - ZOOM.startX);
                ZOOM.ty = ZOOM.startTy + (e.clientY - ZOOM.startY);
                applyZoomTransform();
            });
            window.addEventListener("mouseup", () => {
                if (ZOOM.dragging) {
                    ZOOM.dragging = false;
                    img.classList.remove("grabbing");
                }
            });

            img.addEventListener("touchstart", (e) => {
                if (e.touches.length !== 1) return;
                const t = e.touches[0];
                ZOOM.dragging = true;
                ZOOM.startX = t.clientX; ZOOM.startY = t.clientY;
                ZOOM.startTx = ZOOM.tx; ZOOM.startTy = ZOOM.ty;
            }, { passive: true });
            img.addEventListener("touchmove", (e) => {
                if (!ZOOM.dragging || e.touches.length !== 1) return;
                const t = e.touches[0];
                ZOOM.tx = ZOOM.startTx + (t.clientX - ZOOM.startX);
                ZOOM.ty = ZOOM.startTy + (t.clientY - ZOOM.startY);
                applyZoomTransform();
            }, { passive: true });
            img.addEventListener("touchend", () => { ZOOM.dragging = false; });

            $("mp-modal-in").addEventListener("click", () => zoomCenter(1.25));
            $("mp-modal-out").addEventListener("click", () => zoomCenter(1 / 1.25));
            $("mp-modal-fit").addEventListener("click", fitToScreen);
            $("mp-modal-100").addEventListener("click", zoom100);
        })();
