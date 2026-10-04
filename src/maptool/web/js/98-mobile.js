// 98-mobile
// 移动端适配：双指捏合缩放、安卓下载桥接、触屏撤回/重做悬浮按钮
//
// 只在「安卓宿主」下激活（宿主通过 addJavascriptInterface / evaluateJavascript
// 注入 window.AndroidBridge 与 window.__ANDROID__ = true）。
//
// 为什么不用触屏探测（maxTouchPoints / pointer:coarse / ontouchstart）：
//   无头 Edge/Chromium 会把这些全报成触屏（maxTouchPoints=10、coarse=true），
//   会导致 tests/web_split_check.py 的桌面 DOM 比对被注入的悬浮按钮破坏。
//   安卓宿主标记是唯一可靠的、可由我们控制的开关。

(function () {
    "use strict";

    var bridge = window.AndroidBridge;
    var isAndroid = !!window.__ANDROID__ || !!bridge;
    if (!isAndroid) return;              // 非安卓：零副作用

    function touchDist(a, b) {
        return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    }

    // ---------------------------------------------------------------
    // 安卓下载桥接
    //  安卓 WebView 不支持 showSaveFilePicker / <a download>+blob 落盘，
    //  宿主注入 AndroidBridge.saveFile(name, base64) 写入系统下载目录。
    //  这里把全局 saveBlob 换成「优先走桥接」，地图画 / 地衣的单文件保存都经过它。
    // ---------------------------------------------------------------
    function blobToBase64(blob) {
        return new Promise(function (resolve, reject) {
            var r = new FileReader();
            r.onload = function () { resolve(String(r.result).split(",")[1]); };
            r.onerror = function () { reject(r.error); };
            r.readAsDataURL(blob);
        });
    }

    if (bridge && typeof bridge.saveFile === "function") {
        var _saveBlob = window.saveBlob;
        window.saveBlob = function (blob, filename) {
            return blobToBase64(blob).then(function (b64) {
                bridge.saveFile(filename, b64);
                return filename;
            }).catch(function () {
                return (_saveBlob && _saveBlob !== window.saveBlob)
                    ? _saveBlob(blob, filename) : filename;
            });
        };
    }

    // ---------------------------------------------------------------
    // 双指捏合缩放：专注模式预览图
    //   单指平移已经在 92-focus.js 里有了，这里只补两指捏合（按两指中点缩放）。
    // ---------------------------------------------------------------
    function attachFocusPinch() {
        var box = window.$ ? $("mp-preview-pal") : document.getElementById("mp-preview-pal");
        if (!box) return;
        var pinchDist = 0;
        box.addEventListener("touchstart", function (e) {
            if (typeof focusOn !== "function" || !focusOn()) return;
            if (e.touches.length === 2) pinchDist = touchDist(e.touches[0], e.touches[1]);
        }, { passive: true });
        box.addEventListener("touchmove", function (e) {
            if (e.touches.length !== 2 || !pinchDist) return;
            var d = touchDist(e.touches[0], e.touches[1]);
            var factor = d / pinchDist;
            pinchDist = d;
            var cx = (e.touches[0].clientX + e.touches[1].clientX) / 2;
            var cy = (e.touches[0].clientY + e.touches[1].clientY) / 2;
            if (typeof focusZoomAt === "function") focusZoomAt(factor, cx, cy);
        }, { passive: true });
        box.addEventListener("touchend", function () { pinchDist = 0; });
        box.addEventListener("touchcancel", function () { pinchDist = 0; });
    }

    // ---------------------------------------------------------------
    // 双指捏合缩放：放大模态框（单指拖动 10-modal.js 里已有）
    // ---------------------------------------------------------------
    function attachModalPinch() {
        var img = window.$ ? $("mp-modal-img") : document.getElementById("mp-modal-img");
        if (!img) return;
        var pinchDist = 0;
        img.addEventListener("touchstart", function (e) {
            if (e.touches.length === 2) pinchDist = touchDist(e.touches[0], e.touches[1]);
        }, { passive: true });
        img.addEventListener("touchmove", function (e) {
            if (e.touches.length !== 2 || !pinchDist) return;
            var d = touchDist(e.touches[0], e.touches[1]);
            var factor = d / pinchDist;
            pinchDist = d;
            var cx = (e.touches[0].clientX + e.touches[1].clientX) / 2;
            var cy = (e.touches[0].clientY + e.touches[1].clientY) / 2;
            if (typeof zoomAt === "function") zoomAt(factor, cx, cy);
        }, { passive: true });
        img.addEventListener("touchend", function () { pinchDist = 0; });
        img.addEventListener("touchcancel", function () { pinchDist = 0; });
    }

    // ---------------------------------------------------------------
    // 触屏撤回 / 重做悬浮按钮（键盘 Ctrl+Z 在手机上不存在）
    // ---------------------------------------------------------------
    function addUndoBar() {
        var bar = document.createElement("div");
        bar.id = "mb-undo-bar";
        var u = document.createElement("button");
        u.id = "mb-undo";
        u.type = "button";
        u.setAttribute("aria-label", "撤回");
        u.textContent = "↩";
        var r = document.createElement("button");
        r.id = "mb-redo";
        r.type = "button";
        r.setAttribute("aria-label", "重做");
        r.textContent = "↪";
        bar.appendChild(u);
        bar.appendChild(r);
        document.body.appendChild(bar);

        function sync() {
            var canU = (typeof UNDO !== "undefined") && UNDO.index > 0;
            var canR = (typeof UNDO !== "undefined") && UNDO.index >= 0
                       && UNDO.index < UNDO.stack.length - 1;
            u.disabled = !canU;
            r.disabled = !canR;
        }
        u.addEventListener("click", function () { if (undoStep()) sync(); });
        r.addEventListener("click", function () { if (redoStep()) sync(); });
        sync();
        // 侧边栏的撤回按钮 / 别的入口触发撤回时，同步一下我们按钮的可用态
        setInterval(sync, 1000);
    }

    attachFocusPinch();
    attachModalPinch();
    addUndoBar();
})();
