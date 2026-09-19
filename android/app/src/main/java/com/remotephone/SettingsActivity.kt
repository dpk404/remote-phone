package com.remotephone

import android.os.Bundle
import android.widget.Button
import android.widget.Switch
import android.widget.TextView
import androidx.activity.ComponentActivity

class SettingsActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        bind(R.id.askToggle, Prefs.ASK, Prefs.ask(this))
        bind(R.id.rememberToggle, Prefs.REMEMBER, Prefs.remember(this))
        bind(R.id.multipleToggle, Prefs.MULTIPLE, Prefs.multiple(this))

        findViewById<Button>(R.id.forgetButton).setOnClickListener {
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
        val names = Prefs.remembered(this).values.map { it.name }.sorted()
        findViewById<TextView>(R.id.rememberedText).text =
            if (names.isEmpty()) "No remembered computers" else "Remembered: ${names.joinToString(", ")}"
        findViewById<Button>(R.id.forgetButton).isEnabled = names.isNotEmpty()
    }
}
