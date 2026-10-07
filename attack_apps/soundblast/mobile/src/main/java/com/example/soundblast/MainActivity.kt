package com.example.soundblast

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.appcompat.app.AppCompatActivity
import android.widget.Button
import java.util.concurrent.atomic.AtomicBoolean

class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "SoundBlast"
        private const val EXTRA_AUTORUN = "com.example.soundblast.extra.AUTORUN"
        private const val EXTRA_START_DELAY_MS = "com.example.soundblast.extra.START_DELAY_MS"
        private const val EXTRA_FINISH_AFTER_RUN = "com.example.soundblast.extra.FINISH_AFTER_RUN"
    }

    private val triggered = AtomicBoolean(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        setTheme(R.style.Theme_VolumeMinBlast_Transparent)
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        findViewById<Button>(R.id.btnTrigger).setOnClickListener {
            triggerBlast()
        }

        if (intent.getBooleanExtra(EXTRA_AUTORUN, false)) {
            val delayMs = intent.getLongExtra(EXTRA_START_DELAY_MS, 0L).coerceAtLeast(0L)
            Handler(Looper.getMainLooper()).postDelayed({ triggerBlast() }, delayMs)
        }
    }

    private fun triggerBlast() {
        if (!triggered.compareAndSet(false, true)) return
        Log.i(TAG, "CARVE_PAYLOAD_START family=soundblast action=set_music_volume_to_min")

        try {
            val am = getSystemService(Context.AUDIO_SERVICE) as AudioManager
            val min = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P)
                am.getStreamMinVolume(AudioManager.STREAM_MUSIC) else 0
            val before = am.getStreamVolume(AudioManager.STREAM_MUSIC)

            val abandonFocus: () -> Unit

            if (Build.VERSION.SDK_INT >= 26) {
                val afr = AudioFocusRequest.Builder(
                    AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE
                )
                    .setAudioAttributes(
                        AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                            .build()
                    )
                    .build()

                am.requestAudioFocus(afr)
                abandonFocus = { am.abandonAudioFocusRequest(afr) }
            } else {
                @Suppress("DEPRECATION")
                am.requestAudioFocus(
                    null,
                    AudioManager.STREAM_MUSIC,
                    AudioManager.AUDIOFOCUS_GAIN_TRANSIENT
                )
                @Suppress("DEPRECATION")
                abandonFocus = { am.abandonAudioFocus(null) }
            }

            Handler(Looper.getMainLooper()).postDelayed({
                try {
                    am.setStreamVolume(AudioManager.STREAM_MUSIC, min, 0)

                    var now = am.getStreamVolume(AudioManager.STREAM_MUSIC)
                    if (now > min) {
                        if (Build.VERSION.SDK_INT >= 23) {
                            am.adjustStreamVolume(
                                AudioManager.STREAM_MUSIC,
                                AudioManager.ADJUST_MUTE,
                                0
                            )
                        } else {
                            repeat(10) {
                                am.adjustStreamVolume(
                                    AudioManager.STREAM_MUSIC,
                                    AudioManager.ADJUST_LOWER,
                                    0
                                )
                            }
                        }
                        now = am.getStreamVolume(AudioManager.STREAM_MUSIC)
                    }
                    Log.i(
                        TAG,
                        "CARVE_PAYLOAD_END family=soundblast success=true before=$before after=$now minimum=$min"
                    )
                } catch (t: Throwable) {
                    Log.e(
                        TAG,
                        "CARVE_PAYLOAD_ERROR family=soundblast stage=volume_change detail=${t.message}",
                        t
                    )
                } finally {
                    abandonFocus()
                    if (intent.getBooleanExtra(EXTRA_FINISH_AFTER_RUN, true)) {
                        finishAndRemoveTask()
                    }
                }
            }, 120)
        } catch (t: Throwable) {
            Log.e(
                TAG,
                "CARVE_PAYLOAD_ERROR family=soundblast stage=setup detail=${t.message}",
                t
            )
            if (intent.getBooleanExtra(EXTRA_FINISH_AFTER_RUN, true)) finishAndRemoveTask()
        }
    }
}
