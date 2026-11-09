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

enum class SpeechTarget {
    NOTE,
    DEEPSEEK
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

    val categories = listOf("Story", "Gameplay", "Elliot", "Cash / Loco (Casper)", "Gert / Baustelle")

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

    // AI Studio & Media State
    val availableAgents = listOf(
        CatlordAgentInfo(
            id = "catlord-hermes:latest",
            name = "catlord-hermes:latest",
            title = "Hermes (Story & Dialoge)",
            icon = "🎭",
            description = "Lead-Writer für lebendige Dialoge, Quests & Storyline"
        ),
        CatlordAgentInfo(
            id = "catlord-architect:latest",
            name = "catlord-architect:latest",
            title = "Architect (Konzepte)",
            icon = "📐",
            description = "Game-Design, Strukturierung von Voice-Notes & Mechaniken"
        ),
        CatlordAgentInfo(
            id = "metal-coder:latest",
            name = "metal-coder:latest",
            title = "Coder (Kotlin/Game)",
            icon = "💻",
            description = "Kotlin/Python, Game-Logik, Android & Code-Analyse"
        ),
        CatlordAgentInfo(
            id = "metal-fast:latest",
            name = "metal-fast:latest",
            title = "Fast (Quick Chat)",
            icon = "⚡",
            description = "Blitzschneller Assistent für kurze, direkte Antworten"
        )
    )

    private val _selectedModel = MutableStateFlow("catlord-hermes:latest")
    val selectedModel: StateFlow<String> = _selectedModel.asStateFlow()

    fun selectModel(modelId: String) {
        _selectedModel.value = modelId
    }

    private val _includeLore = MutableStateFlow(true)
    val includeLore: StateFlow<Boolean> = _includeLore.asStateFlow()

    fun toggleIncludeLore() {
        _includeLore.value = !_includeLore.value
    }

    private val _saveToDrafts = MutableStateFlow(true)
    val saveToDrafts: StateFlow<Boolean> = _saveToDrafts.asStateFlow()

    fun toggleSaveToDrafts() {
        _saveToDrafts.value = !_saveToDrafts.value
    }

    private val _mediaStats = MutableStateFlow<MediaStatsResponse?>(null)
    val mediaStats: StateFlow<MediaStatsResponse?> = _mediaStats.asStateFlow()

    private val _latestFindings = MutableStateFlow<FindingsResponse?>(null)
    val latestFindings: StateFlow<FindingsResponse?> = _latestFindings.asStateFlow()

    private val _isLoadingReaMedia = MutableStateFlow(false)
    val isLoadingReaMedia: StateFlow<Boolean> = _isLoadingReaMedia.asStateFlow()

    val defaultPackages = listOf("Catlord", "Elliot (Tank)", "Cash / Loco (Casper)", "Story & Quests", "Game Code")
    private val _selectedPackage = MutableStateFlow("Catlord")
    val selectedPackage: StateFlow<String> = _selectedPackage.asStateFlow()

    private val _availablePackages = MutableStateFlow<List<String>>(defaultPackages)
    val availablePackages: StateFlow<List<String>> = _availablePackages.asStateFlow()

    private val _dropzoneApks = MutableStateFlow<List<DropzoneFile>>(emptyList())
    val dropzoneApks: StateFlow<List<DropzoneFile>> = _dropzoneApks.asStateFlow()

    private val _selectedApkToDecompile = MutableStateFlow<String?>(null)
    val selectedApkToDecompile: StateFlow<String?> = _selectedApkToDecompile.asStateFlow()

    private val _filterKeyword = MutableStateFlow("")
    val filterKeyword: StateFlow<String> = _filterKeyword.asStateFlow()

    private val _isDecompiling = MutableStateFlow(false)
    val isDecompiling: StateFlow<Boolean> = _isDecompiling.asStateFlow()

    private val _decompileMessage = MutableStateFlow<String?>(null)
    val decompileMessage: StateFlow<String?> = _decompileMessage.asStateFlow()

    // Agent Prompt State
    private val _deepSeekPrompt = MutableStateFlow("")
    val deepSeekPrompt: StateFlow<String> = _deepSeekPrompt.asStateFlow()

    private val _isAskingDeepSeek = MutableStateFlow(false)
    val isAskingDeepSeek: StateFlow<Boolean> = _isAskingDeepSeek.asStateFlow()

    private val _deepSeekResponse = MutableStateFlow<AskDeepSeekResponse?>(null)
    val deepSeekResponse: StateFlow<AskDeepSeekResponse?> = _deepSeekResponse.asStateFlow()

    // Speech Recognition Helper & Target
    private var speechHelper: SpeechRecognitionHelper? = null
    private var currentSpeechTarget: SpeechTarget = SpeechTarget.NOTE

