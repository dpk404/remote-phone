package com.remotephone

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Context
import android.graphics.Path
import android.graphics.Rect
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.util.Log
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import androidx.annotation.RequiresApi
import org.json.JSONObject

class RemoteAccessibilityService : AccessibilityService() {

    companion object {
        private const val TAG = "RemoteAccessibility"
        private var instance: RemoteAccessibilityService? = null

        /** Selection captured at copy/cut time, for clients when the clipboard is unreadable. */
        @Volatile
        var lastCopiedText: String? = null
            private set

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
                    // Text editing prefers the real input pipeline: on Android 13+ the
                    // service holds its own InputConnection (flagInputMethodEditor), so
                    // the user's keyboard stays selected. Next preference is the
                    // RemotePhone Keyboard when it is the active IME (pre-13), and the
                    // accessibility node actions below are the last resort.
                    "text" -> {
                        val content = cmd.getString("content")
                        if (!service.imeCommit(content) &&
                            !RemoteInputMethodService.typeText(content)) service.performTextInput(content)
                    }
                    "backspace" -> {
                        if (!service.imeKey(KeyEvent.KEYCODE_DEL) &&
                            !RemoteInputMethodService.backspace()) service.performBackspace()
                    }
                    "delete" -> {
                        if (!service.imeKey(KeyEvent.KEYCODE_FORWARD_DEL) &&
                            !RemoteInputMethodService.forwardDelete()) service.performDelete()
                    }
                    "select_all" -> {
                        if (!service.imeContextMenu(android.R.id.selectAll) &&
                            !RemoteInputMethodService.contextMenu(android.R.id.selectAll)) service.performSelectAll()
                    }
                    "copy" -> {
                        if (!service.imeCopy(cut = false) &&
                            !RemoteInputMethodService.contextMenu(android.R.id.copy))
                            service.performClipboardAction(AccessibilityNodeInfo.ACTION_COPY)
                    }
                    "cut" -> {
                        if (!service.imeCopy(cut = true) &&
                            !RemoteInputMethodService.contextMenu(android.R.id.cut))
                            service.performClipboardAction(AccessibilityNodeInfo.ACTION_CUT)
                    }
                    "paste" -> {
                        if (!service.imeContextMenu(android.R.id.paste) &&
                            !RemoteInputMethodService.contextMenu(android.R.id.paste))
                            service.performClipboardAction(AccessibilityNodeInfo.ACTION_PASTE)
                    }
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

    // ---- Input via the service's own InputConnection (Android 13+) ----
    // Null when the OS is older or no editor is focused; callers then fall back.

    @RequiresApi(Build.VERSION_CODES.TIRAMISU)
    private fun imeIc() = inputMethod?.currentInputConnection

    private fun imeCommit(content: String): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return false
        val ic = imeIc() ?: return false
        if (content == "\n") {
            // Enter as a key event: pages and search boxes listen for the key itself
            ic.sendKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_ENTER))
            ic.sendKeyEvent(KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_ENTER))
        } else {
            ic.commitText(content, 1, null)
        }
        return true
    }

    private fun imeKey(code: Int): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return false
        val ic = imeIc() ?: return false
        ic.sendKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
        ic.sendKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
        return true
    }

    private fun imeContextMenu(id: Int): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return false
        val ic = imeIc() ?: return false
        ic.performContextMenuAction(id)
        return true
    }

    /** Copy/cut, capturing the selection so the server can mirror it to clients
     *  even though only the active IME may read the clipboard. */
    private fun imeCopy(cut: Boolean): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return false
        val ic = imeIc() ?: return false
        try {
            val st = ic.getSurroundingText(5000, 5000, 0)
            if (st != null) {
                val a = minOf(st.selectionStart, st.selectionEnd).coerceIn(0, st.text.length)
                val b = maxOf(st.selectionStart, st.selectionEnd).coerceIn(0, st.text.length)
                if (a != b) lastCopiedText = st.text.substring(a, b).toString()
            }
        } catch (e: Exception) {
            Log.w(TAG, "Selection read failed", e)
        }
        ic.performContextMenuAction(if (cut) android.R.id.cut else android.R.id.copy)
        return true
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
                val rect = Rect()
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

    /** Current selection of [node] as (start, end), ordered and clamped to [text]. Missing cursor = end of text. */
    private fun selection(node: AccessibilityNodeInfo, text: String): Pair<Int, Int> {
        val s = node.textSelectionStart.let { if (it < 0) text.length else it }
        val e = node.textSelectionEnd.let { if (it < 0) text.length else it }
        return minOf(s, e).coerceIn(0, text.length) to maxOf(s, e).coerceIn(0, text.length)
    }

    /** Replace the field's text and put the cursor at [cursor]. */
    private fun setText(node: AccessibilityNodeInfo, text: String, cursor: Int) {
        node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        })
        node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, Bundle().apply {
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, cursor)
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, cursor)
        })
    }

    /**
     * Type text into the currently focused input field at the cursor (replacing any selection).
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
                                val rect = Rect()
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

        val currentText = getEditableText(node)

        if (content == "\n") {
            // Send IME enter action
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                node.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
            } else {
                setText(node, currentText + "\n", currentText.length + 1)
            }
            return
        }

        val (start, end) = selection(node, currentText)
        setText(node, currentText.substring(0, start) + content + currentText.substring(end), start + content.length)
        Log.d(TAG, "Typed '$content' into field")
    }

    /**
     * Delete the selection, or the character before the cursor (backspace).
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
                        val rect = Rect()
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
        val (start, end) = selection(node, currentText)
        when {
            start != end -> setText(node, currentText.substring(0, start) + currentText.substring(end), start)
            start > 0 -> setText(node, currentText.substring(0, start - 1) + currentText.substring(start), start - 1)
        }
    }

    /**
     * Delete the selection, or the character after the cursor (delete key).
     */
    private fun performDelete() {
        val node = findFocusedEditText() ?: return

        val currentText = getEditableText(node)
        val (start, end) = selection(node, currentText)
        when {
            start != end -> setText(node, currentText.substring(0, start) + currentText.substring(end), start)
            end < currentText.length -> setText(node, currentText.substring(0, start) + currentText.substring(start + 1), start)
        }
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

    /** Dispatch a single-stroke gesture along [path] lasting [durationMs]. */
    private fun dispatch(path: Path, durationMs: Long) {
        val stroke = GestureDescription.StrokeDescription(path, 0, durationMs)
        dispatchGesture(GestureDescription.Builder().addStroke(stroke).build(), null, null)
    }

    private fun performTap(x: Float, y: Float) =
        dispatch(Path().apply { moveTo(x, y) }, 10)

    private fun performSwipe(x1: Float, y1: Float, x2: Float, y2: Float, duration: Long) =
        dispatch(Path().apply { moveTo(x1, y1); lineTo(x2, y2) }, duration.coerceAtLeast(50))

    private fun performLongPress(x: Float, y: Float, duration: Long) =
        dispatch(Path().apply { moveTo(x, y) }, duration.coerceAtLeast(500))

    private fun performScroll(x: Float, y: Float, dy: Float) {
        // Map scroll delta to a swipe gesture; 3x is the scroll sensitivity knob
        val distance = dy * 3f
        dispatch(Path().apply { moveTo(x, y); lineTo(x, (y - distance).coerceIn(0f, 10000f)) }, 200)
    }

    private fun performKey(action: String) {
        val globalAction = when (action) {
            "back" -> GLOBAL_ACTION_BACK
            "home" -> GLOBAL_ACTION_HOME
            "recents" -> GLOBAL_ACTION_RECENTS
            "notifications" -> GLOBAL_ACTION_NOTIFICATIONS
            "quick_settings" -> GLOBAL_ACTION_QUICK_SETTINGS
            "power" -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
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
