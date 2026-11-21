package com.remotephone

import android.content.ClipboardManager
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import org.java_websocket.WebSocket
import org.java_websocket.WebSocketImpl
import org.java_websocket.handshake.ClientHandshake
import org.java_websocket.server.WebSocketServer
import org.json.JSONObject
import java.net.InetSocketAddress
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

class MirrorWebSocketServer(
    port: Int,
    private val context: Context,
    private var screenWidth: Int,
    private var screenHeight: Int,
    private val audioAvailable: Boolean,
    private val onControlCommand: (String) -> Unit,
    private val onClientsChanged: () -> Unit,
    private val onApprovalRequest: (id: String, label: String) -> Unit,
    private val onKeyFrameNeeded: () -> Unit,
    private val onBitrate: (Int) -> Unit
) : WebSocketServer(InetSocketAddress(port)) {

    private val mainHandler = Handler(Looper.getMainLooper())

    companion object {
        private const val TAG = "MirrorWSServer"
        private const val APPROVAL_TIMEOUT_MS = 60_000L
        /** WebSocket "policy violation": the phone's owner did not allow this client. */
        const val CLOSE_REJECTED = 1008
        /** Frames queued for one client before it counts as behind; a keyframe still draining is far below this. */
        private const val MAX_QUEUED_FRAMES = 8
        // shortcut: past 4 MB a joiner starts from a requested keyframe, raise it if joining mid-motion feels slow on big-heap phones
        private const val MAX_GOP_BYTES = 4 * 1024 * 1024

        /** The computer's own name, without the prefix the RemotePhone clients put in their hello. */
        fun displayName(name: String) = name.removePrefix("RemotePhone Web on ").removePrefix("RemotePhone on ").removeSuffix(".local")
        fun isBrowser(name: String) = name.startsWith("RemotePhone Web on ")
    }

    /** A computer waiting for the owner's answer. */
    data class PendingRequest(val id: String, val name: String, val address: String)

    /** A computer the owner allowed and that is receiving video, since [since] (epoch millis). */
    data class Watcher(val id: String, val name: String, val address: String, val since: Long)

    // The current group of pictures (SPS/PPS, last keyframe, deltas since) so a new client
    // decodes the latest picture at once instead of waiting for the next keyframe, which a
    // static screen may never produce. It is replayed once the client is allowed, after the
    // info reply, so info is always the first message (the scanner reads only a few). [lock]
    // orders the replay against the live stream: a client joins [ready] only after its replay,
    // so it never sees a delta ahead of its keyframe.
    private val lock = Any()
    private var videoConfigFrame: ByteArray? = null
    private val gop = ArrayList<ByteArray>()  // empty when over MAX_GOP_BYTES, until the next keyframe
    private var gopBytes = 0
    private val ready = HashMap<WebSocket, Watcher>()
    private val stale = HashSet<WebSocket>()  // fell behind or joined without a picture: no video until drained and a keyframe

    // Consent: video and control flow only to computers the owner allowed, for the mirroring
    // session or for good when remembered. Allow hands the computer a random secret inside TLS;
    // on every return the phone sends a nonce and the computer answers with an HMAC over the
    // nonce and the certificate it connected to, so neither a replayed id nor a relay on the
    // network can inherit the access. A computer waiting for the owner is parked in [pending]
    // with its timeout, one proving itself in [awaitingAuth]. All guarded by [lock].
    private class Request(val conn: WebSocket, val name: String, val address: String, val timeout: Runnable)
    private class Auth(val id: String, val name: String, val nonce: String, val secret: String)
    private val approved = HashMap<String, String>()  // id to secret, this session
    private val pending = HashMap<String, Request>()
    private val awaitingAuth = HashMap<WebSocket, Auth>()
    private val random = SecureRandom()

    override fun onOpen(conn: WebSocket, handshake: ClientHandshake) {
        Log.i(TAG, "Client connected: ${conn.remoteSocketAddress}")
    }

    override fun onClose(conn: WebSocket, code: Int, reason: String, remote: Boolean) {
        val (wasReady, wasPending) = synchronized(lock) {
            awaitingAuth.remove(conn)
            val wasPending = pending.entries.firstOrNull { it.value.conn === conn }?.let {
                mainHandler.removeCallbacks(it.value.timeout)
                pending.remove(it.key)
            } != null
            stale.remove(conn)
            Pair(ready.remove(conn) != null, wasPending)
        }
        if (wasReady) RemoteAccessibilityService.liftFinger()  // it may have left mid-press
        if (wasReady || wasPending) onClientsChanged()
        Log.i(TAG, "Client disconnected: ${conn.remoteSocketAddress} (code=$code)")
    }

    fun watchers(): List<Watcher> = synchronized(lock) { ready.values.sortedBy { it.since } }

    /** Ends [id]'s connections with a normal close, which the desktop client does not retry,
     *  and drops its Allow for this session so a reconnect asks again. */
    fun disconnect(id: String) {
        val conns = synchronized(lock) {
            approved.remove(id)
            ready.filterValues { it.id == id }.keys.toList()
        }
        conns.forEach { it.close(1000, "Disconnected on the phone") }
    }

    /** A normal close for everyone, so clients do not retry; [stop] alone closes with 1001. */
    fun closeAll() = connections.forEach { it.close(1000, "Mirroring stopped on the phone") }

    /** Computers waiting for the owner's answer. */
    fun pendingRequests(): List<PendingRequest> = synchronized(lock) {
        pending.map { PendingRequest(it.key, it.value.name, it.value.address) }
    }

    override fun onMessage(conn: WebSocket, message: String) {
        try {
            val json = JSONObject(message)
            val type = json.getString("type")
            if (type == "hello") {
                hello(conn, json)
                return
            }
            if (type == "auth") {
                auth(conn, json.optString("mac"))
                return
            }
            // Nothing else is accepted from a client the phone has not allowed
            if (synchronized(lock) { conn !in ready }) return
            when (type) {
                "request_keyframe" -> onKeyFrameNeeded()
                "toggle_audio" -> {
                    val enabled = json.getBoolean("enabled")
                    ScreenCaptureService.toggleAudio(enabled)
                }
                "copy", "cut" -> {
                    onControlCommand(message)
                    // Mirror the phone clipboard back once the action has landed.
                    // Reading works while the RemotePhone Keyboard is the selected
                    // IME (the Android 10+ background-clipboard exemption).
                    mainHandler.postDelayed({ sendClipboard(conn) }, 250)
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

    private fun hello(conn: WebSocket, json: JSONObject) {
        conn.setAttachment(json.optInt("videoBitrate"))  // applied once the client is allowed
        conn.send(infoJson())
        Log.i(TAG, "Sent device info to client")
        // Scanner probes only identify the phone; they never ask for access
        if (json.optBoolean("probe")) return
        val address = conn.remoteSocketAddress?.address?.hostAddress ?: return
        val id = json.optString("id").ifEmpty { address }
        val name = json.optString("client").take(40)
        if (!roomFor(id)) {
            conn.close(CLOSE_REJECTED, "Another computer is already connected")
            return
        }
        if (!Prefs.ask(context)) {
            grant(conn, id, name, null)  // confirmation switched off: everyone on the network may watch
            return
        }
        val timeout = Runnable { deny(id, "No answer on the phone") }
        val secret = synchronized(lock) {
            val known = approved[id] ?: Prefs.remembered(context)[id]?.secret
            if (known == null) {
                // A reconnect from the same computer replaces its earlier request
                pending.put(id, Request(conn, name, address, timeout))?.let {
                    mainHandler.removeCallbacks(it.timeout)
                    it.conn.close(CLOSE_REJECTED, "Superseded by a newer connection")
                }
            }
            known
        }
        if (secret != null) {
            challenge(conn, id, name, secret)
            return
        }
        conn.send(approvalJson("pending"))
        mainHandler.postDelayed(timeout, APPROVAL_TIMEOUT_MS)
        onApprovalRequest(id, if (name.isBlank()) address else "$name ($address)")
        onClientsChanged()
    }

    /** With several computers disallowed, only the computer already watching may (re)connect. */
    private fun roomFor(id: String): Boolean = Prefs.multiple(context) ||
        synchronized(lock) { ready.values.all { it.id == id } }

    private fun approvalJson(state: String, secret: String? = null): String =
        JSONObject().put("type", "approval").put("state", state).apply { secret?.let { put("secret", it) } }.toString()

    private fun randomHex(): String = ByteArray(32).also { random.nextBytes(it) }.joinToString("") { "%02x".format(it) }

    /** A known computer must prove it holds the secret, bound to the certificate it connected to. */
    private fun challenge(conn: WebSocket, id: String, name: String, secret: String) {
        val nonce = randomHex()
        synchronized(lock) { awaitingAuth[conn] = Auth(id, name, nonce, secret) }
        conn.send(JSONObject().put("type", "challenge").put("nonce", nonce).toString())
    }

    private fun auth(conn: WebSocket, mac: String) {
        val auth = synchronized(lock) { awaitingAuth.remove(conn) } ?: return
        val expected = Mac.getInstance("HmacSHA256").run {
            init(SecretKeySpec(auth.secret.toByteArray(), "HmacSHA256"))
            doFinal("${auth.nonce}|${Tls.fingerprintHex()}".toByteArray())
        }.joinToString("") { "%02x".format(it) }
        if (MessageDigest.isEqual(expected.toByteArray(), mac.lowercase().toByteArray())) {
            grant(conn, auth.id, auth.name, null)
        } else {
            Log.w(TAG, "Client ${auth.id} failed authentication")
            conn.close(CLOSE_REJECTED, "Authentication failed")
        }
    }

    /** Start streaming to an allowed client: the current picture first, then live frames.
     *  [secret] is handed over once, on the Allow that created it. */
    private fun grant(conn: WebSocket, id: String, name: String, secret: String?) {
        val noPicture = synchronized(lock) {
            try {
                conn.send(approvalJson("granted", secret))
                if (audioOn) conn.send(audioJson(true))
                videoConfigFrame?.let { conn.send(it) }
                gop.forEach { conn.send(it) }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to send current picture to new client", e)
            }
            val address = conn.remoteSocketAddress?.address?.hostAddress ?: ""
            ready[conn] = Watcher(id, name, address, System.currentTimeMillis())
            // Without a keyframe to replay, deltas would only break its decoder
            gop.isEmpty().also { if (it) stale.add(conn) }
        }
        if (noPicture) onKeyFrameNeeded()
        conn.getAttachment<Int?>()?.takeIf { it > 0 }?.let(onBitrate)
        onClientsChanged()
    }

    /** The owner allowed [id]: mint its secret, keep it for the session or for good, and start the waiting connection. */
    fun approve(id: String) {
        val secret = randomHex()
        val waiting = synchronized(lock) {
            approved[id] = secret
            pending.remove(id)
        }
        if (Prefs.remember(context)) {
            Prefs.addRemembered(context, id, waiting?.name?.ifBlank { null } ?: waiting?.address ?: id, secret)
        }
        waiting?.let {
            mainHandler.removeCallbacks(it.timeout)
            if (roomFor(id)) grant(it.conn, id, it.name, secret)
            else it.conn.close(CLOSE_REJECTED, "Another computer is already connected")
        }
        onClientsChanged()
        Log.i(TAG, "Client $id allowed")
    }

    fun deny(id: String, reason: String = "Rejected on the phone") {
        val waiting = synchronized(lock) { pending.remove(id) } ?: return
        mainHandler.removeCallbacks(waiting.timeout)
        try { waiting.conn.close(CLOSE_REJECTED, reason) } catch (_: Exception) {}
        onClientsChanged()
        Log.i(TAG, "Client $id denied: $reason")
    }

    /** Device and stream info: the hello reply, and rebroadcast when the screen rotates. */
    private fun infoJson(): String = JSONObject().apply {
        put("type", "info")
        put("device", "${Build.MANUFACTURER} ${Build.MODEL}")
        put("screenWidth", screenWidth)
        put("screenHeight", screenHeight)
        put("audioAvailable", audioAvailable)
        put("liveTouch", Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)  // accepts "touch" down/move/up
        put("androidVersion", Build.VERSION.RELEASE)
    }.toString()

    private fun audioJson(enabled: Boolean): String = JSONObject().put("type", "audio").put("enabled", enabled).toString()

    private var audioOn = false  // guarded by [lock]

    /** Tells every allowed client the phone's audio state, so their playback and button follow it. */
    fun setAudio(enabled: Boolean) {
        val conns = synchronized(lock) { audioOn = enabled; ready.keys.toList() }
        val json = audioJson(enabled)
        conns.forEach { try { it.send(json) } catch (_: Exception) {} }
    }

    fun updateScreenSize(width: Int, height: Int) {
        screenWidth = width
        screenHeight = height
        broadcast(infoJson())
    }

    private fun sendClipboard(conn: WebSocket) {
        // Only the active IME may read the clipboard; when that fails, use the
        // selection the accessibility service captured while performing the copy.
        val text = try {
            val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cm.primaryClip?.getItemAt(0)?.coerceToText(context)?.toString()
        } catch (e: Exception) {
            null
        } ?: RemoteAccessibilityService.lastCopiedText
        try {
            if (!text.isNullOrEmpty() && conn.isOpen) {
                conn.send(JSONObject().put("type", "clipboard").put("content", text).toString())
                Log.i(TAG, "Sent clipboard to client (${text.length} chars)")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Clipboard send failed", e)
        }
    }

    override fun onError(conn: WebSocket?, ex: Exception) {
        Log.e(TAG, "WebSocket error: ${ex.message}", ex)
    }

    override fun onStart() {
        Log.i(TAG, "WebSocket server started on port $port")
        connectionLostTimeout = 0  // Disable connection lost timeout
    }

    /**
     * Broadcast a binary frame (video or audio) to every client that has had its replay,
     * keeping the current group of pictures for the next joiner.
     * A client whose send queue backs up skips deltas, so the encoder thread never blocks. One
     * skipped delta breaks every delta after it, so that client gets no video until its queue has
     * drained, and then resumes at the next keyframe, which is asked for only once it is waiting.
     */
    fun broadcastFrame(frame: ByteArray) {
        if (frame.isEmpty()) return
        val type = frame[0]
        val video = type == ScreenCaptureService.FRAME_VIDEO_KEY || type == ScreenCaptureService.FRAME_VIDEO_DELTA
        var waiting = false
        synchronized(lock) {
            when (type) {
                ScreenCaptureService.FRAME_VIDEO_CONFIG -> { videoConfigFrame = frame; gop.clear() }
                ScreenCaptureService.FRAME_VIDEO_KEY -> { gop.clear(); gop.add(frame); gopBytes = frame.size }
                ScreenCaptureService.FRAME_VIDEO_DELTA -> if (gop.isNotEmpty()) {
                    gop.add(frame)
                    gopBytes += frame.size
                    if (gopBytes > MAX_GOP_BYTES) {
                        gop.clear()
                        Log.i(TAG, "Replay buffer over $MAX_GOP_BYTES bytes, dropped until the next keyframe")
                    }
                }
            }
            for (conn in ready.keys) {
                try {
                    if (!conn.isOpen) continue
                    if (video) {
                        val queued = (conn as WebSocketImpl).outQueue.size
                        if (conn in stale) {
                            if (queued > 1) continue
                            if (type == ScreenCaptureService.FRAME_VIDEO_DELTA) { waiting = true; continue }
                            stale.remove(conn)
                        } else if (type == ScreenCaptureService.FRAME_VIDEO_DELTA && queued > MAX_QUEUED_FRAMES) {
                            stale.add(conn)
                            Log.i(TAG, "Client ${conn.remoteSocketAddress} fell behind, skipping to the next keyframe")
                            continue
                        }
                    }
                    conn.send(frame)
                } catch (e: Exception) {
                    // Client may have disconnected; onClose cleans up
                }
            }
        }
        if (waiting) onKeyFrameNeeded()
    }
}
