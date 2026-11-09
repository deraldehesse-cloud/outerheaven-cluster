package com.example.paperwork_scanner.tailscale

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.IpPrefix
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.system.OsConstants
import android.util.Log
import androidx.core.app.NotificationCompat
import com.example.paperwork_scanner.MainActivity
import com.example.paperwork_scanner.R
import libtailscale.Libtailscale
import java.net.InetAddress
import java.util.UUID

class TailscaleVpnService : VpnService(), libtailscale.IPNService {

    private val serviceId = UUID.randomUUID().toString()
    private var isClosed = false

    override fun id(): String = serviceId

    override fun updateVpnStatus(status: Boolean) {
        TailscaleManager.onVpnStatusChanged(status)
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val action = intent?.action
        if (action == ACTION_STOP_VPN) {
            close()
            return START_NOT_STICKY
        }

        startForeground(NOTIFICATION_ID, buildNotification())
        Libtailscale.requestVPN(this)
        return START_STICKY
    }

    override fun protect(socket: Int): Boolean {
        return super.protect(socket)
    }

    override fun close() {
        if (isClosed) return
        isClosed = true
        disconnectVPN()
        try {
            Libtailscale.serviceDisconnect(this)
        } catch (e: Exception) {
            Log.e(TAG, "Error disconnecting libtailscale: ${e.message}")
        }
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun disconnectVPN() {
        stopSelf()
    }

    override fun onDestroy() {
        close()
        updateVpnStatus(false)
        super.onDestroy()
    }

    override fun onRevoke() {
        close()
        updateVpnStatus(false)
        super.onRevoke()
    }

    override fun newBuilder(): libtailscale.VPNServiceBuilder {
        val builder = Builder()
            .setSession("PaperworkScanner Tailscale")
            .allowFamily(OsConstants.AF_INET)
            .allowFamily(OsConstants.AF_INET6)

        val configIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        builder.setConfigureIntent(configIntent)

        return AppVpnServiceBuilder(builder)
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Tailscale VPN",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Status der internen Tailscale VPN-Verbindung"
            }
            val manager = getSystemService(NotificationManager::class.java)
            manager?.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(): Notification {
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Tailscale aktiv")
            .setContentText("Verbunden mit dem gesicherten OuterHeaven-Netzwerk")
            .setSmallIcon(R.drawable.logo_attic_guy)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val TAG = "TailscaleVpnService"
        const val ACTION_START_VPN = "com.example.paperwork_scanner.START_VPN"
        const val ACTION_STOP_VPN = "com.example.paperwork_scanner.STOP_VPN"
        private const val NOTIFICATION_ID = 1001
        private const val CHANNEL_ID = "tailscale_vpn_channel"
    }
}

class AppVpnServiceBuilder(private val builder: VpnService.Builder) : libtailscale.VPNServiceBuilder {

    override fun addAddress(address: String, prefixLength: Int) {
        try {
            builder.addAddress(address, prefixLength)
        } catch (e: Exception) {
            Log.e("AppVpnServiceBuilder", "addAddress failed: $address/$prefixLength", e)
        }
    }

    override fun addDNSServer(address: String) {
        try {
            builder.addDnsServer(address)
        } catch (e: Exception) {
            Log.e("AppVpnServiceBuilder", "addDNSServer failed: $address", e)
        }
    }

    override fun addRoute(address: String, prefixLength: Int) {
        try {
            builder.addRoute(address, prefixLength)
        } catch (e: Exception) {
            Log.e("AppVpnServiceBuilder", "addRoute failed: $address/$prefixLength", e)
        }
    }

    override fun excludeRoute(address: String, prefixLength: Int) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            try {
                val inet = InetAddress.getByName(address)
                builder.excludeRoute(IpPrefix(inet, prefixLength))
            } catch (e: Exception) {
                Log.e("AppVpnServiceBuilder", "excludeRoute failed: $address/$prefixLength", e)
            }
        }
    }

    override fun addSearchDomain(domain: String) {
        try {
            builder.addSearchDomain(domain)
        } catch (e: Exception) {
            Log.e("AppVpnServiceBuilder", "addSearchDomain failed: $domain", e)
        }
    }

    override fun establish(): libtailscale.ParcelFileDescriptor? {
        val pfd = builder.establish() ?: return null
        return AppParcelFileDescriptor(pfd)
    }

    override fun setMTU(mtu: Int) {
        try {
            builder.setMtu(mtu)
        } catch (e: Exception) {
            Log.e("AppVpnServiceBuilder", "setMTU failed: $mtu", e)
        }
    }
}

class AppParcelFileDescriptor(private val pfd: ParcelFileDescriptor) : libtailscale.ParcelFileDescriptor {
    override fun detach(): Int {
        return pfd.detachFd()
    }
}
