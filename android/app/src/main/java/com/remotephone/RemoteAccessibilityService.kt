package com.remotephone

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Path
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONObject

class RemoteAccessibilityService : AccessibilityService() {

    companion object {
        private const val TAG = "RemoteAccessibility"
        private var instance: RemoteAccessibilityService? = null

        fun isRunning(): Boolean = instance != null

        fun handleCommand(json: String) {
            val service = instance ?: run {
                Log.w(TAG, "Accessibility service not running — cannot dispatch gesture")
                return
            }

            // Wake screen if it's off before dispatching any gesture
            service.ensureScreenOn()

            try {
                val cmd = JSONObject(json)
                when (cmd.getString("type")) {
                    "tap" -> service.performTap(
                        cmd.getDouble("x").toFloat(),
                        cmd.getDouble("y").toFloat()
                    )
                    "touch_down" -> {
                        // Immediate touch feedback — currently a no-op since
                        // AccessibilityService gestures are atomic. The follow-up
                        // tap/swipe/long_press will execute the actual gesture.
                        // This exists so the client can send it without error.
                    }
                    "swipe" -> service.performSwipe(
                        cmd.getDouble("x1").toFloat(),
                        cmd.getDouble("y1").toFloat(),
                        cmd.getDouble("x2").toFloat(),
                        cmd.getDouble("y2").toFloat(),
                        cmd.optLong("duration", 300)
                    )
                    "long_press" -> service.performLongPress(
                        cmd.getDouble("x").toFloat(),
                        cmd.getDouble("y").toFloat(),
                        cmd.optLong("duration", 1000)
                    )
                    "scroll" -> service.performScroll(
                        cmd.getDouble("x").toFloat(),
                        cmd.getDouble("y").toFloat(),
                        cmd.getDouble("dy").toFloat()
                    )
                    "key" -> service.performKey(cmd.getString("action"))
                    "text" -> {
                        val content = cmd.getString("content")
                        service.performTextInput(content)
                    }
                    "backspace" -> service.performBackspace()
                    "delete" -> service.performDelete()
                    "select_all" -> service.performSelectAll()
                    "copy" -> service.performClipboardAction(AccessibilityNodeInfo.ACTION_COPY)
                    "cut" -> service.performClipboardAction(AccessibilityNodeInfo.ACTION_CUT)
                    "paste" -> service.performClipboardAction(AccessibilityNodeInfo.ACTION_PASTE)
                }
            } catch (e: Exception) {
                Log.e(TAG, "Error handling command: $json", e)
            }
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.i(TAG, "Accessibility service connected — remote control ready")
    }

    override fun onDestroy() {
        instance = null
        Log.i(TAG, "Accessibility service destroyed")
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Not used — we only need gesture dispatch capabilities
    }

    override fun onInterrupt() {
        // Required override
    }

    // ---- Screen wake ----

    private var wakeLock: PowerManager.WakeLock? = null

    private fun ensureScreenOn() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        if (!pm.isInteractive) {
            // Wake the screen
            wakeLock?.release()
            @Suppress("DEPRECATION")
            wakeLock = pm.newWakeLock(
                PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP,
                "RemotePhone:WakeLock"
            ).apply {
                acquire(5000) // hold for 5 seconds, auto-release
            }
            Log.i(TAG, "Screen woken up for remote input")
        }
    }

    // ---- Text input ----

    /**
     * Find the currently focused editable text field.
     * Returns null for password/PIN fields — those use clickButtonByLabel() instead.
     */
    private fun findFocusedEditText(): AccessibilityNodeInfo? {
        val root = rootInActiveWindow ?: return null
        val focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
        if (focused != null && focused.isEditable) {
            if (focused.isPassword) return null
            return focused
        }
        val found = findEditableNode(root)
        if (found != null && found.isPassword) return null
        return found
    }

