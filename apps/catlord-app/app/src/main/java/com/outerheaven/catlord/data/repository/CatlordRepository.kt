package com.outerheaven.catlord.data.repository

import android.content.Context
import com.outerheaven.catlord.data.api.CatlordApiService
import com.outerheaven.catlord.data.local.CatlordDatabaseHelper
import com.outerheaven.catlord.data.model.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext

class CatlordRepository private constructor(context: Context) {

    private val dbHelper = CatlordDatabaseHelper(context.applicationContext)
    private var api = CatlordApiService.create()

    private val _notesFlow = MutableStateFlow<List<CatlordVoiceNote>>(emptyList())
    val notesFlow: StateFlow<List<CatlordVoiceNote>> = _notesFlow.asStateFlow()

    private val _isSyncing = MutableStateFlow(false)
    val isSyncing: StateFlow<Boolean> = _isSyncing.asStateFlow()

    private val _isConnected = MutableStateFlow(false)
    val isConnected: StateFlow<Boolean> = _isConnected.asStateFlow()

    fun updateBaseUrl(newUrl: String) {
        api = CatlordApiService.create(newUrl)
    }

    suspend fun refreshNotes() = withContext(Dispatchers.IO) {
        val notes = dbHelper.getAllNotes()
        _notesFlow.value = notes
    }

    suspend fun saveNote(title: String, category: String, content: String): CatlordVoiceNote = withContext(Dispatchers.IO) {
        val note = CatlordVoiceNote(
            title = title.ifBlank { "Idee ${category.replaceFirstChar { it.uppercase() }}" },
            category = category,
            content = content,
            timestamp = System.currentTimeMillis(),
            syncStatus = SyncStatus.PENDING_SYNC
        )
        val id = dbHelper.insertNote(note)
        val created = note.copy(id = id)
        refreshNotes()

        // Sofort versuchen zu synchronisieren
        syncNote(created)
        created
    }

    suspend fun syncNote(note: CatlordVoiceNote): Boolean = withContext(Dispatchers.IO) {
        try {
            val req = VoiceNotePostRequest(
                title = note.title,
                category = note.category,
                content = note.content
            )
            val res = api.postVoiceNote(req)
            if (res.status == "success") {
                dbHelper.updateSyncStatus(note.id, SyncStatus.SYNCED)
                refreshNotes()
                _isConnected.value = true
                return@withContext true
            } else {
                dbHelper.updateSyncStatus(note.id, SyncStatus.FAILED)
                refreshNotes()
                return@withContext false
            }
        } catch (e: Exception) {
            dbHelper.updateSyncStatus(note.id, SyncStatus.FAILED)
            refreshNotes()
            _isConnected.value = false
            return@withContext false
        }
    }

    suspend fun syncPendingNotes(): Int = withContext(Dispatchers.IO) {
        if (_isSyncing.value) return@withContext 0
        _isSyncing.value = true
        var successCount = 0
        try {
            val pending = dbHelper.getPendingNotes()
            for (note in pending) {
                val ok = syncNote(note)
                if (ok) successCount++
            }
        } finally {
            _isSyncing.value = false
        }
        successCount
    }

    suspend fun deleteNote(noteId: Long) = withContext(Dispatchers.IO) {
        dbHelper.deleteNote(noteId)
        refreshNotes()
    }

    suspend fun checkConnection(): Boolean = withContext(Dispatchers.IO) {
        try {
            val status = api.getStatus()
            val ok = !status.node.isNullOrBlank() || status.status == "ok" || !status.hostname.isNullOrBlank() || !status.timestamp.isNullOrBlank()
            _isConnected.value = ok
            ok
        } catch (e: Exception) {
            _isConnected.value = false
            false
        }
    }

    suspend fun loadDocs(folder: String? = null): Result<List<DocItem>> = withContext(Dispatchers.IO) {
        try {
            val res = api.getDocs(folder)
            Result.success(res.docs)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun loadDocContent(folder: String, filename: String): Result<DocDetailResponse> = withContext(Dispatchers.IO) {
        try {
            val res = api.getDocContent(folder, filename)
            Result.success(res)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun loadMediaStats(): Result<MediaStatsResponse> = withContext(Dispatchers.IO) {
        try {
            val res = api.getMediaStats()
            Result.success(res)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun loadLatestFindings(pkg: String? = null): Result<FindingsResponse> = withContext(Dispatchers.IO) {
        try {
            val res = api.getLatestFindings(pkg)
            Result.success(res)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun loadDropzone(): Result<DropzoneResponse> = withContext(Dispatchers.IO) {
        try {
            val res = api.getDropzone()
            Result.success(res)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun triggerDecompile(apkName: String, filterKeyword: String? = null, targetSubfolder: String? = null): Result<DecompileResponse> = withContext(Dispatchers.IO) {
        try {
            val res = api.decompileApk(DecompileRequest(apkName = apkName, filterKeyword = filterKeyword, targetSubfolder = targetSubfolder))
            Result.success(res)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun askDeepSeek(
        prompt: String,
        model: String = "catlord-hermes:latest",
        pkg: String? = null,
        includeLore: Boolean = false,
        saveToFindings: Boolean = false,
        saveToDrafts: Boolean = true
    ): Result<AskDeepSeekResponse> = withContext(Dispatchers.IO) {
        try {
            val res = api.askDeepSeek(
                AskDeepSeekRequest(
                    prompt = prompt,
                    model = model,
                    pkg = pkg,
                    includeLore = includeLore,
                    saveToFindings = saveToFindings,
                    saveToDrafts = saveToDrafts
                )
            )
            Result.success(res)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    companion object {
        @Volatile
        private var INSTANCE: CatlordRepository? = null

        fun getInstance(context: Context): CatlordRepository {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: CatlordRepository(context).also { INSTANCE = it }
            }
        }
    }
}
