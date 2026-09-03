package com.remotephone

import android.app.Activity
import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.inputmethod.InputMethodManager
import android.widget.Button
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
        keyboardStatus = findViewById(R.id.keyboardStatus)
        keyboardButton = findViewById(R.id.keyboardButton)

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

        // Keyboard button: enable in settings first, then just switch keyboards
        keyboardButton.setOnClickListener {
            if (isRemoteKeyboardEnabled()) {
                (getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager).showInputMethodPicker()
            } else {
                startActivity(Intent(Settings.ACTION_INPUT_METHOD_SETTINGS))
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
        updateKeyboardStatus()
        ipText.text = getDeviceIpAddress()
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

        isStreaming = true
        startButton.text = "Stop Mirroring"
        statusText.text = "Streaming"
        statusDot.text = "●"
        statusDot.setTextColor(getColor(R.color.status_green))
    }

    private fun stopScreenCapture() {
        val intent = Intent(this, ScreenCaptureService::class.java).apply {
            action = ScreenCaptureService.ACTION_STOP
        }
        startService(intent)

        isStreaming = false
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
