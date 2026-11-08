package com.outerheaven.catlord.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.outerheaven.catlord.data.api.CatlordApiService
import com.outerheaven.catlord.data.model.*
import com.outerheaven.catlord.data.repository.CatlordRepository
import com.outerheaven.catlord.speech.SpeechRecognitionHelper
import com.outerheaven.catlord.speech.SpeechState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

enum class AppScreen {
    VOICE_INGEST,
    LORE_VIEWER,
    REA_MEDIA
}

class MainViewModel(application: Application) : AndroidViewModel(application) {

    private val repository = CatlordRepository.getInstance(application)

    // Navigation
    private val _currentScreen = MutableStateFlow(AppScreen.VOICE_INGEST)
    val currentScreen: StateFlow<AppScreen> = _currentScreen.asStateFlow()

    fun setScreen(screen: AppScreen) {
        _currentScreen.value = screen
        if (screen == AppScreen.LORE_VIEWER) {
            loadDocs()
        } else if (screen == AppScreen.REA_MEDIA) {
            loadMediaAndFindings()
        }
    }

    // Server connection
    val isConnected = repository.isConnected
    val isSyncing = repository.isSyncing
    val notes = repository.notesFlow

    private val _serverUrl = MutableStateFlow(CatlordApiService.DEFAULT_BASE_URL)
    val serverUrl: StateFlow<String> = _serverUrl.asStateFlow()

    fun updateServerUrl(url: String) {
        _serverUrl.value = url
        repository.updateBaseUrl(url)
        checkConnection()
    }

    // Voice Ingest State
    private val _speechState = MutableStateFlow<SpeechState>(SpeechState.Ready)
    val speechState: StateFlow<SpeechState> = _speechState.asStateFlow()

    private val _selectedCategory = MutableStateFlow("Story")
    val selectedCategory: StateFlow<String> = _selectedCategory.asStateFlow()

    val categories = listOf("Story", "Gameplay", "Elliot", "Käch", "Loco", "Gert / Baustelle")

    private val _noteTitle = MutableStateFlow("")
    val noteTitle: StateFlow<String> = _noteTitle.asStateFlow()

    private val _noteContent = MutableStateFlow("")
    val noteContent: StateFlow<String> = _noteContent.asStateFlow()

    private val _statusMessage = MutableStateFlow<String?>(null)
    val statusMessage: StateFlow<String?> = _statusMessage.asStateFlow()

    // Lore Viewer State
    private val _docsList = MutableStateFlow<List<DocItem>>(emptyList())
    val docsList: StateFlow<List<DocItem>> = _docsList.asStateFlow()

    private val _selectedFolderFilter = MutableStateFlow<String?>(null)
    val selectedFolderFilter: StateFlow<String?> = _selectedFolderFilter.asStateFlow()

    private val _searchQuery = MutableStateFlow("")
    val searchQuery: StateFlow<String> = _searchQuery.asStateFlow()

    private val _selectedDoc = MutableStateFlow<DocDetailResponse?>(null)
    val selectedDoc: StateFlow<DocDetailResponse?> = _selectedDoc.asStateFlow()

    private val _isLoadingDocs = MutableStateFlow(false)
    val isLoadingDocs: StateFlow<Boolean> = _isLoadingDocs.asStateFlow()

    // REA & Media State
    private val _mediaStats = MutableStateFlow<MediaStatsResponse?>(null)
    val mediaStats: StateFlow<MediaStatsResponse?> = _mediaStats.asStateFlow()

    private val _latestFindings = MutableStateFlow<FindingsResponse?>(null)
    val latestFindings: StateFlow<FindingsResponse?> = _latestFindings.asStateFlow()

    private val _isLoadingReaMedia = MutableStateFlow(false)
    val isLoadingReaMedia: StateFlow<Boolean> = _isLoadingReaMedia.asStateFlow()

    // Speech Recognition Helper
    private var speechHelper: SpeechRecognitionHelper? = null

