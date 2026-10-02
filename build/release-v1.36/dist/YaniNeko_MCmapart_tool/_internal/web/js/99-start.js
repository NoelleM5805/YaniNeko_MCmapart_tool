// 99-start
// 启动：进入专注模式、拉调色板、连保活

        applySoloPreview();

        // 左边栏的「配色调参」镜像：等 90-init 把 CFG 里的表单值贴到主区之后再建，
        // 这样镜像一开始就是对的
        if (typeof rpBuildMirrors === "function") rpBuildMirrors();

        loadPalette();
        keepaliveConnect();
