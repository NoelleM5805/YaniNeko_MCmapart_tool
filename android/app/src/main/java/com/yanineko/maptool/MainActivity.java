package com.yanineko.maptool;

import android.app.DownloadManager;
import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.webkit.DownloadListener;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.OutputStream;
import java.util.Base64;

/**
 * 安卓宿主：进程内起 FastAPI 后端（Chaquopy），前端用系统 WebView 加载本地服务。
 *
 * 与桌面的关系：
 *   - 后端：src/maptool 原样打包进 APK（由 android/prepare.py 复制到 src/main/python/）。
 *   - 前端：web/index.html + css/js 随 maptool 包一起打包，get_base_dir() 通过 __file__ 找到。
 *   - 开关：Python 入口 main.py 设置 MAPART_PLATFORM=android，于是后端不开浏览器、不起
 *     「关页面自动退出」看门狗；生命周期完全由本 Activity 控制。
 */
public class MainActivity extends AppCompatActivity {

    private WebView webView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        webView = new WebView(this);
        setContentView(webView);
        WebSettings ws = webView.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);          // 前端配置持久化用 localStorage
        ws.setAllowFileAccess(false);
        webView.setWebViewClient(new WebViewClient());

        // 前端通过 window.AndroidBridge.saveFile(name, base64) 保存单文件（blob）
        webView.addJavascriptInterface(new Bridge(), "AndroidBridge");

        // 真实 URL 的下载（如「打包成 zip」走 /api/slice/zip/...）交给系统下载器
        webView.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent,
                                        String contentDisposition, String mimetype,
                                        long contentLength) {
                enqueueDownload(url, mimetype, guessName(contentDisposition, url));
            }
        });

        startBackend();
    }

    /** 起 Python 后端（慢，放后台线程），就绪后把 URL 交给 WebView。 */
    private void startBackend() {
        new Thread(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(MainActivity.this));
                }
                String url = Python.getInstance()
                        .getModule("main").callAttr("start").toString();
                runOnUiThread(() -> {
                    // 注入安卓标记：前端据此启用移动端交互 + 走桥接保存
                    webView.evaluateJavascript("window.__ANDROID__ = true;", null);
                    webView.loadUrl(url);
                });
            } catch (Throwable t) {
                t.printStackTrace();
                runOnUiThread(() -> Toast.makeText(MainActivity.this,
                        "后端启动失败：" + t.getMessage(), Toast.LENGTH_LONG).show());
            }
        }, "mapart-backend").start();
    }

    /** 用系统 DownloadManager 下载真实 URL（loopback 也能下）。 */
    private void enqueueDownload(String url, String mimetype, String filename) {
        try {
            DownloadManager.Request req = new DownloadManager.Request(Uri.parse(url));
            req.setMimeType(mimetype);
            req.setTitle(filename);
            req.setNotificationVisibility(
                    DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            req.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename);
            DownloadManager dm = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            if (dm != null) dm.enqueue(req);
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    private static String guessName(String contentDisposition, String url) {
        if (contentDisposition != null && contentDisposition.contains("filename=")) {
            String[] parts = contentDisposition.split("filename=");
            if (parts.length > 1) {
                String name = parts[1].replace("\"", "").split(";")[0].trim();
                if (!name.isEmpty()) return name;
            }
        }
        String[] seg = url.split("/");
        String last = seg.length > 0 ? seg[seg.length - 1] : "download";
        return last.isEmpty() ? "download" : last;
    }

    /** 前端 JS 桥接：把 blob 转成 base64 后写进系统「下载」目录。 */
    private class Bridge {
        @JavascriptInterface
        public void saveFile(String filename, String base64) {
            try {
                byte[] data = Base64.getDecoder().decode(base64);
                ContentValues values = new ContentValues();
                values.put(MediaStore.MediaColumns.DISPLAY_NAME, filename);
                values.put(MediaStore.MediaColumns.MIME_TYPE, "application/octet-stream");
                values.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS);
                Uri uri = getContentResolver().insert(
                        MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
                if (uri == null) throw new IllegalStateException("insert 返回 null");
                try (OutputStream os = getContentResolver().openOutputStream(uri)) {
                    if (os == null) throw new IllegalStateException("openOutputStream 返回 null");
                    os.write(data);
                }
                runOnUiThread(() -> Toast.makeText(MainActivity.this,
                        "已保存到「下载」：" + filename, Toast.LENGTH_LONG).show());
            } catch (Exception e) {
                e.printStackTrace();
                runOnUiThread(() -> Toast.makeText(MainActivity.this,
                        "保存失败：" + e.getMessage(), Toast.LENGTH_LONG).show());
            }
        }
    }
}
