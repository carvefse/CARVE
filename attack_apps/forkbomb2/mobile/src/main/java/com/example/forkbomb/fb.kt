package com.example.forkbomb

import java.io.BufferedReader
import android.util.Log
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
object ShellExecutor {
    data class Result(
        val exitCode: Int,
        val stdout: String,
        val stderr: String
    )

    fun execute(command: String, timeoutSec: Long = 5): Result {
        val pb = ProcessBuilder("sh", "-c", command)
        pb.redirectErrorStream(false)

        val process = pb.start()

        val executor = Executors.newFixedThreadPool(2)
        val stdoutFuture = executor.submit<String> {
            process.inputStream.bufferedReader().use(BufferedReader::readText)
        }
        val stderrFuture = executor.submit<String> {
            process.errorStream.bufferedReader().use(BufferedReader::readText)
        }

        if (timeoutSec > 0L) {
            if (!process.waitFor(timeoutSec, TimeUnit.SECONDS)) {
                process.destroy()
                process.waitFor(2, TimeUnit.SECONDS)
                process.destroyForcibly()
            }
        } else {
            process.waitFor()
        }

        val stdout = try {
            stdoutFuture.get(1, TimeUnit.SECONDS)
        } catch (e: TimeoutException) {
            Log.w("ShellExecutor", "stdout timed out")
            "" // or partial output
        }

        val stderr = try {
            stderrFuture.get(1, TimeUnit.SECONDS)
        } catch (e: TimeoutException) {
            Log.w("ShellExecutor", "stderr timed out")
            ""
        }
        executor.shutdownNow()
        val exitCode = process.exitValue()

        return Result(exitCode, stdout, stderr)
    }
}
