package com.example.paperwork_scanner.tailscale

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.os.Build
import android.provider.Settings
import android.util.Log
import androidx.core.content.ContextCompat
import kotlinx.coroutines.*
import libtailscale.AppContext
import libtailscale.Application
import libtailscale.Libtailscale
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.Socket
import com.example.paperwork_scanner.data.AuthPreferencesRepository
import java.util.Collections

object TailscaleManager {

    private const val TAG = "TailscaleManager"
    private const val PREFS_NAME = "tailscale_internal_state"
    // Primär: Tailscale-IP, Fallback: Lokales Heimnetz
    const val TARGET_HOST = "100.127.247.117"
    const val FALLBACK_HOST = "192.168.188.96"
    const val TARGET_PORT = 8000
    const val TAILSCALE_PACKAGE = "com.tailscale.ipn"
    const val DEFAULT_AUTH_KEY = "tskey-auth-k7EweAQHds11CNTRL-feZqt9c5KUjSYXUuPqdnUjEWB2M7skzYd"

    // Hermes auf Fox (Primär: Tailscale, Fallback: Lokales LAN)
    const val HERMES_HOST = "100.93.184.87"
    const val HERMES_FALLBACK_HOST = "192.168.188.91"
    const val HERMES_PORT = 8081

    private var appInstance: Application? = null
    private var isVpnActive: Boolean = false
    @Volatile
    private var cachedActiveHost: String = TARGET_HOST
    @Volatile
    private var cachedHermesHost: String = HERMES_HOST
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    fun getActiveHost(): String {
        if (android.os.Looper.myLooper() == android.os.Looper.getMainLooper()) {
            return cachedActiveHost
        }
        for (host in listOf(TARGET_HOST, FALLBACK_HOST)) {
            try {
                Socket().use { socket ->
                    socket.connect(InetSocketAddress(host, TARGET_PORT), 800)
                    cachedActiveHost = host
                    return host
                }
            } catch (_: Exception) {}
        }
        return cachedActiveHost
    }

    fun getActiveBaseUrl(authRepository: AuthPreferencesRepository? = null): String {
        val configuredUrl = authRepository?.getServerUrl()
        if (!configuredUrl.isNullOrBlank() &&
            configuredUrl != "http://$TARGET_HOST:$TARGET_PORT" &&
            configuredUrl != "http://$FALLBACK_HOST:$TARGET_PORT"
        ) {
            return configuredUrl
        }
        val host = getActiveHost()
        return "http://$host:$TARGET_PORT"
    }

    fun getHermesActiveHost(): String {
        if (android.os.Looper.myLooper() == android.os.Looper.getMainLooper()) {
            return cachedHermesHost
        }
        for (host in listOf(HERMES_HOST, HERMES_FALLBACK_HOST)) {
            try {
                Socket().use { socket ->
                    socket.connect(InetSocketAddress(host, HERMES_PORT), 800)
                    cachedHermesHost = host
                    return host
                }
            } catch (_: Exception) {}
        }
        return cachedHermesHost
    }

    fun getHermesBaseUrl(): String {
        val host = getHermesActiveHost()
        return "http://$host:$HERMES_PORT"
    }

    suspend fun checkPaperlessEndpoint(timeoutMs: Int = 1500): Boolean = withContext(Dispatchers.IO) {
        for (host in listOf(TARGET_HOST, FALLBACK_HOST)) {
            try {
                Socket().use { socket ->
                    socket.connect(InetSocketAddress(host, TARGET_PORT), timeoutMs)
                    cachedActiveHost = host
                    return@withContext true
                }
            } catch (_: Exception) {}
        }
        false
    }

    fun isTailscaleAppInstalled(context: Context): Boolean {
        return try {
            context.packageManager.getLaunchIntentForPackage(TAILSCALE_PACKAGE) != null
        } catch (_: Exception) {
            false
        }
    }

    fun launchTailscaleApp(context: Context): Boolean {
        return try {
            val intent = context.packageManager.getLaunchIntentForPackage(TAILSCALE_PACKAGE)
            if (intent != null) {
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(intent)
                true
            } else {
                false
            }
        } catch (_: Exception) {
            false
        }
    }

    fun onVpnStatusChanged(active: Boolean) {
        Log.d(TAG, "VPN status changed: active=$active")
        isVpnActive = active
    }

