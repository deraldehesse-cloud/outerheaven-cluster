# Proguard rules for Google API client & Play Services
-keep class com.google.api.services.drive.** { *; }
-keepclassmembers class * {
  @com.google.api.client.util.Key <fields>;
}
