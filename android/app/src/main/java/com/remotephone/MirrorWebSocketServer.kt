package com.remotephone

import android.os.Build
import android.util.Log
import org.java_websocket.WebSocket
import org.java_websocket.handshake.ClientHandshake
import org.java_websocket.server.WebSocketServer
import org.json.JSONObject
import java.net.InetSocketAddress
import java.nio.ByteBuffer

class MirrorWebSocketServer(
    port: Int,
    private val screenWidth: Int,
    private val screenHeight: Int,
    private val audioAvailable: Boolean,
    private val onControlCommand: (String) -> Unit
) : WebSocketServer(InetSocketAddress(port)) {

    companion object {
        private const val TAG = "MirrorWSServer"
    }

    // Cache the latest SPS/PPS config frame so new clients can initialize their decoder
    private var videoConfigFrame: ByteArray? = null

    override fun onOpen(conn: WebSocket, handshake: ClientHandshake) {
        Log.i(TAG, "Client connected: ${conn.remoteSocketAddress}")

        // Send cached video config to new client so they can start decoding immediately
        videoConfigFrame?.let { config ->
            try {
                conn.send(config)
            } catch (e: Exception) {
                Log.e(TAG, "Failed to send config to new client", e)
            }
        }
    }

    override fun onClose(conn: WebSocket, code: Int, reason: String, remote: Boolean) {
        Log.i(TAG, "Client disconnected: ${conn.remoteSocketAddress} (code=$code)")
    }

    override fun onMessage(conn: WebSocket, message: String) {
        try {
            val json = JSONObject(message)
            when (json.getString("type")) {
                "hello" -> {
                    // Respond with device and stream info
                    val info = JSONObject().apply {
                        put("type", "info")
                        put("device", "${Build.MANUFACTURER} ${Build.MODEL}")
                        put("screenWidth", screenWidth)
                        put("screenHeight", screenHeight)
                        put("streamWidth", screenWidth)
                        put("streamHeight", screenHeight)
                        put("audioAvailable", audioAvailable)
                        put("androidVersion", Build.VERSION.RELEASE)
                    }
                    conn.send(info.toString())
                    Log.i(TAG, "Sent device info to client")
                }
                "toggle_audio" -> {
                    val enabled = json.getBoolean("enabled")
                    ScreenCaptureService.toggleAudio(enabled)
                }
                else -> {
                    // Forward all other messages as control commands
                    onControlCommand(message)
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error processing message: $message", e)
        }
    }

    override fun onMessage(conn: WebSocket, message: ByteBuffer) {
        // Not expected from client side
    }

    override fun onError(conn: WebSocket?, ex: Exception) {
        Log.e(TAG, "WebSocket error: ${ex.message}", ex)
    }

    override fun onStart() {
        Log.i(TAG, "WebSocket server started on port $port")
        connectionLostTimeout = 0  // Disable connection lost timeout
    }

    /**
     * Broadcast a binary frame (video or audio) to all connected clients.
     * Caches video config frames (SPS/PPS) for late-joining clients.
     * Skips slow clients (send queue > 3 frames) to avoid blocking the encoder thread.
     */
    fun broadcastFrame(frame: ByteArray) {
        // Cache video config (SPS/PPS) frames
        if (frame.isNotEmpty() && frame[0] == ScreenCaptureService.FRAME_VIDEO_CONFIG) {
            videoConfigFrame = frame.copyOf()
        }

        // Send to all connected clients with backpressure
        val clients = connections ?: return
        for (conn in clients) {
            try {
                if (conn.isOpen) {
                    // Skip delta frames for slow clients to prevent encoder thread blocking
                    if (conn.hasBufferedData() && frame.isNotEmpty() &&
                        frame[0] == ScreenCaptureService.FRAME_VIDEO_DELTA) {
                        continue // drop delta frame for this slow client
                    }
                    conn.send(frame)
                }
            } catch (e: Exception) {
                // Client may have disconnected — will be cleaned up by onClose
            }
        }
    }
}
