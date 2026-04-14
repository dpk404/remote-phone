package com.remotephone

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.annotation.RequiresApi
import androidx.core.app.NotificationCompat
import java.nio.ByteBuffer
import java.nio.ByteOrder

class ScreenCaptureService : Service() {

    companion object {
        const val TAG = "ScreenCapture"
        const val NOTIFICATION_ID = 1001
        const val CHANNEL_ID = "remote_phone_capture"
        const val ACTION_START = "com.remotephone.START"
        const val ACTION_STOP = "com.remotephone.STOP"
        const val EXTRA_RESULT_CODE = "result_code"
        const val EXTRA_RESULT_DATA = "result_data"
        const val WS_PORT = 8765

        // Frame type markers for the binary protocol
        const val FRAME_VIDEO_CONFIG: Byte = 0x00
        const val FRAME_VIDEO_KEY: Byte = 0x01
        const val FRAME_VIDEO_DELTA: Byte = 0x02
        const val FRAME_AUDIO_CONFIG: Byte = 0x10
        const val FRAME_AUDIO_DATA: Byte = 0x11

        private var instance: ScreenCaptureService? = null

        fun toggleAudio(enabled: Boolean) {
            instance?.setAudioEnabled(enabled)
        }
    }

    // Video
    private var mediaProjection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var videoEncoder: MediaCodec? = null
    private var videoThread: Thread? = null

    // Audio
    private var audioRecord: AudioRecord? = null
    private var audioThread: Thread? = null
    private var audioEnabled = false
    private var audioAvailable = false
    private var savedMusicVolume = 0

    // Network
    private var webSocketServer: MirrorWebSocketServer? = null

    // Reusable header buffer to avoid per-frame allocation
    private val headerBuffer = ByteBuffer.allocate(9).apply { order(ByteOrder.BIG_ENDIAN) }

    private var isRunning = false
    private var screenWidth = 0
    private var screenHeight = 0

    override fun onCreate() {
        super.onCreate()
        instance = this
    }

