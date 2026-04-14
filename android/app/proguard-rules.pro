# RemotePhone ProGuard rules

# Keep WebSocket server (uses reflection internally)
-keep class org.java_websocket.** { *; }
-dontwarn org.java_websocket.**

# Keep our accessibility service (referenced in XML)
-keep class com.remotephone.RemoteAccessibilityService { *; }

# Keep JSON parsing
-keep class org.json.** { *; }
