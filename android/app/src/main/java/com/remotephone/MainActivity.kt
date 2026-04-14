package com.remotephone

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.Switch
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import java.net.Inet4Address
import java.net.NetworkInterface

class MainActivity : AppCompatActivity() {

    private lateinit var ipText: TextView
    private lateinit var portText: TextView
    private lateinit var statusDot: TextView
    private lateinit var statusText: TextView
    private lateinit var clientCount: TextView
    private lateinit var startButton: Button
    private lateinit var audioToggle: Switch
    private lateinit var audioSubtext: TextView
    private lateinit var accessibilityStatus: TextView
    private lateinit var accessibilityButton: Button

    private var isStreaming = false

    private val projectionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK && result.data != null) {
            startScreenCapture(result.resultCode, result.data!!)
        } else {
            statusText.text = "Permission denied"
            statusDot.setTextColor(Color.parseColor("#EF4444"))
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
        clientCount = findViewById(R.id.clientCount)
        startButton = findViewById(R.id.startButton)
        audioToggle = findViewById(R.id.audioToggle)
        audioSubtext = findViewById(R.id.audioSubtext)
        accessibilityStatus = findViewById(R.id.accessibilityStatus)
        accessibilityButton = findViewById(R.id.accessibilityButton)

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

        // Request notification permission on Android 13+
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 100)
        }
    }

    override fun onResume() {
        super.onResume()
        updateAccessibilityStatus()
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
        statusDot.setTextColor(Color.parseColor("#10B981"))
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
        statusDot.setTextColor(Color.parseColor("#9CA3AF"))
        clientCount.text = ""
    }

    private fun updateAccessibilityStatus() {
        if (RemoteAccessibilityService.isRunning()) {
            accessibilityStatus.text = "Accessibility Service: ✓ Enabled"
            accessibilityStatus.setTextColor(Color.parseColor("#10B981"))
        } else {
            accessibilityStatus.text = "Accessibility Service: Not enabled"
            accessibilityStatus.setTextColor(Color.parseColor("#F59E0B"))
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
