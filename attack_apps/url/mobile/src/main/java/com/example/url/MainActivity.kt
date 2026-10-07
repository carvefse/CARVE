package com.example.url

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.webkit.*
import androidx.annotation.RequiresApi
import androidx.appcompat.app.AppCompatActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class WebViewActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private val ioScope = CoroutineScope(Dispatchers.IO)
    private lateinit var targetUrl: String
    private lateinit var markerFamily: String
    private var allowLocalhostSelfSigned = false
    private var finishAfterPayloadDispatch = false
    private var payloadStartLogged = false
    private var payloadEndLogged = false

    companion object {
        private const val TAG = "WebTrack"
        private const val DEFAULT_START_URL = "https://news.ycombinator.com/"
        private const val TRACK_ENDPOINT = "https://httpbin.org/post"
        private const val EXTRA_TARGET_URL = "com.example.url.extra.TARGET_URL"
        private const val EXTRA_MARKER_FAMILY = "com.example.url.extra.MARKER_FAMILY"
        private const val EXTRA_ALLOW_LOCALHOST_SELF_SIGNED =
            "com.example.url.extra.ALLOW_LOCALHOST_SELF_SIGNED"
        private const val EXTRA_FINISH_AFTER_PAYLOAD_DISPATCH =
            "com.example.url.extra.FINISH_AFTER_PAYLOAD_DISPATCH"
        private val JSON = "application/json; charset=utf-8".toMediaType()
    }

    private val client by lazy {
        OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .writeTimeout(5, TimeUnit.SECONDS)
            .readTimeout(5, TimeUnit.SECONDS)
            .callTimeout(8, TimeUnit.SECONDS)
            .build()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_webview)

        targetUrl = intent.getStringExtra(EXTRA_TARGET_URL)?.trim().orEmpty()
            .ifEmpty { DEFAULT_START_URL }
        markerFamily = intent.getStringExtra(EXTRA_MARKER_FAMILY)?.trim()
            ?.takeIf { it.matches(Regex("[a-z0-9_]+")) }
            ?: "url"
        allowLocalhostSelfSigned = intent.getBooleanExtra(EXTRA_ALLOW_LOCALHOST_SELF_SIGNED, false)
        finishAfterPayloadDispatch =
            intent.getBooleanExtra(EXTRA_FINISH_AFTER_PAYLOAD_DISPATCH, false)

        webView = findViewById(R.id.webview)
        with(webView.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onConsoleMessage(consoleMessage: ConsoleMessage): Boolean {
                val message = consoleMessage.message()
                Log.i(TAG, "CONSOLE: $message")
                if (message == "CARVE_POC_DISPATCH family=$markerFamily") {
                    markPayloadEnd("page_script_dispatch")
                }
                return true
            }
        }

        webView.webViewClient = object : WebViewClient() {

            @Deprecated("Use Lollipop version when available")
            override fun shouldOverrideUrlLoading(view: WebView, url: String): Boolean {
                Log.d(TAG, "NAV legacy: $url")
                trackVisit(url, true, null, null)
                return false
            }

            @RequiresApi(Build.VERSION_CODES.LOLLIPOP)
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val uri = request.url
                val headers = request.requestHeaders ?: emptyMap()
                val referrer = headers["Referer"]

                val scheme = uri.scheme?.lowercase()
                if (scheme == "http" || scheme == "https") {
                    if (request.isForMainFrame) {
                        val gesture = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) request.hasGesture() else null
                        Log.d(TAG, "NAV: $uri (mainFrame=${request.isForMainFrame}, gesture=$gesture)")
                        trackVisit(uri.toString(), true, gesture, referrer)
                    }
                    return false
                } else {
                    Log.w(TAG, "Blocked scheme: $scheme")
                    return true
                }
            }

            override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) {
                Log.d(TAG, "START: $url")
                if (!payloadStartLogged) {
                    payloadStartLogged = true
                    Log.i(
                        TAG,
                        "CARVE_PAYLOAD_START family=$markerFamily evidence=webview_page_started url=$url"
                    )
                }
                super.onPageStarted(view, url, favicon)
            }

            override fun onPageFinished(view: WebView, url: String) {
                Log.d(TAG, "FINISH: $url (title='${view.title}')")
                super.onPageFinished(view, url)
            }

            override fun onReceivedSslError(
                view: WebView,
                handler: SslErrorHandler,
                error: android.net.http.SslError
            ) {
                if (allowLocalhostSelfSigned && isFrozenLocalUrl(error.url)) {
                    Log.i(TAG, "Proceeding past frozen localhost certificate error for ${error.url}")
                    handler.proceed()
                } else {
                    handler.cancel()
                    markPayloadError("ssl_error", error.toString())
                }
            }

            override fun onReceivedError(
                view: WebView,
                request: WebResourceRequest,
                error: WebResourceError
            ) {
                if (request.isForMainFrame) {
                    markPayloadError("web_resource_error", error.description.toString())
                }
                super.onReceivedError(view, request, error)
            }
        }

        Log.i(TAG, "Frozen target selected: $targetUrl marker_family=$markerFamily")
        webView.loadUrl(targetUrl)
    }

    private fun isFrozenLocalUrl(rawUrl: String?): Boolean {
        val uri = rawUrl?.let(Uri::parse) ?: return false
        val target = Uri.parse(targetUrl)
        return uri.scheme == "https" && uri.host == "localhost" && uri.port == 4443 &&
            target.scheme == "https" && target.host == "localhost" && target.port == 4443
    }

    private fun markPayloadEnd(evidence: String) {
        if (payloadEndLogged) return
        payloadEndLogged = true
        Log.i(
            TAG,
            "CARVE_PAYLOAD_END family=$markerFamily success=true evidence=$evidence url=$targetUrl"
        )
        if (finishAfterPayloadDispatch) {
            Handler(Looper.getMainLooper()).postDelayed({ finishAndRemoveTask() }, 250)
        }
    }

    private fun markPayloadError(stage: String, detail: String) {
        Log.e(
            TAG,
            "CARVE_PAYLOAD_ERROR family=$markerFamily stage=$stage detail=${detail.replace('\n', ' ')}"
        )
    }

    private fun isOnline(): Boolean {
        return try {
            val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val nw = cm.activeNetwork ?: return false
            val caps = cm.getNetworkCapabilities(nw) ?: return false
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        } catch (t: Throwable) {
            // If anything goes wrong (including missing permission), don't crash.
            Log.w(TAG, "isOnline() check failed: ${t.message}")
            true // fall back to "assume online" so tracking still attempts
        }
    }

    private fun trackVisit(
        url: String,
        isForMainFrame: Boolean,
        hasUserGesture: Boolean?,
        referrer: String?
    ) {
        if (!isOnline()) {
            Log.d(TAG, "Offline, skip tracking.")
            return
        }

        val payload = JSONObject().apply {
            put("url", url)
            put("timestampMs", System.currentTimeMillis())
            put("isForMainFrame", isForMainFrame)
            hasUserGesture?.let { put("hasUserGesture", it) }
            referrer?.let { put("referrer", it) }
            put("userAgent", webView.settings.userAgentString)
        }.toString()

        ioScope.launch {
            try {
                val req = Request.Builder()
                    .url(TRACK_ENDPOINT)
                    .post(payload.toRequestBody(JSON))
                    .build()
                client.newCall(req).execute().use { resp ->
                    Log.d(TAG, "Track POST -> HTTP ${resp.code}")
                }
            } catch (t: Throwable) {
                Log.w(TAG, "Track POST failed: ${t.localizedMessage}")
            }
        }
    }

    override fun onDestroy() {
        (webView.parent as? android.view.ViewGroup)?.removeView(webView)
        webView.removeAllViews()
        webView.destroy()
        super.onDestroy()
    }
}