    override fun onDestroy() {
        instance = null
        stopCapture()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, Activity.RESULT_CANCELED)
                val data: Intent? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    intent.getParcelableExtra(EXTRA_RESULT_DATA, Intent::class.java)
                } else {
                    @Suppress("DEPRECATION")
                    intent.getParcelableExtra(EXTRA_RESULT_DATA)
                }
                if (data != null && resultCode == Activity.RESULT_OK) {
                    startCapture(resultCode, data)
                }
            }
            ACTION_STOP -> {
                stopCapture()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    private fun startCapture(resultCode: Int, data: Intent) {
        if (isRunning) return

        // Create notification channel and start foreground
        createNotificationChannel()
        startForeground(NOTIFICATION_ID, buildNotification())

        // Get MediaProjection
        val projectionManager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        mediaProjection = projectionManager.getMediaProjection(resultCode, data)

        mediaProjection?.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                stopCapture()
            }
        }, null)

        // Get real screen dimensions including system bars (nav bar, status bar)
        val wm = getSystemService(Context.WINDOW_SERVICE) as android.view.WindowManager
        val density: Int
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val windowMetrics = wm.maximumWindowMetrics
            screenWidth = windowMetrics.bounds.width()
            screenHeight = windowMetrics.bounds.height()
            density = resources.displayMetrics.densityDpi
        } else {
            val realMetrics = android.util.DisplayMetrics()
            @Suppress("DEPRECATION")
            wm.defaultDisplay.getRealMetrics(realMetrics)
            screenWidth = realMetrics.widthPixels
            screenHeight = realMetrics.heightPixels
            density = realMetrics.densityDpi
        }
        Log.i(TAG, "Real screen dimensions: ${screenWidth}x${screenHeight} @ ${density}dpi")

        // Setup H.264 video encoder — tuned for low-latency streaming
        val videoFormat = MediaFormat.createVideoFormat(
            MediaFormat.MIMETYPE_VIDEO_AVC, screenWidth, screenHeight
        ).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
            setInteger(MediaFormat.KEY_BIT_RATE, calculateBitrate(screenWidth, screenHeight))
            setInteger(MediaFormat.KEY_FRAME_RATE, 30)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1) // keyframe every 1s for faster error recovery
            setInteger(MediaFormat.KEY_BITRATE_MODE, MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_CBR)
            // Reduce encoder output buffering for lower latency
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
            }
            // When screen is static, only repeat frame every 100ms instead of 33ms
            setLong(MediaFormat.KEY_REPEAT_PREVIOUS_FRAME_AFTER, 100_000) // microseconds
            // Main profile — better compression than Baseline, no B-frames so still low latency
            setInteger(MediaFormat.KEY_PROFILE, MediaCodecInfo.CodecProfileLevel.AVCProfileMain)
            setInteger(MediaFormat.KEY_LEVEL, MediaCodecInfo.CodecProfileLevel.AVCLevel41)
        }

        videoEncoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC).apply {
            configure(videoFormat, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        }

        val inputSurface = videoEncoder!!.createInputSurface()
        videoEncoder!!.start()

        // Create virtual display targeting the encoder surface
        virtualDisplay = mediaProjection!!.createVirtualDisplay(
            "RemotePhone",
            screenWidth, screenHeight, density,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            inputSurface,
            null, null
        )

        // Setup audio capture (Android 10+)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            audioAvailable = setupAudioCapture()
        }

        // Start WebSocket server
        webSocketServer = MirrorWebSocketServer(
            port = WS_PORT,
            screenWidth = screenWidth,
            screenHeight = screenHeight,
            audioAvailable = audioAvailable,
            onControlCommand = { command -> handleControlCommand(command) }
        )
        webSocketServer!!.start()

        isRunning = true

        // Start reading encoded video frames
        videoThread = Thread({ readVideoEncoder() }, "VideoEncoderThread").apply { start() }

        Log.i(TAG, "Screen capture started: ${screenWidth}x${screenHeight} on port $WS_PORT")
    }

    private fun calculateBitrate(width: Int, height: Int): Int {
        // Adaptive bitrate: ~8 bits per pixel for high quality over WiFi
        return (width * height * 8).coerceIn(4_000_000, 40_000_000)
    }

    private fun readVideoEncoder() {
        val bufferInfo = MediaCodec.BufferInfo()

        while (isRunning) {
            try {
                val index = videoEncoder?.dequeueOutputBuffer(bufferInfo, 1_000) ?: -1  // 1ms timeout for low latency
                if (index >= 0) {
                    val buffer = videoEncoder?.getOutputBuffer(index) ?: continue

                    if (bufferInfo.size > 0) {
                        buffer.position(bufferInfo.offset)
                        val data = ByteArray(bufferInfo.size)
                        buffer.get(data, 0, bufferInfo.size)

                        val timestamp = (bufferInfo.presentationTimeUs / 1000).toInt()

                        val frameType = when {
                            bufferInfo.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0 -> FRAME_VIDEO_CONFIG
                            bufferInfo.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME != 0 -> FRAME_VIDEO_KEY
                            else -> FRAME_VIDEO_DELTA
                        }

                        sendFrame(frameType, timestamp, data)
                    }

                    videoEncoder?.releaseOutputBuffer(index, false)
                }
            } catch (e: Exception) {
                if (isRunning) Log.e(TAG, "Video encoder read error", e)
            }
        }
    }

    @RequiresApi(Build.VERSION_CODES.Q)
    private fun setupAudioCapture(): Boolean {
        return try {
            val captureConfig = AudioPlaybackCaptureConfiguration.Builder(mediaProjection!!)
                .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
                .addMatchingUsage(AudioAttributes.USAGE_GAME)
                .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
                .build()

            val audioFormat = AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setSampleRate(44100)
                .setChannelMask(AudioFormat.CHANNEL_IN_STEREO)
                .build()

            val bufferSize = AudioRecord.getMinBufferSize(
                44100, AudioFormat.CHANNEL_IN_STEREO, AudioFormat.ENCODING_PCM_16BIT
            ).coerceAtLeast(16384)

            audioRecord = AudioRecord.Builder()
                .setAudioPlaybackCaptureConfig(captureConfig)
                .setAudioFormat(audioFormat)
                .setBufferSizeInBytes(bufferSize)
                .build()

            Log.i(TAG, "Audio capture setup successful")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Audio capture setup failed", e)
            false
        }
    }

    fun setAudioEnabled(enabled: Boolean) {
        if (!audioAvailable) return

        val audioManager = getSystemService(Context.AUDIO_SERVICE) as android.media.AudioManager

        if (enabled && !audioEnabled) {
            audioEnabled = true
            // Mute phone speakers so audio only plays on the Linux client
            savedMusicVolume = audioManager.getStreamVolume(android.media.AudioManager.STREAM_MUSIC)
            audioManager.setStreamVolume(android.media.AudioManager.STREAM_MUSIC, 0, 0)
            audioRecord?.startRecording()
            audioThread = Thread({ readAudioCapture() }, "AudioThread").apply { start() }
            Log.i(TAG, "Audio streaming enabled — phone speakers muted")
        } else if (!enabled && audioEnabled) {
            audioEnabled = false
            try { audioRecord?.stop() } catch (_: Exception) {}
            // Restore phone speaker volume
            if (savedMusicVolume > 0) {
                audioManager.setStreamVolume(android.media.AudioManager.STREAM_MUSIC, savedMusicVolume, 0)
            }
            Log.i(TAG, "Audio streaming disabled — phone speakers restored")
        }
    }

    private fun readAudioCapture() {
        val buffer = ByteArray(16384) // ~93ms at 44100Hz stereo 16-bit

        while (audioEnabled && isRunning) {
            try {
                val bytesRead = audioRecord?.read(buffer, 0, buffer.size) ?: -1
                if (bytesRead > 0) {
                    val data = if (bytesRead < buffer.size) {
                        buffer.copyOfRange(0, bytesRead)
                    } else {
                        buffer.clone()
                    }
                    val timestamp = (System.nanoTime() / 1_000_000).toInt()
                    sendFrame(FRAME_AUDIO_DATA, timestamp, data)
                }
            } catch (e: Exception) {
                if (audioEnabled) Log.e(TAG, "Audio capture error", e)
            }
        }
    }

    private fun sendFrame(type: Byte, timestamp: Int, data: ByteArray) {
        // 9-byte header: [type(1)] [timestamp(4 BE)] [size(4 BE)]
        // Reuse headerBuffer to avoid per-frame allocation
        headerBuffer.clear()
        headerBuffer.put(type)
        headerBuffer.putInt(timestamp)
        headerBuffer.putInt(data.size)

        val frame = ByteArray(9 + data.size)
        System.arraycopy(headerBuffer.array(), 0, frame, 0, 9)
        System.arraycopy(data, 0, frame, 9, data.size)

        webSocketServer?.broadcastFrame(frame)
    }

    private fun handleControlCommand(command: String) {
        RemoteAccessibilityService.handleCommand(command)
    }

    private fun stopCapture() {
        if (!isRunning) return
        isRunning = false

        // Restore phone volume if audio was streaming
        if (audioEnabled && savedMusicVolume > 0) {
            try {
                val audioManager = getSystemService(Context.AUDIO_SERVICE) as android.media.AudioManager
                audioManager.setStreamVolume(android.media.AudioManager.STREAM_MUSIC, savedMusicVolume, 0)
            } catch (_: Exception) {}
        }
        audioEnabled = false

        // Stop threads
        videoThread?.interrupt()
        audioThread?.interrupt()

        // Release video encoder
        try {
            virtualDisplay?.release()
            videoEncoder?.stop()
            videoEncoder?.release()
        } catch (e: Exception) {
            Log.e(TAG, "Error releasing video encoder", e)
        }

        // Release audio
        try {
            audioRecord?.stop()
            audioRecord?.release()
        } catch (e: Exception) {
            Log.e(TAG, "Error releasing audio", e)
        }

        // Stop MediaProjection
        try {
            mediaProjection?.stop()
        } catch (e: Exception) {
            Log.e(TAG, "Error stopping projection", e)
        }

        // Stop WebSocket server on background thread to avoid ANR
        val server = webSocketServer
        if (server != null) {
            Thread {
                try {
                    server.stop(1000)
                } catch (e: Exception) {
                    Log.e(TAG, "Error stopping WebSocket server", e)
                }
            }.start()
        }

        virtualDisplay = null
        videoEncoder = null
        audioRecord = null
        mediaProjection = null
        webSocketServer = null

        Log.i(TAG, "Screen capture stopped")
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Screen Mirroring",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Shows when screen mirroring is active"
            }
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(): Notification {
        val stopIntent = Intent(this, ScreenCaptureService::class.java).apply {
            action = ACTION_STOP
        }
        val stopPendingIntent = PendingIntent.getService(
            this, 0, stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val openIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        val openPendingIntent = PendingIntent.getActivity(
            this, 1, openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("RemotePhone")
            .setContentText("Screen mirroring is active")
            .setSmallIcon(R.drawable.ic_cast)
            .setContentIntent(openPendingIntent)
            .addAction(0, "Stop", stopPendingIntent)
            .setOngoing(true)
            .build()
    }
}
