package com.remotephone

import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.Switch
import android.widget.TextView
import androidx.activity.ComponentActivity

/** One row of item_computer: icon by client kind, highlighted when [live], and a red text action. */
fun computerRow(
    ctx: Context, parent: ViewGroup, clientName: String, live: Boolean,
    detail: String, action: String, onAction: () -> Unit
): View = LayoutInflater.from(ctx).inflate(R.layout.item_computer, parent, false).apply {
    findViewById<ImageView>(R.id.icon).apply {
        setImageResource(if (MirrorWebSocketServer.isBrowser(clientName)) R.drawable.ic_globe else R.drawable.ic_monitor)
        backgroundTintList = ctx.getColorStateList(if (live) R.color.accent_soft else R.color.inset_bg)
        imageTintList = ctx.getColorStateList(if (live) R.color.accent_text else R.color.text_secondary)
    }
    findViewById<TextView>(R.id.name).text = MirrorWebSocketServer.displayName(clientName)
    findViewById<TextView>(R.id.detail).text = detail
    findViewById<TextView>(R.id.action).apply {
        text = action
        contentDescription = "$action ${MirrorWebSocketServer.displayName(clientName)}"
        setOnClickListener { onAction() }
    }
}

class SettingsActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        bind(R.id.askToggle, Prefs.ASK, Prefs.ask(this))
        bind(R.id.rememberToggle, Prefs.REMEMBER, Prefs.remember(this))
        bind(R.id.multipleToggle, Prefs.MULTIPLE, Prefs.multiple(this))

        findViewById<View>(R.id.backButton).setOnClickListener { finish() }
        findViewById<View>(R.id.forgetButton).setOnClickListener {
            Prefs.forgetRemembered(this)
            showRemembered()
        }
    }

    override fun onResume() {
        super.onResume()
        showRemembered()  // an Allow may have happened while this screen was in the background
    }

    private fun bind(id: Int, key: String, value: Boolean) {
        findViewById<Switch>(id).apply {
            isChecked = value
            setOnCheckedChangeListener { _, checked -> Prefs.set(this@SettingsActivity, key, checked) }
        }
    }

    private fun showRemembered() {
        val live = ScreenCaptureService.watchers().orEmpty().map { it.id }
        val remembered = Prefs.remembered(this).entries.sortedBy { MirrorWebSocketServer.displayName(it.value.name).lowercase() }
        val list = findViewById<LinearLayout>(R.id.rememberedList)
        list.removeAllViews()
        for ((id, computer) in remembered) {
            val kind = if (MirrorWebSocketServer.isBrowser(computer.name)) "Browser" else "Desktop app"
            list.addView(computerRow(
                this, list, computer.name, id in live,
                if (id in live) "$kind \u00B7 connected now" else kind, "Forget"
            ) {
                Prefs.forgetRemembered(this, id)
                showRemembered()
            })
        }
        list.visibility = if (remembered.isEmpty()) View.GONE else View.VISIBLE
        findViewById<TextView>(R.id.rememberedCount).text = "${remembered.size}"
        findViewById<TextView>(R.id.rememberedFooter).text =
            if (remembered.isEmpty()) "No remembered computers yet."
            else "A forgotten computer is asked for again the next time it connects."
        findViewById<View>(R.id.forgetButton).visibility = if (remembered.isEmpty()) View.GONE else View.VISIBLE
    }
}