    /**
     * Check if a password/PIN field is currently focused.
     */
    private fun isPasswordFieldFocused(): Boolean {
        val root = rootInActiveWindow ?: return false
        val focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
        if (focused != null && focused.isPassword) return true
        val found = findEditableNode(root)
        return found != null && found.isPassword
    }

    /**
     * Find a clickable button/key by its text label and click it.
     * Used for PIN pad and on-screen keyboard interaction.
     */
    private fun clickButtonByLabel(label: String): Boolean {
        val root = rootInActiveWindow ?: return false
        val nodes = root.findAccessibilityNodeInfosByText(label)
        for (node in nodes) {
            val nodeText = node.text?.toString()?.trim() ?: ""
            val nodeDesc = node.contentDescription?.toString()?.trim() ?: ""
            // Match exact label (avoid "10" matching "1")
            if (nodeText == label || nodeDesc == label) {
                if (node.isClickable) {
                    node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                    Log.d(TAG, "Clicked button '$label'")
                    return true
                }
                // If node itself isn't clickable, try its parent
                var parent = node.parent
                while (parent != null) {
                    if (parent.isClickable) {
                        parent.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                        Log.d(TAG, "Clicked parent of '$label'")
                        return true
                    }
                    parent = parent.parent
                }
                // Fallback: tap the center of the node's bounds
                val rect = android.graphics.Rect()
                node.getBoundsInScreen(rect)
                if (rect.width() > 0 && rect.height() > 0) {
                    performTap(rect.centerX().toFloat(), rect.centerY().toFloat())
                    Log.d(TAG, "Tapped center of '$label' at (${rect.centerX()}, ${rect.centerY()})")
                    return true
                }
            }
        }
        return false
    }