    suspend fun isSystemTailscaleActive(): Boolean = withContext(Dispatchers.IO) {
        for (host in listOf(TARGET_HOST, FALLBACK_HOST)) {
            try {
                val socket = Socket()
                socket.connect(InetSocketAddress(host, TARGET_PORT), 1500)
                socket.close()
                Log.d(TAG, "Host tailscale/LAN connection verified directly to $host:$TARGET_PORT")
                return@withContext true
            } catch (e: Exception) {
                Log.d(TAG, "Direct probe to $host:$TARGET_PORT failed: ${e.message}")
            }
        }
        try {
            val interfaces = Collections.list(NetworkInterface.getNetworkInterfaces())
            for (nif in interfaces) {
                if (nif.isUp && (nif.name.contains("tun") || nif.name.contains("tailscale"))) {
                    Log.d(TAG, "Found active VPN interface: ${nif.name}")
                    return@withContext true
                }
            }
        } catch (_: Exception) {}
        return@withContext false
    }

    fun start(
        context: Context,
        authKey: String = DEFAULT_AUTH_KEY,
        onConnected: () -> Unit,
        onError: (String) -> Unit
    ) {
        scope.launch {
            try {
                // 1. Check if system tailscale is already up (Sebastian's test device)
                if (isSystemTailscaleActive()) {
                    Log.i(TAG, "System Tailscale is already active. Skipping embedded VPN setup.")
                    withContext(Dispatchers.Main) { onConnected() }
                    return@launch
                }

                // 2. Initialize embedded Libtailscale if not already started
                val appContext = TailscaleAppContext(context.applicationContext)
                val filesDir = context.filesDir.absolutePath

                if (appInstance == null) {
                    Log.i(TAG, "Starting Libtailscale runtime...")
                    appInstance = Libtailscale.start(filesDir, filesDir, false, appContext)
                }

                // 3. Start TailscaleVpnService
                val serviceIntent = Intent(context, TailscaleVpnService::class.java).apply {
                    action = TailscaleVpnService.ACTION_START_VPN
                }
                ContextCompat.startForegroundService(context, serviceIntent)

                // 4. Send start request with AuthKey to LocalAPI
                val startBody = JSONObject().apply {
                    put("UpdatePrefs", JSONObject().apply {
                        put("WantRunning", true)
                        put("ControlURL", "https://controlplane.tailscale.com")
                        put("LoggedOut", false)
                    })
                    put("AuthKey", authKey)
                }.toString().toByteArray(Charsets.UTF_8)

                Log.i(TAG, "Calling LocalAPI /localapi/v0/start with AuthKey...")
                val startResp = appInstance?.callLocalAPI(
                    30000L,
                    "POST",
                    "/localapi/v0/start",
                    ByteStreamAdapter(startBody)
                )
                Log.i(TAG, "LocalAPI start returned status: ${startResp?.statusCode()}")

                // 5. Poll for connectivity
                var connected = false
                val startTime = System.currentTimeMillis()
                val timeoutMs = 35000L

                while (System.currentTimeMillis() - startTime < timeoutMs) {
                    delay(1500)

                    // Check probe reachability
                    try {
                        for (host in listOf(TARGET_HOST, FALLBACK_HOST)) {
                            try {
                                val socket = Socket()
                                socket.connect(InetSocketAddress(host, TARGET_PORT), 1500)
                                socket.close()
                                connected = true
                                Log.i(TAG, "Successfully connected to $host:$TARGET_PORT via tunnel!")
                                break
                            } catch (_: Exception) {}
                        }
                        if (connected) break
                    } catch (_: Exception) {
                        // Check backend status via LocalAPI
                        try {
                            val statusResp = appInstance?.callLocalAPI(
                                5000L,
                                "GET",
                                "/localapi/v0/status",
                                null
                            )
                            val bodyBytes = statusResp?.bodyBytes()
                            if (bodyBytes != null && bodyBytes.isNotEmpty()) {
                                val jsonStr = String(bodyBytes, Charsets.UTF_8)
                                val statusObj = JSONObject(jsonStr)
                                val backendState = statusObj.optString("BackendState")
                                Log.d(TAG, "Tailscale BackendState: $backendState")
                                if (backendState == "Running") {
                                    connected = true
                                    break
                                }
                            }
                        } catch (e: Exception) {
                            Log.d(TAG, "Error checking LocalAPI status: ${e.message}")
                        }
                    }
                }

                if (connected) {
                    withContext(Dispatchers.Main) { onConnected() }
                } else {
                    withContext(Dispatchers.Main) {
                        onError("Verbindung zum Paperless-Server konnte nicht hergestellt werden. Bitte Tailscale oder Heim-WLAN prüfen.")
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "Error starting Tailscale: ${e.message}", e)
                withContext(Dispatchers.Main) {
                    onError("Fehler beim Starten des Tailscale-Tunnels: ${e.localizedMessage}")
                }
            }
        }
    }

    fun stop(context: Context) {
        val intent = Intent(context, TailscaleVpnService::class.java).apply {
            action = TailscaleVpnService.ACTION_STOP_VPN
        }
        context.startService(intent)
    }
}

class ByteStreamAdapter(private val bytes: ByteArray) : libtailscale.InputStream {
    private var readDone = false

