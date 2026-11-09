package com.example.paperwork_scanner.data

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException
import java.util.concurrent.TimeUnit

class AuthPreferencesRepository(context: Context) {

    private val sharedPreferences: SharedPreferences =
        context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    init {
        // Hinterlege Standardwerte (defaultValues), falls noch keine SharedPreferences existieren
        val editor = sharedPreferences.edit()
        var changed = false

        if (!sharedPreferences.contains(KEY_PAPERLESS_SERVER_URL)) {
            editor.putString(KEY_PAPERLESS_SERVER_URL, DEFAULT_SERVER_URL)
            changed = true
        }
        if (!sharedPreferences.contains(KEY_PAPERLESS_API_TOKEN)) {
            editor.putString(KEY_PAPERLESS_API_TOKEN, DEFAULT_PAPERLESS_TOKEN)
            changed = true
        }
        if (changed) {
            editor.apply()
        }
    }

    fun isRegistered(): Boolean {
        return hasValidToken()
    }

    fun hasValidToken(): Boolean {
        val token = getPaperlessToken()
        return token.isNotBlank() && token != OLD_INVALID_TOKEN
    }

    fun getServerUrl(): String {
        val saved = sharedPreferences.getString(KEY_PAPERLESS_SERVER_URL, null)
        if (saved.isNullOrBlank()) {
            return DEFAULT_SERVER_URL
        }
        return saved.trim().removeSuffix("/")
    }

    fun saveServerUrl(url: String) {
        val cleaned = url.trim().removeSuffix("/")
        val urlToSave = if (cleaned.isBlank()) DEFAULT_SERVER_URL else cleaned
        sharedPreferences.edit()
            .putString(KEY_PAPERLESS_SERVER_URL, urlToSave)
            .apply()
    }

    fun getPaperlessToken(): String {
        val saved = sharedPreferences.getString(KEY_PAPERLESS_API_TOKEN, null)
        if (saved.isNullOrBlank() || saved == OLD_INVALID_TOKEN) {
            sharedPreferences.edit()
                .putString(KEY_PAPERLESS_API_TOKEN, DEFAULT_PAPERLESS_TOKEN)
                .apply()
            return DEFAULT_PAPERLESS_TOKEN
        }
        return saved.trim()
    }

    fun savePaperlessToken(apiToken: String) {
        val cleaned = apiToken.trim()
        val tokenToSave = if (cleaned.isBlank()) DEFAULT_PAPERLESS_TOKEN else cleaned
        sharedPreferences.edit()
            .putString(KEY_PAPERLESS_API_TOKEN, tokenToSave)
            .apply()
    }

    /**
     * Validiert den angegebenen Token online gegen den Paperless-Server
     */
    suspend fun validateTokenOnline(
        token: String = getPaperlessToken(),
        baseUrl: String = getServerUrl()
    ): TokenValidationResult = withContext(Dispatchers.IO) {
        val client = OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(5, TimeUnit.SECONDS)
            .build()

        try {
            val request = Request.Builder()
                .url("$baseUrl/api/")
                .addHeader("Authorization", "Token $token")
                .get()
                .build()

            client.newCall(request).execute().use { response ->
                when (response.code) {
                    in 200..299 -> TokenValidationResult.Valid
                    401, 403 -> TokenValidationResult.Invalid("API-Token ungültig (HTTP ${response.code})")
                    else -> TokenValidationResult.ServerError(response.code, "Serverfehler (HTTP ${response.code})")
                }
            }
        } catch (e: Exception) {
            TokenValidationResult.NetworkError("Verbindungsfehler: ${e.localizedMessage}")
        }
    }

    sealed class TokenValidationResult {
        data object Valid : TokenValidationResult()
        data class Invalid(val message: String) : TokenValidationResult()
        data class ServerError(val code: Int, val message: String) : TokenValidationResult()
        data class NetworkError(val message: String) : TokenValidationResult()
    }

    companion object {
        const val PREFS_NAME = "paperwork_auth_prefs"
        const val DEFAULT_SERVER_URL = "http://100.127.247.117:8000"
        const val FALLBACK_SERVER_URL = "http://192.168.188.96:8000"
        const val DEFAULT_PAPERLESS_TOKEN = "428107bcaa486337ba98b06547d0a843b3007036"
        private const val OLD_INVALID_TOKEN = "e1ea8674359e0979eece9a5335cd4d49fa709ed"

        const val KEY_PAPERLESS_SERVER_URL = "paperless_server_url"
        const val KEY_PAPERLESS_API_TOKEN = "paperless_api_token"
    }
}
