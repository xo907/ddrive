# The map page calls into the app (MapScreen's bridge): those methods are found by name.
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
# Activities, services and receivers are named in the manifest (kept by default); nothing else is looked up by name.
# DDrive's shared code (st.xo.ddrive.core) finds its word list next to its own classes (Words): its names stay.
-keep class st.xo.ddrive.core.** { *; }
