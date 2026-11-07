package com.newhorizon.voiceroadmap.data.model

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

enum class SyncStatus {
    PENDING_SYNC,
    SYNCED
}

data class VoiceNote(
    val id: Long = 0,
    val text: String,
    val timestamp: Long = System.currentTimeMillis(),
    val syncStatus: SyncStatus = SyncStatus.PENDING_SYNC
) {
    fun formattedTimestamp(): String {
        val sdf = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault())
        return sdf.format(Date(timestamp))
    }

    fun toMarkdownEntry(): String {
        return "### [${formattedTimestamp()}] - Voice Note\n\n${text.trim()}\n\n"
    }
}
