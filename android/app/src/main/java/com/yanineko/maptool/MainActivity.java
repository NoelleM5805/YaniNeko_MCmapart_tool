package com.yanineko.maptool;

import android.app.DownloadManager;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.webkit.DownloadListener;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
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

    private static final int REQUEST_FILE_CHOOSER = 1001;

    private WebView webView;
    /** 正在等待文件选择结果的回调（WebView 的文件上传必须自己实现）。 */
    private ValueCallback<Uri[]> pendingFileCallback;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 调试构建允许 Chrome DevTools 远程调试 WebView（chrome://inspect）
        if ((getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0) {
            WebView.setWebContentsDebuggingEnabled(true);
        }

        webView = new WebView(this);
        setContentView(webView);
        WebSettings ws = webView.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);          // 前端配置持久化用 localStorage
        ws.setAllowFileAccess(false);
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                // 注意：页面导航会重置 window，所以标记要在每次加载完成后重新打。
                // （前端 98-mobile.js 同时认 window.AndroidBridge，那个不受导航影响。）
                view.evaluateJavascript("window.__ANDROID__ = true;", null);
            }
        });

        // 关键：<input type="file"> 在 WebView 里必须由 WebChromeClient 接管，
        // 否则点了没反应（前端是「点上传区 -> input.click()」触发选择器）。
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view,
                                             ValueCallback<Uri[]> filePathCallback,
                                             FileChooserParams fileChooserParams) {
                // 上一次还没回调就再点一次：先把旧回调作废，否则输入框会一直卡住
                if (pendingFileCallback != null) {
                    pendingFileCallback.onReceiveValue(null);
                    pendingFileCallback = null;
                }
                pendingFileCallback = filePathCallback;

                Intent intent;
                try {
                    intent = fileChooserParams.createIntent();
                } catch (Exception e) {
                    intent = null;
                }
                if (intent == null || intent.getType() == null) {
                    // `.litematic` 之类未知扩展名拿不到 MIME 时，退回不限类型
                    intent = new Intent(Intent.ACTION_GET_CONTENT);
                    intent.addCategory(Intent.CATEGORY_OPENABLE);
                    intent.setType("*/*");
                }
                intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                if (fileChooserParams.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE) {
                    intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                }
                try {
                    startActivityForResult(intent, REQUEST_FILE_CHOOSER);
                    return true;
                } catch (Exception e) {
                    pendingFileCallback = null;
                    Toast.makeText(MainActivity.this,
                            "没有可用的文件选择器：" + e.getMessage(),
                            Toast.LENGTH_LONG).show();
                    return false;
                }
            }
        });

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

    /** 文件选择器返回：把结果交回 WebView（取消也必须回调，否则输入框卡死）。 */
    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQUEST_FILE_CHOOSER) {
            if (pendingFileCallback != null) {
                Uri[] results = null;
                if (resultCode == RESULT_OK && data != null) {
                    results = WebChromeClient.FileChooserParams.parseResult(resultCode, data);
                }
                pendingFileCallback.onReceiveValue(results);
                pendingFileCallback = null;
            }
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
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
