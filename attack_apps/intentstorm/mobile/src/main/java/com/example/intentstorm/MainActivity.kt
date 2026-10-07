package com.example.intentstorm

import android.content.Intent
import android.os.Bundle
import android.os.SystemClock
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch


class MainActivity : ComponentActivity() {
    companion object {
        private const val TAG = "CARVE_ATTACK"
        private const val EXTRA_AUTORUN = "com.example.intentstorm.extra.AUTORUN"
        private const val EXTRA_INTERVAL_MS = "com.example.intentstorm.extra.INTERVAL_MS"
        private const val EXTRA_DURATION_MS = "com.example.intentstorm.extra.DURATION_MS"
        private const val EXTRA_MAX_LAUNCHES = "com.example.intentstorm.extra.MAX_LAUNCHES"
        private const val EXTRA_FINISH_PASSIVE_CHILDREN =
            "com.example.intentstorm.extra.FINISH_PASSIVE_CHILDREN"
        private const val EXTRA_CHILD_LIFETIME_MS =
            "com.example.intentstorm.extra.CHILD_LIFETIME_MS"
        private const val EXTRA_PASSIVE_CHILD = "com.example.intentstorm.extra.PASSIVE_CHILD"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (intent.getBooleanExtra(EXTRA_PASSIVE_CHILD, false)) {
            configurePassiveChild()
            return
        }

        if (intent.getBooleanExtra(EXTRA_AUTORUN, false)) {
            startBoundedStorm()
        }
    }

    private fun configurePassiveChild() {
        val finishChild = intent.getBooleanExtra(EXTRA_FINISH_PASSIVE_CHILDREN, true)
        val lifetimeMs = intent.getLongExtra(EXTRA_CHILD_LIFETIME_MS, 250L).coerceAtLeast(0L)
        if (finishChild) {
            lifecycleScope.launch {
                if (lifetimeMs > 0L) {
                    delay(lifetimeMs)
                }
                finish()
            }
        }
    }

    private fun startBoundedStorm() {
        val intervalMs = intent.getLongExtra(EXTRA_INTERVAL_MS, 100L).coerceAtLeast(1L)
        val durationMs = intent.getLongExtra(EXTRA_DURATION_MS, 8_000L).coerceAtLeast(1L)
        val maxLaunches = intent.getIntExtra(EXTRA_MAX_LAUNCHES, -1)
        val finishChildren = intent.getBooleanExtra(EXTRA_FINISH_PASSIVE_CHILDREN, true)
        val childLifetimeMs =
            intent.getLongExtra(EXTRA_CHILD_LIFETIME_MS, 250L).coerceAtLeast(0L)

        lifecycleScope.launch {
            val startMs = SystemClock.elapsedRealtime()
            var launches = 0
            var success = false
            Log.i(
                TAG,
                "CARVE_PAYLOAD_START family=intentstorm interval_ms=$intervalMs " +
                    "duration_ms=$durationMs max_launches=$maxLaunches",
            )
            try {
                while (
                    SystemClock.elapsedRealtime() - startMs < durationMs &&
                    (maxLaunches < 0 || launches < maxLaunches)
                ) {
                    val child = Intent(this@MainActivity, MainActivity::class.java).apply {
                        putExtra(EXTRA_PASSIVE_CHILD, true)
                        putExtra(EXTRA_FINISH_PASSIVE_CHILDREN, finishChildren)
                        putExtra(EXTRA_CHILD_LIFETIME_MS, childLifetimeMs)
                    }
                    startActivity(child)
                    launches += 1
                    delay(intervalMs)
                }
                success = true
            } catch (error: Throwable) {
                Log.e(TAG, "CARVE_PAYLOAD_ERROR family=intentstorm launches=$launches", error)
            } finally {
                val elapsedMs = SystemClock.elapsedRealtime() - startMs
                Log.i(
                    TAG,
                    "CARVE_PAYLOAD_END family=intentstorm success=$success " +
                        "launches=$launches elapsed_ms=$elapsedMs",
                )
            }
        }
    }
}