    init {
        speechHelper = SpeechRecognitionHelper(
            context = application,
            onStateChanged = { state -> _speechState.value = state },
            onPartialResult = { partial ->
                // Bei Teilergebnis an aktuellen Inhalt anhängen oder aktualisieren
                _statusMessage.value = "Höre zu: $partial"
            },
            onFinalResult = { final ->
                if (final.isNotBlank()) {
                    val current = _noteContent.value
                    _noteContent.value = if (current.isBlank()) final else "$current\n$final"
                    _statusMessage.value = "Erkannt: $final"
                }
            }
        )

        viewModelScope.launch {
            repository.refreshNotes()
            checkConnection()
        }
    }

    fun setCategory(category: String) {
        _selectedCategory.value = category
    }

    fun setTitle(title: String) {
        _noteTitle.value = title
    }

    fun setContent(content: String) {
        _noteContent.value = content
    }

    fun clearStatusMessage() {
        _statusMessage.value = null
    }

    fun toggleSpeechRecognition() {
        when (_speechState.value) {
            is SpeechState.Listening -> {
                speechHelper?.stopListening()
            }
            else -> {
                speechHelper?.startListening("de-DE")
            }
        }
    }

    fun saveCurrentNote() {
        val content = _noteContent.value.trim()
        if (content.isBlank()) {
            _statusMessage.value = "Bitte zuerst Notiztext einsprechen oder eingeben."
            return
        }

        val category = _selectedCategory.value
        val title = _noteTitle.value.trim()

        viewModelScope.launch {
            repository.saveNote(title, category, content)
            _noteTitle.value = ""
            _noteContent.value = ""
            _statusMessage.value = "Notiz gespeichert & Sync ausgelöst!"
        }
    }

    fun syncAllNotes() {
        viewModelScope.launch {
            val count = repository.syncPendingNotes()
            _statusMessage.value = if (count > 0) "$count Notiz(en) erfolgreich synchronisiert." else "Alle Notizen sind bereits synchronisiert."
        }
    }

    fun deleteNote(id: Long) {
        viewModelScope.launch {
            repository.deleteNote(id)
        }
    }

    fun checkConnection() {
        viewModelScope.launch {
            repository.checkConnection()
        }
    }

    // Lore Docs Functions
    fun setFolderFilter(folder: String?) {
        _selectedFolderFilter.value = folder
        loadDocs()
    }

    fun setSearchQuery(query: String) {
        _searchQuery.value = query
    }

    fun loadDocs() {
        viewModelScope.launch {
            _isLoadingDocs.value = true
            val res = repository.loadDocs(_selectedFolderFilter.value)
            res.onSuccess { list ->
                _docsList.value = list
            }.onFailure { err ->
                _statusMessage.value = "Fehler beim Laden der Docs: ${err.message}"
            }
            _isLoadingDocs.value = false
        }
    }

    fun openDoc(folder: String, filename: String) {
        viewModelScope.launch {
            _isLoadingDocs.value = true
            val res = repository.loadDocContent(folder, filename)
            res.onSuccess { doc ->
                _selectedDoc.value = doc
            }.onFailure { err ->
                _statusMessage.value = "Konnte Datei nicht öffnen: ${err.message}"
            }
            _isLoadingDocs.value = false
        }
    }

    fun closeDocViewer() {
        _selectedDoc.value = null
    }

    // REA & Media Functions
    fun loadMediaAndFindings() {
        viewModelScope.launch {
            _isLoadingReaMedia.value = true
            val statsRes = repository.loadMediaStats()
            statsRes.onSuccess { stats ->
                _mediaStats.value = stats
            }

            val findingsRes = repository.loadLatestFindings()
            findingsRes.onSuccess { findings ->
                _latestFindings.value = findings
            }
            _isLoadingReaMedia.value = false
        }
    }

    override fun onCleared() {
        super.onCleared()
        speechHelper?.destroy()
    }
}