    private fun findEditableNode(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isFocused && node.isEditable) return node
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val result = findEditableNode(child)
            if (result != null) return result
        }
        return null
    }

    /**
     * Get the real editable text from a node, excluding placeholder/hint text.
     */
    /**
     * Get the real editable text from a node, excluding placeholder/hint text.
     * Many Android views return the hint via getText() when the field is empty.
     */
    private fun getEditableText(node: AccessibilityNodeInfo): String {
        val text = node.text?.toString() ?: return ""

        // Check 1: if hintText matches text, field is showing placeholder
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val hint = node.hintText?.toString()
            if (hint != null && text == hint) return ""
        }

        // Check 2: if cursor is at 0,0 or -1,-1 but text is non-empty,
        // the "text" is almost certainly the placeholder/hint
        val selStart = node.textSelectionStart
        val selEnd = node.textSelectionEnd
        if (selStart <= 0 && selEnd <= 0 && text.isNotEmpty()) {
            return ""
        }

        return text
    }

    /**
     * Type text into the currently focused input field by appending to existing text.
     * For password/PIN fields, clicks the on-screen buttons instead.
     */
    private fun performTextInput(content: String) {
        // For password/PIN fields: click the on-screen buttons
        if (isPasswordFieldFocused()) {
            if (content == "\n") {
                // Enter/confirm on PIN pad — try common labels
                if (!clickButtonByLabel("OK") &&
                    !clickButtonByLabel("Done") &&
                    !clickButtonByLabel("Enter") &&
                    !clickButtonByLabel("Confirm") &&
                    !clickButtonByLabel("✓") &&
                    !clickButtonByLabel("ENTER")) {
                    // Last resort: find any node with "enter"/"ok"/"confirm" in description
                    val root = rootInActiveWindow
                    if (root != null) {
                        for (keyword in listOf("enter", "ok", "confirm", "done", "check")) {
                            val nodes = root.findAccessibilityNodeInfosByText(keyword)
                            for (node in nodes) {
                                val rect = android.graphics.Rect()
                                node.getBoundsInScreen(rect)
                                if (rect.width() > 0) {
                                    performTap(rect.centerX().toFloat(), rect.centerY().toFloat())
                                    Log.d(TAG, "Tapped '$keyword' button for PIN confirm")
                                    return
                                }
                            }
                        }
                    }
                }
            } else {
                for (ch in content) {
                    clickButtonByLabel(ch.toString())
                }
            }
            return
        }

        val node = findFocusedEditText()
        if (node == null) {
            Log.w(TAG, "No focused editable field for text input")
            return
        }

        if (content == "\n") {
            // Send IME enter action
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                node.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
            } else {
                val currentText = getEditableText(node)
                val args = Bundle()
                args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, currentText + "\n")
                node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
            }
            return
        }

        // Get current text and cursor position, append new text at cursor
        val currentText = getEditableText(node)

        // Try to get selection/cursor position
        val selStart = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.JELLY_BEAN_MR2) {
            node.textSelectionStart.let { if (it < 0) currentText.length else it }
        } else {
            currentText.length
        }
        val selEnd = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.JELLY_BEAN_MR2) {
            node.textSelectionEnd.let { if (it < 0) currentText.length else it }
        } else {
            currentText.length
        }

        // Replace selection (or insert at cursor if no selection)
        val start = minOf(selStart, selEnd).coerceIn(0, currentText.length)
        val end = maxOf(selStart, selEnd).coerceIn(0, currentText.length)
        val newText = currentText.substring(0, start) + content + currentText.substring(end)
        val newCursor = start + content.length

        val args = Bundle()
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, newText)
        node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)

        // Move cursor to after inserted text
        val cursorArgs = Bundle()
        cursorArgs.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, newCursor)
        cursorArgs.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, newCursor)
        node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, cursorArgs)

        Log.d(TAG, "Typed '$content' into field")
    }

    /**
     * Delete the character before the cursor (backspace).
     */
    private fun performBackspace() {
        // For password/PIN fields: click the on-screen delete/backspace button
        if (isPasswordFieldFocused()) {
            // Try common labels for the delete button on PIN pads
            if (!clickButtonByLabel("Delete") &&
                !clickButtonByLabel("delete") &&
                !clickButtonByLabel("Backspace")) {
                // Fallback: find a node with delete content description
                val root = rootInActiveWindow
                if (root != null) {
                    val nodes = root.findAccessibilityNodeInfosByText("delete")
                    for (node in nodes) {
                        val rect = android.graphics.Rect()
                        node.getBoundsInScreen(rect)
                        if (rect.width() > 0) {
                            performTap(rect.centerX().toFloat(), rect.centerY().toFloat())
                            return
                        }
                    }
                }
            }
            return
        }

        val node = findFocusedEditText()
        if (node == null) {
            // No text field focused — treat as back button
            performGlobalAction(GLOBAL_ACTION_BACK)
            return
        }

        val currentText = getEditableText(node)
        if (currentText.isEmpty()) return

        val selStart = node.textSelectionStart.let { if (it < 0) currentText.length else it }
        val selEnd = node.textSelectionEnd.let { if (it < 0) currentText.length else it }

        val start = minOf(selStart, selEnd).coerceIn(0, currentText.length)
        val end = maxOf(selStart, selEnd).coerceIn(0, currentText.length)

        val newText: String
        val newCursor: Int
        if (start != end) {
            newText = currentText.substring(0, start) + currentText.substring(end)
            newCursor = start
        } else if (start > 0) {
            newText = currentText.substring(0, start - 1) + currentText.substring(start)
            newCursor = start - 1
        } else {
            return
        }

        val args = Bundle()
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, newText)
        node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)

        val cursorArgs = Bundle()
        cursorArgs.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, newCursor)
        cursorArgs.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, newCursor)
        node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, cursorArgs)
    }

    /**
     * Delete the character after the cursor (delete key).
     */
    private fun performDelete() {
        val node = findFocusedEditText() ?: return

        val currentText = getEditableText(node)
        val selStart = node.textSelectionStart.let { if (it < 0) currentText.length else it }
        val selEnd = node.textSelectionEnd.let { if (it < 0) currentText.length else it }

        val start = minOf(selStart, selEnd).coerceIn(0, currentText.length)
        val end = maxOf(selStart, selEnd).coerceIn(0, currentText.length)

        val newText: String
        if (start != end) {
            newText = currentText.substring(0, start) + currentText.substring(end)
        } else if (end < currentText.length) {
            newText = currentText.substring(0, start) + currentText.substring(start + 1)
        } else {
            return
        }

        val args = Bundle()
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, newText)
        node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)

        val cursorArgs = Bundle()
        cursorArgs.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, start)
        cursorArgs.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, start)
        node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, cursorArgs)
    }

    /**
     * Select all text in the focused field.
     */
    private fun performSelectAll() {
        val node = findFocusedEditText() ?: return
        val len = node.text?.length ?: return
        val args = Bundle()
        args.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, 0)
        args.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, len)
        node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, args)
    }

    /**
     * Perform copy/cut/paste on the focused field.
     */
    private fun performClipboardAction(action: Int) {
        val node = findFocusedEditText() ?: return
        node.performAction(action)
    }

    // ---- Gesture implementations ----

    private fun performTap(x: Float, y: Float) {
        val path = Path().apply { moveTo(x, y) }
        val stroke = GestureDescription.StrokeDescription(path, 0, 10)
        val gesture = GestureDescription.Builder().addStroke(stroke).build()
        dispatchGesture(gesture, object : GestureResultCallback() {
            override fun onCompleted(gestureDescription: GestureDescription?) {
                Log.d(TAG, "Tap at ($x, $y) completed")
            }
            override fun onCancelled(gestureDescription: GestureDescription?) {
                Log.w(TAG, "Tap at ($x, $y) cancelled")
            }
        }, null)
    }

    private fun performSwipe(x1: Float, y1: Float, x2: Float, y2: Float, duration: Long) {
        val path = Path().apply {
            moveTo(x1, y1)
            lineTo(x2, y2)
        }
        val stroke = GestureDescription.StrokeDescription(path, 0, duration.coerceAtLeast(50))
        val gesture = GestureDescription.Builder().addStroke(stroke).build()
        dispatchGesture(gesture, null, null)
    }

    private fun performLongPress(x: Float, y: Float, duration: Long) {
        val path = Path().apply { moveTo(x, y) }
        val stroke = GestureDescription.StrokeDescription(path, 0, duration.coerceAtLeast(500))
        val gesture = GestureDescription.Builder().addStroke(stroke).build()
        dispatchGesture(gesture, null, null)
    }

    private fun performScroll(x: Float, y: Float, dy: Float) {
        // Map scroll delta to a swipe gesture
        val distance = dy * 3f  // Scale factor for scroll sensitivity
        val path = Path().apply {
            moveTo(x, y)
            lineTo(x, (y - distance).coerceIn(0f, 10000f))
        }
        val stroke = GestureDescription.StrokeDescription(path, 0, 200)
        val gesture = GestureDescription.Builder().addStroke(stroke).build()
        dispatchGesture(gesture, null, null)
    }

    private fun performKey(action: String) {
        val globalAction = when (action) {
            "back" -> GLOBAL_ACTION_BACK
            "home" -> GLOBAL_ACTION_HOME
            "recents" -> GLOBAL_ACTION_RECENTS
            "notifications" -> GLOBAL_ACTION_NOTIFICATIONS
            "quick_settings" -> GLOBAL_ACTION_QUICK_SETTINGS
            "power" -> {
                if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.P) {
                    GLOBAL_ACTION_LOCK_SCREEN
                } else {
                    Log.w(TAG, "Lock screen action requires Android 9+")
                    return
                }
            }
            else -> {
                Log.w(TAG, "Unknown key action: $action")
                return
            }
        }
        performGlobalAction(globalAction)
    }
}
