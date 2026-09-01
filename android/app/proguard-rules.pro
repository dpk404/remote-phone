# RemotePhone ProGuard rules

# Keep WebSocket server (uses reflection internally)
-keep class org.java_websocket.** { *; }
-dontwarn org.java_websocket.**
