package com.remotephone

import android.content.Context
import org.json.JSONObject

/** Who may connect. Read at decision time, so a change applies to the next hello. */
object Prefs {
    const val ASK = "ask_before_connect"      // the owner must allow a new computer on the phone
    const val REMEMBER = "remember_clients"   // Allow also remembers the computer for later sessions
    const val MULTIPLE = "allow_multiple"     // more than one computer at a time
    private const val REMEMBERED = "remembered_computers"  // JSON object: client id to display name

    private fun prefs(ctx: Context) = ctx.getSharedPreferences("settings", Context.MODE_PRIVATE)

    fun ask(ctx: Context) = prefs(ctx).getBoolean(ASK, true)
    fun remember(ctx: Context) = prefs(ctx).getBoolean(REMEMBER, false)
    fun multiple(ctx: Context) = prefs(ctx).getBoolean(MULTIPLE, false)
    fun set(ctx: Context, key: String, value: Boolean) = prefs(ctx).edit().putBoolean(key, value).apply()

    class Remembered(val name: String, val secret: String)

    fun remembered(ctx: Context): Map<String, Remembered> {
        val json = JSONObject(prefs(ctx).getString(REMEMBERED, null) ?: "{}")
        return json.keys().asSequence().associateWith {
            val entry = json.getJSONObject(it)
            Remembered(entry.getString("name"), entry.getString("secret"))
        }
    }
    fun addRemembered(ctx: Context, id: String, name: String, secret: String) {
        val json = JSONObject(prefs(ctx).getString(REMEMBERED, null) ?: "{}")
        json.put(id, JSONObject().put("name", name).put("secret", secret))
        prefs(ctx).edit().putString(REMEMBERED, json.toString()).apply()
    }
    fun forgetRemembered(ctx: Context) = prefs(ctx).edit().remove(REMEMBERED).apply()
}