    init {
        speechHelper = SpeechRecognitionHelper(
            context = application,
            onStateChanged = { state -> _speechState.value = state },
            onPartialResult = { partial ->
                _statusMessage.value = "Höre zu: $partial"
            },
            onFinalResult = { final ->
                if (final.isNotBlank()) {
                    if (currentSpeechTarget == SpeechTarget.NOTE) {
                        val current = _noteContent.value
                        _noteContent.value = if (current.isBlank()) final else "$current\n$final"
                        _statusMessage.value = "Notiz erkannt: $final"
                    } else {
                        val current = _deepSeekPrompt.value
                        _deepSeekPrompt.value = if (current.isBlank()) final else "$current $final"
                        _statusMessage.value = "Prompt erkannt: $final"
                    }
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
        currentSpeechTarget = SpeechTarget.NOTE
        when (_speechState.value) {
            is SpeechState.Listening -> {
                speechHelper?.stopListening()
            }
            else -> {
                speechHelper?.startListening("de-DE")
            }
        }
    }

    fun toggleDeepSeekSpeech() {
        currentSpeechTarget = SpeechTarget.DEEPSEEK
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
    fun selectPackage(pkg: String) {
        _selectedPackage.value = pkg
        loadLatestFindingsForPackage(pkg)
    }

    fun selectApkToDecompile(apk: String?) {
        _selectedApkToDecompile.value = apk
    }

    fun setFilterKeyword(kw: String) {
        _filterKeyword.value = kw
    }

    fun setDeepSeekPrompt(prompt: String) {
        _deepSeekPrompt.value = prompt
    }

    fun clearDeepSeekResponse() {
        _deepSeekResponse.value = null
    }

    fun loadDropzone() {
        viewModelScope.launch {
            val res = repository.loadDropzone()
            res.onSuccess { dz ->
                _dropzoneApks.value = dz.files.filter { it.isApk }
                if (_selectedApkToDecompile.value == null && _dropzoneApks.value.isNotEmpty()) {
                    _selectedApkToDecompile.value = _dropzoneApks.value.first().name
                }
            }
        }
    }

    fun loadLatestFindingsForPackage(pkg: String) {
        viewModelScope.launch {
            _isLoadingReaMedia.value = true
            val findingsRes = repository.loadLatestFindings(pkg)
            findingsRes.onSuccess { findings ->
                _latestFindings.value = findings
                if (findings.availablePackages.isNotEmpty()) {
                    val catlordOnly = findings.availablePackages.filter {
                        it.contains("catlord", ignoreCase = true) ||
                        it.contains("elliot", ignoreCase = true) ||
                        it.contains("kaech", ignoreCase = true)
                    }
                    val merged = (defaultPackages + catlordOnly).distinct()
                    _availablePackages.value = merged
                }
            }.onFailure { err ->
                _statusMessage.value = "Findings für '$pkg' konnten nicht geladen werden: ${err.message}"
            }
            _isLoadingReaMedia.value = false
        }
    }

    fun triggerDecompile() {
        val apk = _selectedApkToDecompile.value ?: return
        val kw = _filterKeyword.value.trim().ifBlank { null }
        viewModelScope.launch {
            _isDecompiling.value = true
            _decompileMessage.value = "Dekompiliere $apk in Sandbox..."
            val res = repository.triggerDecompile(apkName = apk, filterKeyword = kw)
            res.onSuccess { resp ->
                _decompileMessage.value = resp.message.ifBlank { "Dekompilierung gestartet!" }
                _statusMessage.value = "Dekompilierung für $apk gestartet"
                loadMediaAndFindings()
            }.onFailure { err ->
                _decompileMessage.value = "Fehler: ${err.message}"
                _statusMessage.value = "Dekompilierung fehlgeschlagen: ${err.message}"
            }
            _isDecompiling.value = false
        }
    }

    fun askDeepSeek() {
        val prompt = _deepSeekPrompt.value.trim()
        if (prompt.isBlank()) {
            _statusMessage.value = "Bitte eine Frage oder einen Prompt eingeben."
            return
        }
        val currentModel = _selectedModel.value
        val lore = _includeLore.value
        val drafts = _saveToDrafts.value
        viewModelScope.launch {
            _isAskingDeepSeek.value = true
            _statusMessage.value = "Agent generiert Antwort auf metal-dgear..."
            val res = repository.askDeepSeek(
                prompt = prompt,
                model = currentModel,
                pkg = null,
                includeLore = lore,
                saveToFindings = false,
                saveToDrafts = drafts
            )
            res.onSuccess { resp ->
                _deepSeekResponse.value = resp
                _statusMessage.value = "Antwort von ${resp.model ?: currentModel} erhalten!"
            }.onFailure { err ->
                _statusMessage.value = "Fehler bei Anfrage: ${err.message}"
            }
            _isAskingDeepSeek.value = false
        }
    }

    fun loadMediaAndFindings() {
        viewModelScope.launch {
            _isLoadingReaMedia.value = true
            val statsRes = repository.loadMediaStats()
            statsRes.onSuccess { stats ->
                _mediaStats.value = stats
            }

            val findingsRes = repository.loadLatestFindings(_selectedPackage.value)
            findingsRes.onSuccess { findings ->
                _latestFindings.value = findings
                if (findings.availablePackages.isNotEmpty()) {
                    val catlordOnly = findings.availablePackages.filter {
                        it.contains("catlord", ignoreCase = true) ||
                        it.contains("elliot", ignoreCase = true) ||
                        it.contains("kaech", ignoreCase = true)
                    }
                    val merged = (defaultPackages + catlordOnly).distinct()
                    _availablePackages.value = merged
                }
            }

            loadDropzone()
            _isLoadingReaMedia.value = false
        }
    }

    override fun onCleared() {
        super.onCleared()
        speechHelper?.destroy()
    }
}
