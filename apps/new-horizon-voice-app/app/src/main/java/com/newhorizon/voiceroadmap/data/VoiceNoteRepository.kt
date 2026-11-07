package com.newhorizon.voiceroadmap.data

import android.content.Context
import com.newhorizon.voiceroadmap.data.local.VoiceNoteDatabaseHelper
import com.newhorizon.voiceroadmap.data.model.SyncStatus
import com.newhorizon.voiceroadmap.data.model.VoiceNote
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext

class VoiceNoteRepository private constructor(context: Context) {

    private val dbHelper = VoiceNoteDatabaseHelper(context.applicationContext)
    private val _notesFlow = MutableStateFlow<List<VoiceNote>>(emptyList())
    val notesFlow: StateFlow<List<VoiceNote>> = _notesFlow.asStateFlow()

    suspend fun refreshNotes() = withContext(Dispatchers.IO) {
        val notes = dbHelper.getAllNotes()
        _notesFlow.value = notes
    }

    suspend fun saveNote(text: String): VoiceNote = withContext(Dispatchers.IO) {
        val newNote = VoiceNote(
            text = text,
            timestamp = System.currentTimeMillis(),
            syncStatus = SyncStatus.PENDING_SYNC
        )
        val insertedId = dbHelper.insertNote(newNote)
        val created = newNote.copy(id = insertedId)
        refreshNotes()
        created
    }

    suspend fun getPendingNotes(): List<VoiceNote> = withContext(Dispatchers.IO) {
        dbHelper.getPendingNotes()
    }

    suspend fun markAsSynced(noteId: Long) = withContext(Dispatchers.IO) {
        dbHelper.updateSyncStatus(noteId, SyncStatus.SYNCED)
        refreshNotes()
    }

    suspend fun deleteNote(noteId: Long) = withContext(Dispatchers.IO) {
        dbHelper.deleteNote(noteId)
        refreshNotes()
    }

    companion object {
        @Volatile
        private var INSTANCE: VoiceNoteRepository? = null

        fun getInstance(context: Context): VoiceNoteRepository {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: VoiceNoteRepository(context).also { INSTANCE = it }
            }
        }
    }
}
