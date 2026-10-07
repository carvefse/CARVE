package com.example.jpegcve

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.widget.Button
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import java.util.concurrent.atomic.AtomicBoolean

class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "CARVE_ATTACK"
        private const val IMAGE_ASSET_NAME = "test_12000x12000.jpg"
        private const val EXTRA_AUTORUN = "com.example.jpegcve.extra.AUTORUN"
        private const val EXTRA_LOAD_FIRST = "com.example.jpegcve.extra.LOAD_FIRST"
        private const val EXTRA_STRESS_ITERATIONS = "com.example.jpegcve.extra.STRESS_ITERATIONS"
        private const val EXTRA_STRESS_DELAY_MS = "com.example.jpegcve.extra.STRESS_DELAY_MS"
        private const val EXTRA_START_DELAY_MS = "com.example.jpegcve.extra.START_DELAY_MS"
        private const val EXTRA_FINISH_AFTER_RUN = "com.example.jpegcve.extra.FINISH_AFTER_RUN"
    }

    private lateinit var imageView: ImageView
    private lateinit var infoText: TextView
    private lateinit var btnLoad: Button
    private lateinit var btnStress: Button
    private val payloadRunning = AtomicBoolean(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        imageView = findViewById(R.id.imageView)
        infoText = findViewById(R.id.infoText)
        btnLoad = findViewById(R.id.btnLoad)
        btnStress = findViewById(R.id.btnStress)

        btnLoad.setOnClickListener {
            Log.i(TAG, "Load button clicked")
            loadAndDisplayScaledBitmapFromAssets()
        }

        btnStress.setOnClickListener {
            Log.i(TAG, "Stress button clicked")
            startStressDecodeFromAssets(
                iterations = 100,
                delayMs = 200L,
                finishAfterRun = false,
            )
        }

        if (intent.getBooleanExtra(EXTRA_AUTORUN, false)) {
            val loadFirst = intent.getBooleanExtra(EXTRA_LOAD_FIRST, true)
            val iterations = intent.getIntExtra(EXTRA_STRESS_ITERATIONS, 25).coerceIn(1, 100)
            val delayMs = intent.getLongExtra(EXTRA_STRESS_DELAY_MS, 100L).coerceAtLeast(0L)
            val startDelayMs = intent.getLongExtra(EXTRA_START_DELAY_MS, 0L).coerceAtLeast(0L)
            val finishAfterRun = intent.getBooleanExtra(EXTRA_FINISH_AFTER_RUN, true)
            if (loadFirst) {
                loadAndDisplayScaledBitmapFromAssets()
            }
            Handler(Looper.getMainLooper()).postDelayed(
                {
                    startStressDecodeFromAssets(
                        iterations = iterations,
                        delayMs = delayMs,
                        finishAfterRun = finishAfterRun,
                    )
                },
                startDelayMs,
            )
        }
    }

    /**
     * Load the big JPEG from assets and downsample for display.
     */
    private fun loadAndDisplayScaledBitmapFromAssets() {
        try {
            // First pass: read bounds
            val boundsOpts = BitmapFactory.Options().apply {
                inJustDecodeBounds = true
            }
            assets.open(IMAGE_ASSET_NAME).use { input ->
                BitmapFactory.decodeStream(input, null, boundsOpts)
            }

            val srcWidth = boundsOpts.outWidth
            val srcHeight = boundsOpts.outHeight

            if (srcWidth <= 0 || srcHeight <= 0) {
                Toast.makeText(this, "Failed to read image bounds.", Toast.LENGTH_SHORT).show()
                Log.e(TAG, "Failed to read bounds from asset $IMAGE_ASSET_NAME")
                return
            }

            Log.i(TAG, "Source image size (asset): ${srcWidth}x${srcHeight}")

            // Downsample for display (~2000px max side)
            val maxDim = maxOf(srcWidth, srcHeight)
            var inSampleSize = 1
            while (maxDim / inSampleSize > 2000) {
                inSampleSize *= 2
            }

            val decodeOpts = BitmapFactory.Options().apply {
                this.inSampleSize = inSampleSize
            }

            val bm: Bitmap? = assets.open(IMAGE_ASSET_NAME).use { input ->
                BitmapFactory.decodeStream(input, null, decodeOpts)
            }

            if (bm == null) {
                Toast.makeText(this, "Decode failed.", Toast.LENGTH_SHORT).show()
                Log.e(TAG, "Scaled decode failed for asset $IMAGE_ASSET_NAME")
                return
            }

            Log.i(
                TAG,
                "Decoded bitmap (display) size: ${bm.width}x${bm.height}, bytes=${bm.byteCount}, inSampleSize=$inSampleSize"
            )

            imageView.setImageBitmap(bm)
            infoText.text =
                "Loaded from assets: ${srcWidth}x${srcHeight} → shown as ${bm.width}x${bm.height} (inSampleSize=$inSampleSize)"
        } catch (e: Exception) {
            Log.e(TAG, "Error loading bitmap from assets", e)
            Toast.makeText(this, "Error loading asset image", Toast.LENGTH_LONG).show()
        }
    }

    /**
     * Stress test: repeatedly decode full-resolution JPEG from assets.
     * This is where you push memory / exercise libjpeg.
     */
    private fun startStressDecodeFromAssets(
        iterations: Int,
        delayMs: Long,
        finishAfterRun: Boolean,
    ) {
        if (!payloadRunning.compareAndSet(false, true)) {
            Log.w(TAG, "CARVE_PAYLOAD_REJECTED family=jpegcve reason=already_running")
            return
        }
        Toast.makeText(this, "Starting stress decode (assets) – check logcat.", Toast.LENGTH_SHORT)
            .show()
        Log.i(
            TAG,
            "CARVE_PAYLOAD_START family=jpegcve iterations=$iterations delay_ms=$delayMs asset=$IMAGE_ASSET_NAME",
        )

        Thread {
            var completedIterations = 0
            var success = false
            try {
                for (i in 1..iterations) {
                    val opts = BitmapFactory.Options() // full size
                    val bm: Bitmap? = assets.open(IMAGE_ASSET_NAME).use { input ->
                        BitmapFactory.decodeStream(input, null, opts)
                    }

                    if (bm == null) {
                        Log.e(TAG, "Stress decode failed at iteration $i")
                        break
                    }

                    val bytes = bm.byteCount
                    completedIterations = i
                    Log.i(TAG, "CARVE_PAYLOAD_PROGRESS family=jpegcve iteration=$i bytes=$bytes")

                    // Comment out recycle() to increase memory pressure if you want
                    // bm.recycle()

                    if (delayMs > 0L) {
                        Thread.sleep(delayMs)
                    }
                }
                success = completedIterations == iterations
            } catch (error: Throwable) {
                Log.e(TAG, "CARVE_PAYLOAD_ERROR family=jpegcve iteration=${completedIterations + 1}", error)
            } finally {
                Log.i(
                    TAG,
                    "CARVE_PAYLOAD_END family=jpegcve success=$success completed=$completedIterations requested=$iterations",
                )
                payloadRunning.set(false)
                runOnUiThread {
                    infoText.text =
                        "Stress decode completed $completedIterations/$iterations iterations (success=$success)"
                    if (finishAfterRun && !isFinishing) {
                        finish()
                    }
                }
            }
        }.apply { name = "carve-jpegcve-payload" }.start()
    }
}