    override fun read(): ByteArray? {
        if (readDone) return null
        readDone = true
        return bytes
    }

    override fun close() {
        readDone = true
    }
}

class TailscaleAppContext(private val context: Context) : AppContext {

    private val prefs: SharedPreferences =
        context.getSharedPreferences("tailscale_app_state", Context.MODE_PRIVATE)

    override fun encryptToPref(prefKey: String?, plaintext: String?) {
        if (prefKey != null && plaintext != null) {
            prefs.edit().putString(prefKey, plaintext).apply()
        }
    }

    override fun decryptFromPref(prefKey: String?): String? {
        return if (prefKey != null) prefs.getString(prefKey, null) else null
    }

    override fun getStateStoreKeysJSON(): String {
        val prefix = "statestore-"
        val keys = prefs.all.keys
            .filter { it.startsWith(prefix) }
            .map { it.removePrefix(prefix) }
        return JSONArray(keys).toString()
    }

    override fun getDeviceName(): String {
        val userDeviceName = Settings.Global.getString(
            context.contentResolver,
            Settings.Global.DEVICE_NAME
        )
        return userDeviceName ?: "Frank-${Build.MODEL}"
    }

    override fun getOSVersion(): String = Build.VERSION.RELEASE

    override fun getSDKInt(): Long = Build.VERSION.SDK_INT.toLong()

    override fun isChromeOS(): Boolean = false

    override fun isClientLoggingEnabled(): Boolean = false

    override fun shouldUseGoogleDNSFallback(): Boolean = true

    override fun getPlatformDNSConfig(): String = ""

    override fun getInstallSource(): String = "debug"

    override fun getInterfacesAsJson(): String {
        val array = JSONArray()
        try {
            val interfaces = Collections.list(NetworkInterface.getNetworkInterfaces())
            for (nif in interfaces) {
                val ifaceObj = JSONObject()
                ifaceObj.put("Name", nif.name)
                val addrsArray = JSONArray()
                for (ia in nif.interfaceAddresses) {
                    val addr = ia.address ?: continue
                    val host = addr.hostAddress ?: continue
                    val ipObj = JSONObject()
                    ipObj.put("IP", host)
                    ipObj.put("PrefixLen", ia.networkPrefixLength.toInt())
                    addrsArray.put(ipObj)
                }
                ifaceObj.put("Addresses", addrsArray)
                array.put(ifaceObj)
            }
        } catch (e: Exception) {
            Log.e("TailscaleAppContext", "getInterfacesAsJson error: ${e.message}")
        }
        return array.toString()
    }

    override fun getSyspolicyBooleanValue(key: String?): Boolean = false

    override fun getSyspolicyStringValue(key: String?): String = ""

    override fun getSyspolicyStringArrayJSONValue(key: String?): String = "[]"

    override fun hardwareAttestationKeySupported(): Boolean = false

    override fun hardwareAttestationKeyCreate(): String = ""

    override fun hardwareAttestationKeyRelease(id: String?) {}

    override fun hardwareAttestationKeySign(id: String?, data: ByteArray?): ByteArray = ByteArray(0)

    override fun hardwareAttestationKeyPublic(id: String?): ByteArray = ByteArray(0)

    override fun hardwareAttestationKeyLoad(id: String?) {}

    override fun bindSocketToNetwork(fd: Int): Boolean = false

    override fun getUserCACertsPEM(): ByteArray = ByteArray(0)

    override fun log(tag: String?, msg: String?) {
        Log.d(tag ?: "Libtailscale", msg ?: "")
    }
}
