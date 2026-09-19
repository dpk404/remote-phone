package com.remotephone

import android.app.Activity
import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.Switch
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import java.net.Inet4Address
import java.net.NetworkInterface

class MainActivity : ComponentActivity() {

    private lateinit var ipText: TextView
    private lateinit var portText: TextView
    private lateinit var statusDot: TextView
    private lateinit var statusText: TextView
    private lateinit var startButton: Button
    private lateinit var audioToggle: Switch
    private lateinit var audioSubtext: TextView
    private lateinit var accessibilityStatus: TextView
    private lateinit var accessibilityButton: Button
    private lateinit var keyboardStatus: TextView
    private lateinit var keyboardButton: Button
    private lateinit var requestsCard: View
    private lateinit var requestsContainer: LinearLayout

    private var isStreaming = false

    private val projectionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK && result.data != null) {
            startScreenCapture(result.resultCode, result.data!!)
        } else {
            statusText.text = "Permission denied"
            statusDot.setTextColor(getColor(R.color.status_red))
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Bind views
        ipText = findViewById(R.id.ipText)
        portText = findViewById(R.id.portText)
        statusDot = findViewById(R.id.statusDot)
        statusText = findViewById(R.id.statusText)
        startButton = findViewById(R.id.startButton)
        audioToggle = findViewById(R.id.audioToggle)
        audioSubtext = findViewById(R.id.audioSubtext)
        accessibilityStatus = findViewById(R.id.accessibilityStatus)
        accessibilityButton = findViewById(R.id.accessibilityButton)
        requestsCard = findViewById(R.id.requestsCard)
        requestsContainer = findViewById(R.id.requestsContainer)

        // Show device IP
        ipText.text = getDeviceIpAddress()
        portText.text = "${ScreenCaptureService.WS_PORT}"

        // Start/Stop button
        startButton.setOnClickListener {
            if (!isStreaming) {
                requestScreenCapture()
            } else {
                stopScreenCapture()
            }
        }

        // Audio toggle
        audioToggle.setOnCheckedChangeListener { _, isChecked ->
            ScreenCaptureService.toggleAudio(isChecked)
        }

        // Audio only available on Android 10+
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            audioToggle.isEnabled = true
            audioSubtext.text = "Streams phone audio to PC"
        } else {
            audioToggle.isEnabled = false
            audioSubtext.text = "Requires Android 10+"
        }

        // Accessibility settings button
        accessibilityButton.setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }

        findViewById<Button>(R.id.settingsButton).setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        // Android 13+ types through the accessibility service's own input
        // connection, so the extra keyboard (and its card) is only wired on older versions
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            findViewById<android.view.View>(R.id.keyboardCard).visibility = android.view.View.GONE
        } else {
            keyboardStatus = findViewById(R.id.keyboardStatus)
            keyboardButton = findViewById(R.id.keyboardButton)
            keyboardButton.setOnClickListener {
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
        updateAccessibilityStatus()
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) updateKeyboardStatus()
        ipText.text = getDeviceIpAddress()
        ScreenCaptureService.onClientsChanged = ::showStreaming
        ScreenCaptureService.clients()?.let(::showStreaming)
    }

    override fun onPause() {
        ScreenCaptureService.onClientsChanged = null
        super.onPause()
    }

    /** Streaming state with who is watching, so a silent viewer on the network is never invisible. */
    private fun showStreaming(clients: List<String>) {
        isStreaming = true
        startButton.text = "Stop Mirroring"
        statusDot.text = "\u25CF"
        if (clients.isEmpty()) {
            statusText.text = "Streaming, waiting for a client"
            statusDot.setTextColor(getColor(R.color.status_amber))
        } else {
            statusText.text = "Streaming to ${clients.joinToString(", ")}"
            statusDot.setTextColor(getColor(R.color.status_green))
        }
        showRequests(ScreenCaptureService.pendingRequests())
    }

    /** Computers waiting for an answer, with Allow and Deny, so a request never depends on the notification. */
    private fun showRequests(requests: List<MirrorWebSocketServer.PendingRequest>) {
        requestsCard.visibility = if (requests.isEmpty()) View.GONE else View.VISIBLE
        requestsContainer.removeAllViews()
        for ((id, name, address) in requests) {
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }
            row.addView(TextView(this).apply {
                text = if (name.isBlank()) address else "$name\n$address"
                textSize = 13f
                setTextColor(getColor(R.color.text_secondary))
            }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            row.addView(answerButton("Allow", R.color.status_green) { ScreenCaptureService.answer(id, true) })
            row.addView(answerButton("Deny", R.color.status_red) { ScreenCaptureService.answer(id, false) })
            requestsContainer.addView(row)
        }
    }

    private fun answerButton(label: String, color: Int, onClick: () -> Unit) =
        Button(this, null, android.R.attr.borderlessButtonStyle).apply {
            text = label
            setTextColor(getColor(color))
            setOnClickListener { onClick() }
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
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }

        showStreaming(emptyList())
    }

    private fun stopScreenCapture() {
        val intent = Intent(this, ScreenCaptureService::class.java).apply {
            action = ScreenCaptureService.ACTION_STOP
        }
        startService(intent)

        isStreaming = false
        showRequests(emptyList())
        startButton.text = "Start Mirroring"
        statusText.text = "Ready to stream"
        statusDot.text = "○"
        statusDot.setTextColor(getColor(R.color.text_muted))
    }

    private fun updateAccessibilityStatus() {
        if (RemoteAccessibilityService.isRunning()) {
            accessibilityStatus.text = "Accessibility Service: ✓ Enabled"
            accessibilityStatus.setTextColor(getColor(R.color.status_green))
        } else {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                accessibilityStatus.text = "Accessibility Service: Not enabled\n⚠ On Android 13+: go to Settings → Apps → RemotePhone → ⋮ menu → \"Allow restricted settings\" first"
            } else {
                accessibilityStatus.text = "Accessibility Service: Not enabled"
            }
            accessibilityStatus.setTextColor(getColor(R.color.status_amber))
        }
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
        when {
            selected -> {
                keyboardStatus.text = "RemotePhone Keyboard: \u2713 Active"
                keyboardStatus.setTextColor(getColor(R.color.status_green))
                keyboardButton.text = "Switch keyboard"
            }
            isRemoteKeyboardEnabled() -> {
                keyboardStatus.text = "RemotePhone Keyboard: enabled, not selected"
                keyboardStatus.setTextColor(getColor(R.color.status_amber))
                keyboardButton.text = "Switch keyboard"
            }
            else -> {
                keyboardStatus.text = "RemotePhone Keyboard: not enabled"
                keyboardStatus.setTextColor(getColor(R.color.status_amber))
                keyboardButton.text = "Enable RemotePhone Keyboard"
            }
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
