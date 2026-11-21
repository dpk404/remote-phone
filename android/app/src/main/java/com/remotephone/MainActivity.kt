package com.remotephone

import android.app.Activity
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.text.Html
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.Switch
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import java.net.Inet4Address
import java.net.NetworkInterface

class MainActivity : ComponentActivity() {

    private lateinit var ipText: TextView
    private lateinit var addressText: TextView
    private lateinit var statusDot: View
    private lateinit var statusText: TextView
    private lateinit var heroTitle: TextView
    private lateinit var heroBody: TextView
    private lateinit var watchersList: LinearLayout
    private lateinit var severalCaption: TextView
    private lateinit var startButton: Button
    private lateinit var audioToggle: Switch
    private lateinit var audioSubtext: TextView
    private lateinit var accessibilityStatus: TextView
    private lateinit var setupCaption: TextView
    private lateinit var keyboardStatus: TextView
    private lateinit var requestsCard: View

    private var isStreaming = false
    private var setupDone = true
    // The service starts asynchronously, so the first onResume after consent cannot see it yet
    private var starting = false

    // Redraws the watchers so their "time connected" keeps up while the screen is open
    private val tick = Runnable { ScreenCaptureService.watchers()?.let(::showStreaming) }

    private val projectionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK && result.data != null) {
            startScreenCapture(result.resultCode, result.data!!)
        } else {
            statusText.text = "Permission denied"
            statusText.setTextColor(getColor(R.color.red_text))
            statusDot.backgroundTintList = getColorStateList(R.color.status_red)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        ipText = findViewById(R.id.ipText)
        addressText = findViewById(R.id.addressText)
        statusDot = findViewById(R.id.statusDot)
        statusText = findViewById(R.id.statusText)
        heroTitle = findViewById(R.id.heroTitle)
        heroBody = findViewById(R.id.heroBody)
        watchersList = findViewById(R.id.watchersList)
        severalCaption = findViewById(R.id.severalCaption)
        startButton = findViewById(R.id.startButton)
        audioToggle = findViewById(R.id.audioToggle)
        audioSubtext = findViewById(R.id.audioSubtext)
        accessibilityStatus = findViewById(R.id.accessibilityStatus)
        setupCaption = findViewById(R.id.setupCaption)
        requestsCard = findViewById(R.id.requestsCard)

        findViewById<TextView>(R.id.portText).text =
            "Port ${ScreenCaptureService.WS_PORT} \u00B7 type it on the computer, or let the desktop client find it"

        startButton.setOnClickListener {
            if (!isStreaming) requestScreenCapture() else stopScreenCapture()
        }

        // On while idle means "start with audio"; while mirroring it shows the real state
        audioToggle.setOnCheckedChangeListener { _, isChecked ->
            ScreenCaptureService.toggleAudio(isChecked)
            if (isStreaming) audioToggle.isChecked = ScreenCaptureService.audioEnabled()
        }
        // Audio only available on Android 10+
        audioToggle.isEnabled = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q

        findViewById<View>(R.id.accessibilityRow).setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }

        val openSettings = View.OnClickListener { startActivity(Intent(this, SettingsActivity::class.java)) }
        findViewById<View>(R.id.settingsButton).setOnClickListener(openSettings)
        severalCaption.setOnClickListener(openSettings)
        severalCaption.text = Html.fromHtml(
            "Several computers at once is on. Each one is watching the same screen. " +
                "<b><font color=\"#A78BFA\">Change in Settings</font></b>", Html.FROM_HTML_MODE_LEGACY
        )

        // Android 13+ types through the accessibility service's own input
        // connection, so the extra keyboard (and its row) is only wired on older versions
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            findViewById<View>(R.id.keyboardCard).visibility = View.GONE
        } else {
            keyboardStatus = findViewById(R.id.keyboardStatus)
            findViewById<View>(R.id.keyboardCard).setOnClickListener {
                if (isRemoteKeyboardEnabled()) {
                    (getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager).showInputMethodPicker()
                } else {
                    startActivity(Intent(Settings.ACTION_INPUT_METHOD_SETTINGS))
                }
            }
        }

        // Request notification permission on Android 13+
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 100)
        }
    }

    override fun onResume() {
        super.onResume()
        updateSetupStatus()
        RemoteAccessibilityService.onStateChanged = ::updateSetupStatus
        val ip = getDeviceIpAddress()
        ipText.text = ip
        addressText.text = Html.fromHtml("$ip<font color=\"#9CA3AF\">:${ScreenCaptureService.WS_PORT}</font>", Html.FROM_HTML_MODE_LEGACY)
        ScreenCaptureService.onClientsChanged = ::showState
        ScreenCaptureService.watchers().let { if (it != null || !starting) showState(it) }
    }

    override fun onPause() {
        starting = false
        ScreenCaptureService.onClientsChanged = null
        RemoteAccessibilityService.onStateChanged = null
        watchersList.removeCallbacks(tick)
        super.onPause()
    }

    /** Null watchers means not mirroring. */
    private fun showState(watchers: List<MirrorWebSocketServer.Watcher>?) {
        watchers?.let(::showStreaming) ?: showIdle()
    }

    /** Streaming state with who is watching, so a silent viewer on the network is never invisible. */
    private fun showStreaming(watchers: List<MirrorWebSocketServer.Watcher>) {
        isStreaming = true
        val n = watchers.size
        statusText.text = "Mirroring"
        statusText.setTextColor(getColor(if (n == 0) R.color.amber_text else R.color.green_text))
        statusDot.backgroundTintList = getColorStateList(if (n == 0) R.color.status_amber else R.color.status_green)
        heroTitle.text = when (n) {
            0 -> "Waiting for a computer"
            1 -> "1 computer watching"
            else -> "$n computers watching"
        }
        heroBody.text = "Connect from your computer with the address below."
        heroBody.visibility = if (n == 0) View.VISIBLE else View.GONE

        watchersList.removeAllViews()
        for (w in watchers) {
            watchersList.addView(computerRow(
                this, watchersList, w.name, true,
                "${w.address} \u00B7 ${elapsed(w.since)}", "Disconnect"
            ) { ScreenCaptureService.disconnect(w.id) }.apply {
                findViewById<TextView>(R.id.detail).typeface = Typeface.MONOSPACE
            })
        }
        watchersList.visibility = if (n == 0) View.GONE else View.VISIBLE
        severalCaption.visibility = if (n > 1) View.VISIBLE else View.GONE

        startButton.text = if (n > 1) "Stop mirroring for everyone" else "Stop mirroring"
        startButton.setBackgroundResource(R.drawable.button_stop)
        startButton.setTextColor(getColor(R.color.red_text))
        findViewById<View>(R.id.connectCard).visibility = if (n == 0) View.VISIBLE else View.GONE
        findViewById<View>(R.id.addressCard).visibility = if (n == 0) View.GONE else View.VISIBLE
        findViewById<View>(R.id.setupCard).visibility = if (setupDone) View.GONE else View.VISIBLE
        audioToggle.isChecked = ScreenCaptureService.audioEnabled()
        showAudioSubtext()
        showRequest()

        watchersList.removeCallbacks(tick)
        if (n > 0) watchersList.postDelayed(tick, 30_000)
    }

    private fun showIdle() {
        if (isStreaming) audioToggle.isChecked = false  // audio ended with the session
        isStreaming = false
        statusText.text = "Not mirroring"
        statusText.setTextColor(getColor(R.color.text_muted))
        statusDot.backgroundTintList = getColorStateList(R.color.dot_idle)
        heroTitle.text = "Ready when you are"
        heroBody.text = "Start, then connect from your computer on the same Wi-Fi."
        heroBody.visibility = View.VISIBLE
        watchersList.visibility = View.GONE
        severalCaption.visibility = View.GONE
        startButton.text = "Start mirroring"
        startButton.setBackgroundResource(R.drawable.button_primary)
        startButton.setTextColor(0xFFFFFFFF.toInt())
        findViewById<View>(R.id.connectCard).visibility = View.VISIBLE
        findViewById<View>(R.id.addressCard).visibility = View.GONE
        findViewById<View>(R.id.setupCard).visibility = View.VISIBLE
        requestsCard.visibility = View.GONE
        showAudioSubtext()
    }

    private fun showAudioSubtext() {
        audioSubtext.text = when {
            Build.VERSION.SDK_INT < Build.VERSION_CODES.Q -> "Requires Android 10+"
            isStreaming -> "Phone speaker is quiet while on"
            else -> "Phone speaker goes quiet while on"
        }
    }

    private fun elapsed(since: Long): String {
        val min = (System.currentTimeMillis() - since) / 60_000
        return when {
            min < 1 -> "just now"
            min < 60 -> "$min min"
            else -> "${min / 60} h ${min % 60} min"
        }
    }

    /** The oldest computer waiting for an answer, so a request never depends on the notification. */
    private fun showRequest() {
        val request = ScreenCaptureService.pendingRequests().firstOrNull()
        requestsCard.visibility = if (request == null) View.GONE else View.VISIBLE
        if (request == null) return
        val name = MirrorWebSocketServer.displayName(request.name).ifBlank { request.address }
        findViewById<TextView>(R.id.requestTitle).text = "$name wants to connect"
        findViewById<TextView>(R.id.requestAddress).apply {
            text = request.address
            visibility = if (name == request.address) View.GONE else View.VISIBLE
        }
        findViewById<View>(R.id.allowButton).setOnClickListener { ScreenCaptureService.answer(request.id, true) }
        findViewById<View>(R.id.denyButton).setOnClickListener { ScreenCaptureService.answer(request.id, false) }
    }

    private fun requestScreenCapture() {
        val projectionManager = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        projectionLauncher.launch(projectionManager.createScreenCaptureIntent())
    }

    private fun startScreenCapture(resultCode: Int, data: Intent) {
        val intent = Intent(this, ScreenCaptureService::class.java).apply {
            action = ScreenCaptureService.ACTION_START
            putExtra(ScreenCaptureService.EXTRA_RESULT_CODE, resultCode)
            putExtra(ScreenCaptureService.EXTRA_RESULT_DATA, data)
            putExtra(ScreenCaptureService.EXTRA_AUDIO, audioToggle.isChecked)
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }

        starting = true
        showStreaming(emptyList())
    }

    private fun stopScreenCapture() {
        val intent = Intent(this, ScreenCaptureService::class.java).apply {
            action = ScreenCaptureService.ACTION_STOP
        }
        startService(intent)
        watchersList.removeCallbacks(tick)
        showIdle()
    }

    /** Green "On" with a check, or amber with what to do, on a setup row's tile and chip. */
    private fun showSetupState(icon: Int, chip: Int, ok: Boolean, label: String) {
        val soft = getColorStateList(if (ok) R.color.green_soft else R.color.amber_soft)
        val fg = getColorStateList(if (ok) R.color.green_text else R.color.amber_text)
        findViewById<ImageView>(icon).apply { backgroundTintList = soft; imageTintList = fg }
        findViewById<TextView>(chip).apply {
            backgroundTintList = soft
            setTextColor(fg)
            compoundDrawableTintList = fg
            setCompoundDrawablesRelativeWithIntrinsicBounds(if (ok) R.drawable.ic_check else 0, 0, 0, 0)
            text = label
        }
    }

    private fun updateSetupStatus() {
        updateAccessibilityStatus()
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) updateKeyboardStatus()
        if (isStreaming) findViewById<View>(R.id.setupCard).visibility = if (setupDone) View.GONE else View.VISIBLE
    }

    private fun updateAccessibilityStatus() {
        val on = RemoteAccessibilityService.isRunning()
        setupDone = on
        showSetupState(R.id.accessibilityIcon, R.id.accessibilityChip, on, if (on) "On" else "Set up")
        accessibilityStatus.text =
            if (on) "Accessibility service \u00B7 tap to open Android settings"
            else "Accessibility service off \u00B7 tap to switch it on"
        setupCaption.text = "Android only lets you switch these on in its own settings, so each row takes you there." +
            if (!on && Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU)
                " On Android 13 and newer, first go to Settings \u2192 Apps \u2192 RemotePhone \u2192 \u22EE menu \u2192 Allow restricted settings."
            else ""
    }

    private fun isRemoteKeyboardEnabled(): Boolean {
        // Settings.Secure.ENABLED_INPUT_METHODS is not readable from targetSdk 34;
        // InputMethodManager is the supported way to list enabled keyboards.
        val imm = getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager
        return imm.enabledInputMethodList.any { it.packageName == packageName }
    }

    private fun updateKeyboardStatus() {
        val selected = Settings.Secure.getString(contentResolver, Settings.Secure.DEFAULT_INPUT_METHOD)
            ?.startsWith(packageName) == true
        val enabled = isRemoteKeyboardEnabled()
        if (!selected) setupDone = false
        showSetupState(R.id.keyboardIcon, R.id.keyboardChip, selected, when {
            selected -> "On"
            enabled -> "Select"
            else -> "Set up"
        })
        keyboardStatus.text = when {
            selected -> "Keyboard selected \u00B7 tap to switch keyboards"
            enabled -> "Keyboard enabled, not selected \u00B7 tap to choose it"
            else -> "Keyboard not enabled \u00B7 tap to switch it on"
        }
    }

    private fun getDeviceIpAddress(): String {
        try {
            val interfaces = NetworkInterface.getNetworkInterfaces()
            while (interfaces.hasMoreElements()) {
                val iface = interfaces.nextElement()
                if (iface.isLoopback || !iface.isUp) continue
                val addresses = iface.inetAddresses
                while (addresses.hasMoreElements()) {
                    val addr = addresses.nextElement()
                    if (addr is Inet4Address && !addr.isLoopbackAddress) {
                        return addr.hostAddress ?: "Unknown"
                    }
                }
            }
        } catch (_: Exception) {
        }
        return "Not connected to WiFi"
    }
}
