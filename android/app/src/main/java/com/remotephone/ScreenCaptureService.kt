package com.remotephone

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.Surface
import androidx.annotation.RequiresApi
import androidx.core.app.NotificationCompat
import org.java_websocket.server.DefaultSSLWebSocketServerFactory
import java.nio.ByteBuffer

class ScreenCaptureService : Service() {

    companion object {
        const val TAG = "ScreenCapture"
        const val NOTIFICATION_ID = 1001
        const val CHANNEL_ID = "remote_phone_capture"
        const val ACTION_START = "com.remotephone.START"
        const val ACTION_STOP = "com.remotephone.STOP"
        const val ACTION_APPROVE = "com.remotephone.APPROVE"
        const val ACTION_DENY = "com.remotephone.DENY"
        const val EXTRA_CLIENT = "client"
        const val REQUEST_CHANNEL_ID = "remote_phone_requests"
        const val EXTRA_RESULT_CODE = "result_code"
        const val EXTRA_RESULT_DATA = "result_data"
        const val WS_PORT = 8765
        const val FRAME_RATE = 60
        private val SIZE_STEPS = floatArrayOf(1f, 2 / 3f, 0.5f)
        /** Average milliseconds the encoder may hold frames before the stream size steps down. */
        private const val MAX_ENCODER_DELAY_MS = 60
        private const val DELAY_WINDOW = 90  // frames averaged per check

        // Frame type markers for the binary protocol
        const val FRAME_VIDEO_CONFIG: Byte = 0x00
        const val FRAME_VIDEO_KEY: Byte = 0x01
        const val FRAME_VIDEO_DELTA: Byte = 0x02
        const val FRAME_AUDIO_CONFIG: Byte = 0x10
        const val FRAME_AUDIO_DATA: Byte = 0x11

        private var instance: ScreenCaptureService? = null

        const val EXTRA_AUDIO = "audio"

        fun toggleAudio(enabled: Boolean) {
            instance?.setAudioEnabled(enabled)
        }

        fun audioEnabled(): Boolean = instance?.takeIf { it.isRunning }?.audioEnabled == true

        /** The computers currently watching, or null when not mirroring. */
        fun watchers(): List<MirrorWebSocketServer.Watcher>? = instance?.takeIf { it.isRunning }?.webSocketServer?.watchers()

        fun disconnect(id: String) {
            instance?.webSocketServer?.disconnect(id)
        }

        /** Computers waiting for the owner's answer; empty when not mirroring. */
        fun pendingRequests(): List<MirrorWebSocketServer.PendingRequest> =
            instance?.takeIf { it.isRunning }?.webSocketServer?.pendingRequests() ?: emptyList()

        fun answer(id: String, allow: Boolean) {
            instance?.answer(id, allow)
        }

        /** Set by the activity while visible; called on the main thread when watchers or requests change. */
        @Volatile var onClientsChanged: ((List<MirrorWebSocketServer.Watcher>?) -> Unit)? = null
    }

    private fun answer(id: String, allow: Boolean) {
        if (allow) webSocketServer?.approve(id) else webSocketServer?.deny(id)
        getSystemService(NotificationManager::class.java).cancel(requestNotificationId(id))
    }

    private val mainHandler = Handler(Looper.getMainLooper())

