package com.remotephone

import android.content.Context
import android.inputmethodservice.InputMethodService
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.TextView

/**
 * Minimal keyboard that lets the desktop client type through the real input
 * pipeline (InputConnection) instead of accessibility SET_TEXT. Web fields,
 * games and custom editors all honour this path because it is what a physical
 * keyboard uses; the accessibility path stays as fallback for when this
 * keyboard is not selected (and for the lock screen, where IMEs do not run).
 */
class RemoteInputMethodService : InputMethodService() {

    companion object {
        private var instance: RemoteInputMethodService? = null

        private fun ic() = instance?.takeIf { it.editorActive }?.currentInputConnection

        private fun key(ic: android.view.inputmethod.InputConnection, code: Int): Boolean {
            ic.sendKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
            return ic.sendKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
        }

        /** Each returns false when this keyboard is not active, so the caller can fall back. */
        fun typeText(content: String): Boolean {
            val ic = ic() ?: return false
            // Enter as a key event: web pages and search boxes listen for the key itself
            return if (content == "\n") key(ic, KeyEvent.KEYCODE_ENTER)
            else ic.commitText(content, 1)
        }

        fun backspace(): Boolean = ic()?.let { key(it, KeyEvent.KEYCODE_DEL) } ?: false

        fun forwardDelete(): Boolean = ic()?.let { key(it, KeyEvent.KEYCODE_FORWARD_DEL) } ?: false

        fun contextMenu(id: Int): Boolean = ic()?.performContextMenuAction(id) ?: false
    }

    private var editorActive = false

    override fun onCreate() {
        super.onCreate()
        instance = this
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    override fun onStartInput(attribute: EditorInfo?, restarting: Boolean) {
        super.onStartInput(attribute, restarting)
        // TYPE_NULL means no real editor is connected (dummy InputConnection)
        editorActive = (attribute?.inputType ?: EditorInfo.TYPE_NULL) != EditorInfo.TYPE_NULL
    }

    override fun onFinishInput() {
        editorActive = false
        super.onFinishInput()
    }

    /**
     * Instead of a key panel, show a slim bar so whoever holds the phone can
     * see why no keyboard appears and switch back with one tap.
     */
    override fun onCreateInputView(): View = TextView(this).apply {
        text = "RemotePhone keyboard active, tap to switch"
        gravity = Gravity.CENTER
        setPadding(24, 28, 24, 28)
        setTextColor(getColor(R.color.text_secondary))
        setBackgroundColor(getColor(R.color.card_bg))
        setOnClickListener {
            (getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager)
                .showInputMethodPicker()
        }
    }
}