    // Runs on the main thread whenever the watchers or requests change
    private val publishClients = Runnable {
        if (!isRunning) return@Runnable
        val watchers = webSocketServer?.watchers() ?: emptyList()
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, buildNotification(watchers.map { MirrorWebSocketServer.displayName(it.name).ifBlank { it.address } }))
        onClientsChanged?.invoke(watchers)
    }

    // Video
    private var mediaProjection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    @Volatile private var videoEncoder: MediaCodec? = null
    private var inputSurface: Surface? = null
    private var videoThread: Thread? = null

    // Audio
    private var audioRecord: AudioRecord? = null
    private var audioThread: Thread? = null
    private var audioEnabled = false
    private var audioAvailable = false
    private var savedMusicVolume = 0

    // Network
    private var webSocketServer: MirrorWebSocketServer? = null

    private var isRunning = false
    private var screenWidth = 0
    private var screenHeight = 0
    private var density = 0
    private var bitrate = 0  // asked for by the last client allowed; 0 until one asks
    // The stream is encoded smaller than the screen when the encoder cannot keep up at full size
    // (see readVideoEncoder). It only ever steps down, and starts again at full size next session.
    private var sizeStep = 0
    private val encWidth get() = scaled(screenWidth)
    private val encHeight get() = scaled(screenHeight)
    private fun scaled(pixels: Int) =
        if (sizeStep == 0) pixels else (pixels * SIZE_STEPS[sizeStep]).toInt() and (-2)  // even, like the full size; multiples of 16 would change the shape
    @Volatile private var lastKeyFrameRequest = 0L

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
                    if (intent.getBooleanExtra(EXTRA_AUDIO, false)) setAudioEnabled(true)
                }
            }
            ACTION_STOP -> {
                stopCapture()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
            // Allow / Deny from the connection request notification
            ACTION_APPROVE, ACTION_DENY -> {
                intent.getStringExtra(EXTRA_CLIENT)?.let { answer(it, intent.action == ACTION_APPROVE) }
                if (!isRunning) stopSelf()
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

        val projection = mediaProjection
        projection?.registerCallback(object : MediaProjection.Callback() {
            // Also fires for stopCapture's own projection.stop(), once this session is already over
            override fun onStop() {
                if (!isRunning || mediaProjection !== projection) return
                Log.i(TAG, "Projection stopped by the system")
                stopCapture()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }, null)

        readScreenSize()

        val encoder = createEncoder()
        val surface = encoder.createInputSurface()
        encoder.start()
        videoEncoder = encoder
        inputSurface = surface

        virtualDisplay = createDisplay(surface)

        // Setup audio capture (Android 10+)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            audioAvailable = setupAudioCapture()
        }

        // Start WebSocket server
        webSocketServer = MirrorWebSocketServer(
            port = WS_PORT,
            context = this,
            screenWidth = screenWidth,
            screenHeight = screenHeight,
            audioAvailable = audioAvailable,
            onControlCommand = { RemoteAccessibilityService.handleCommand(it) },
            onClientsChanged = { mainHandler.post(publishClients) },
            onApprovalRequest = { id, label -> requestApproval(id, label) },
            onKeyFrameNeeded = { requestKeyFrame() },
            onBitrate = { setBitrate(it) }
        )
        // Everything, video included, travels inside TLS under the phone's own certificate
        webSocketServer!!.setWebSocketFactory(DefaultSSLWebSocketServerFactory(Tls.serverContext()))
        // A quick stop and start must not fail on the previous socket still closing
        webSocketServer!!.isReuseAddr = true
        // The library leaves Nagle on, which holds the tail of a frame back until the client ACKs
        webSocketServer!!.isTcpNoDelay = true
        webSocketServer!!.start()

        isRunning = true

        // Start reading encoded video frames
        videoThread = Thread({ readVideoEncoder(encoder) }, "VideoEncoderThread").apply { start() }

        Log.i(TAG, "Screen capture started: ${screenWidth}x${screenHeight} on port $WS_PORT")
    }

    /**
     * Real screen size including system bars, even-aligned as some hardware encoders require.
     * Returns true when the size changed since the last read (rotation, fold).
     */
    private fun readScreenSize(): Boolean {
        val oldWidth = screenWidth
        val oldHeight = screenHeight
        val wm = getSystemService(Context.WINDOW_SERVICE) as android.view.WindowManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val bounds = wm.maximumWindowMetrics.bounds
            screenWidth = bounds.width()
            screenHeight = bounds.height()
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
        screenWidth = screenWidth and (-2)
        screenHeight = screenHeight and (-2)
        return screenWidth != oldWidth || screenHeight != oldHeight
    }

    /**
     * H.264 encoder at the current screen size, tuned for low-latency streaming.
     * Hardware encoders reject keys they do not support with IllegalArgumentException (low
     * latency on older Qualcomm ones, profile/level on some custom ROMs), so each attempt drops
     * low latency, profile/level or both, keeping the other tuning; the bare format comes last.
     */
    private fun createEncoder(): MediaCodec {
        val encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
        for (profile in listOf(true, false)) for (lowLatency in listOf(true, false)) {
            try {
                encoder.configure(tunedVideoFormat(encoder, lowLatency, profile), null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
                return encoder
            } catch (e: IllegalArgumentException) {
                Log.w(TAG, "Encoder rejected the tuned format (low latency $lowLatency, profile $profile), retrying with less")
                encoder.reset()
            }
        }
        encoder.configure(baseVideoFormat(), null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        return encoder
    }

    private fun tunedVideoFormat(encoder: MediaCodec, lowLatency: Boolean, profile: Boolean): MediaFormat =
        baseVideoFormat().apply {
            // Reduce encoder output buffering for lower latency
            if (lowLatency && Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
            }
            // A 90 or 120 Hz display would feed more frames than the bitrate is budgeted for
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                setFloat(MediaFormat.KEY_MAX_FPS_TO_ENCODER, FRAME_RATE.toFloat())
            }
            // When screen is static, only repeat frame every 100ms
            setLong(MediaFormat.KEY_REPEAT_PREVIOUS_FRAME_AFTER, 100_000) // microseconds
            // Main profile: better compression than Baseline, no B-frames so still low latency.
            // A profile must come with a level, and a fixed one is too low for tall screens at
            // 60 fps, so take the highest level this encoder offers.
            if (profile) encoder.codecInfo.getCapabilitiesForType(MediaFormat.MIMETYPE_VIDEO_AVC).profileLevels
                .filter { it.profile == MediaCodecInfo.CodecProfileLevel.AVCProfileMain }
                .maxOfOrNull { it.level }
                ?.let {
                    setInteger(MediaFormat.KEY_PROFILE, MediaCodecInfo.CodecProfileLevel.AVCProfileMain)
                    setInteger(MediaFormat.KEY_LEVEL, it)
                }
        }

    // Services receive the device configuration, so this fires when another app rotates the
    // screen even though MainActivity is portrait-locked. Locale, font and theme changes land
    // here too, and a 180 degree flip keeps the size, so only a real size change restarts video.
    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        if (isRunning && readScreenSize()) restartVideo()
    }

    private fun createDisplay(surface: Surface): VirtualDisplay = mediaProjection!!.createVirtualDisplay(
        "RemotePhone",
        encWidth, encHeight, density,
        DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
        surface,
        null, null
    )

    /**
     * Swap in an encoder at the new screen size. Android 14 allows a single createVirtualDisplay
     * per MediaProjection, so it resizes; Android 13 does not rescale a resized display, so it recreates.
     */
    private fun restartVideo() {
        try {
            val old = videoEncoder
            val oldSurface = inputSurface
            val oldThread = videoThread
            val encoder = createEncoder()
            val surface = encoder.createInputSurface()
            encoder.start()
            videoEncoder = encoder  // the old reader loop exits on this swap
            inputSurface = surface
            oldThread?.join(500)
            // Resize before attaching, so the new surface only ever sees the new projection
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                virtualDisplay?.resize(encWidth, encHeight, density)
                virtualDisplay?.setSurface(surface)
            } else {
                virtualDisplay?.release()
                virtualDisplay = createDisplay(surface)
            }
            try {
                old?.stop()
                old?.release()
                oldSurface?.release()
            } catch (e: Exception) {
                Log.e(TAG, "Error releasing old encoder", e)
            }
            // Clients learn the new size before the first frame at that size arrives
            webSocketServer?.updateScreenSize(encWidth, encHeight)
            // Clients send positions on the picture they see; the phone works in screen pixels
            RemoteAccessibilityService.inputScaleX = screenWidth.toFloat() / encWidth
            RemoteAccessibilityService.inputScaleY = screenHeight.toFloat() / encHeight
            videoThread = Thread({ readVideoEncoder(encoder) }, "VideoEncoderThread").apply { start() }
            Log.i(TAG, "Video restarted at ${encWidth}x${encHeight} for a ${screenWidth}x${screenHeight} screen")
        } catch (e: Exception) {
            Log.e(TAG, "Video restart after rotation failed", e)
        }
    }

    /** The minimal H.264 format every encoder accepts; optional low-latency keys are layered on top. */
    private fun baseVideoFormat(): MediaFormat =
        MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, encWidth, encHeight).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
            setInteger(MediaFormat.KEY_BIT_RATE, if (bitrate > 0) bitrate else calculateBitrate(screenWidth, screenHeight))
            setInteger(MediaFormat.KEY_FRAME_RATE, FRAME_RATE)
            // A keyframe is many times the size of a normal frame and stalls the stream while it
            // is sent, so they are rare; a client that needs one sooner asks (requestKeyFrame)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 10)
        }

    /** Ask the encoder for a keyframe now; at most twice a second however many clients ask. */
    private fun requestKeyFrame() {
        val now = SystemClock.elapsedRealtime()
        if (now - lastKeyFrameRequest < 500) return
        lastKeyFrameRequest = now
        setEncoderParameter(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0)
    }

    /** The bitrate a client asked for, kept across rotation. With several clients the last one wins. */
    private fun setBitrate(bitsPerSecond: Int) {
        bitrate = bitsPerSecond.coerceIn(1_000_000, 40_000_000)
        setEncoderParameter(MediaCodec.PARAMETER_KEY_VIDEO_BITRATE, bitrate)
    }

    private fun setEncoderParameter(key: String, value: Int) {
        try {
            videoEncoder?.setParameters(Bundle().apply { putInt(key, value) })
        } catch (e: IllegalStateException) {
            // Mid-rotation or stopping: the next encoder starts with a keyframe and the stored bitrate
        }
    }

    private fun calculateBitrate(width: Int, height: Int): Int {
        // Adaptive bitrate: ~8 bits per pixel for high quality over WiFi
        return (width * height * 8).coerceIn(4_000_000, 40_000_000)
    }

    private fun readVideoEncoder(encoder: MediaCodec) {
        val bufferInfo = MediaCodec.BufferInfo()
        var delaySum = 0L
        var delayCount = 0
        var slowWindows = 0

        // Exits when capture stops or a rotation swaps in a new encoder
        while (isRunning && videoEncoder === encoder) {
            try {
                val index = encoder.dequeueOutputBuffer(bufferInfo, 1_000)  // 1ms timeout for low latency
                if (index >= 0) {
                    val buffer = encoder.getOutputBuffer(index) ?: continue

                    if (bufferInfo.size > 0) {
                        buffer.position(bufferInfo.offset)
                        val data = ByteArray(bufferInfo.size)
                        buffer.get(data, 0, bufferInfo.size)

                        val timestamp = (bufferInfo.presentationTimeUs / 1000).toInt()

                        // A frame's timestamp is when it was drawn, on the same clock as nanoTime, so
                        // the difference is how long the encoder held it. An encoder that cannot
                        // keep up at this size queues frames (over 100 ms on older phones, where a
                        // smaller picture brings it back near 30 ms), so two slow windows in a row
                        // step the stream size down. Absurd values mean another clock: ignored.
                        val delay = System.nanoTime() / 1_000_000 - bufferInfo.presentationTimeUs / 1000
                        if (bufferInfo.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG == 0 && delay in 0..1000) {
                            delaySum += delay
                            if (++delayCount == DELAY_WINDOW) {
                                slowWindows = if (delaySum / DELAY_WINDOW > MAX_ENCODER_DELAY_MS) slowWindows + 1 else 0
                                if (slowWindows == 2 && sizeStep < SIZE_STEPS.size - 1) {
                                    Log.i(TAG, "Encoder holds frames ${delaySum / DELAY_WINDOW} ms at ${encWidth}x${encHeight}: making the stream smaller")
                                    mainHandler.post { if (isRunning && videoEncoder === encoder) { sizeStep++; restartVideo() } }
                                }
                                delaySum = 0; delayCount = 0
                            }
                        }

                        val frameType = when {
                            bufferInfo.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0 -> FRAME_VIDEO_CONFIG
                            bufferInfo.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME != 0 -> FRAME_VIDEO_KEY
                            else -> FRAME_VIDEO_DELTA
                        }

                        sendFrame(frameType, timestamp, data)
                    }

                    encoder.releaseOutputBuffer(index, false)
                }
            } catch (e: Exception) {
                if (isRunning && videoEncoder === encoder) Log.e(TAG, "Video encoder read error", e)
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

    // From the phone's switch on the main thread and from a client's toggle_audio on the server's
    @Synchronized
    fun setAudioEnabled(enabled: Boolean) {
        if (!audioAvailable || (enabled && !isRunning)) return
        val was = audioEnabled

        val audioManager = getSystemService(Context.AUDIO_SERVICE) as AudioManager

        if (enabled && !audioEnabled) {
            audioEnabled = true
            // Mute phone speakers so audio only plays on the Linux client
            savedMusicVolume = audioManager.getStreamVolume(AudioManager.STREAM_MUSIC)
            audioManager.setStreamVolume(AudioManager.STREAM_MUSIC, 0, 0)
            audioRecord?.startRecording()
            audioThread = Thread({ readAudioCapture() }, "AudioThread").apply { start() }
            Log.i(TAG, "Audio streaming enabled, phone speakers muted")
        } else if (!enabled && audioEnabled) {
            audioEnabled = false
            try { audioRecord?.stop() } catch (_: Exception) {}
            // Restore phone speaker volume
            if (savedMusicVolume > 0) {
                audioManager.setStreamVolume(AudioManager.STREAM_MUSIC, savedMusicVolume, 0)
            }
            Log.i(TAG, "Audio streaming disabled, phone speakers restored")
        }
        if (audioEnabled != was) {
            webSocketServer?.setAudio(audioEnabled)
            mainHandler.post(publishClients)  // the phone's switch follows
        }
    }

    private fun readAudioCapture() {
        val buffer = ByteArray(16384) // ~93ms at 44100Hz stereo 16-bit

        while (audioEnabled && isRunning) {
            try {
                val bytesRead = audioRecord?.read(buffer, 0, buffer.size) ?: -1
                if (bytesRead > 0) {
                    val timestamp = (System.nanoTime() / 1_000_000).toInt()
                    sendFrame(FRAME_AUDIO_DATA, timestamp, buffer.copyOf(bytesRead))
                }
            } catch (e: Exception) {
                if (audioEnabled) Log.e(TAG, "Audio capture error", e)
            }
        }
    }

    private fun sendFrame(type: Byte, timestamp: Int, data: ByteArray) {
        // 9-byte big-endian header: [type(1)] [timestamp(4)] [size(4)], then the payload
        val frame = ByteBuffer.allocate(9 + data.size)
            .put(type).putInt(timestamp).putInt(data.size).put(data)
            .array()
        webSocketServer?.broadcastFrame(frame)
    }

    private fun stopCapture() {
        if (!isRunning) return
        isRunning = false
        mainHandler.post { onClientsChanged?.invoke(null) }

        // Restore phone volume if audio was streaming
        try { setAudioEnabled(false) } catch (_: Exception) {}

        // Stop threads
        videoThread?.interrupt()
        audioThread?.interrupt()

        // Release video encoder
        try {
            virtualDisplay?.release()
            videoEncoder?.stop()
            videoEncoder?.release()
            inputSurface?.release()
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
                    server.closeAll()
                    server.stop(1000)
                } catch (e: Exception) {
                    Log.e(TAG, "Error stopping WebSocket server", e)
                }
            }.start()
        }

        sizeStep = 0
        RemoteAccessibilityService.inputScaleX = 1f
        RemoteAccessibilityService.inputScaleY = 1f
        virtualDisplay = null
        videoEncoder = null
        inputSurface = null
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
            // High importance so the request pops up over whatever the phone is showing
            manager.createNotificationChannel(NotificationChannel(
                REQUEST_CHANNEL_ID, "Connection requests", NotificationManager.IMPORTANCE_HIGH
            ).apply { description = "Asks before a computer may view and control this phone" })
        }
    }

    private fun requestNotificationId(id: String) = 2000 + (id.hashCode() and 0xFFFF)

    /** Ask the owner whether the computer [label] may watch and control the phone. Unanswered, the server denies after a minute. */
    private fun requestApproval(id: String, label: String) {
        fun answer(action: String) = PendingIntent.getService(
            this, id.hashCode(),
            Intent(this, ScreenCaptureService::class.java).setAction(action).putExtra(EXTRA_CLIENT, id),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val notification = NotificationCompat.Builder(this, REQUEST_CHANNEL_ID)
            .setContentTitle("Connection request")
            .setContentText("Allow $label to view and control this phone?")
            .setStyle(NotificationCompat.BigTextStyle())
            .setSmallIcon(R.drawable.ic_cast)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setTimeoutAfter(60_000)
            .addAction(0, "Allow", answer(ACTION_APPROVE))
            .addAction(0, "Deny", answer(ACTION_DENY))
            .build()
        getSystemService(NotificationManager::class.java).notify(requestNotificationId(id), notification)
        Log.i(TAG, "Asking to allow $label")
    }

    private fun buildNotification(clients: List<String> = emptyList()): Notification {
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
            .setContentText(
                if (clients.isEmpty()) "Mirroring, no client connected"
                else "Mirroring to ${clients.joinToString(", ")}"
            )
            .setSmallIcon(R.drawable.ic_cast)
            .setContentIntent(openPendingIntent)
            .addAction(0, "Stop", stopPendingIntent)
            .setOngoing(true)
            .build()
    }
}
