package com.newhorizon.voiceroadmap.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.google.android.gms.auth.api.signin.GoogleSignIn
import com.google.android.gms.auth.api.signin.GoogleSignInAccount
import com.newhorizon.voiceroadmap.data.DriveRepository
import com.newhorizon.voiceroadmap.data.DriveSyncWorker
import com.newhorizon.voiceroadmap.data.VoiceNoteRepository
import com.newhorizon.voiceroadmap.data.model.VoiceNote
import com.newhorizon.voiceroadmap.speech.SpeechRecognitionHelper
import com.newhorizon.voiceroadmap.speech.SpeechState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

class MainViewModel(application: Application) : AndroidViewModel(application) {

    private val repository = VoiceNoteRepository.getInstance(application)
    private val driveRepository = DriveRepository(application)

    val voiceNotes: StateFlow<List<VoiceNote>> = repository.notesFlow.stateIn(
        scope = viewModelScope,
        started = SharingStarted.WhileSubscribed(5000),
        initialValue = emptyList()
    )

    private val _speechState = MutableStateFlow<SpeechState>(SpeechState.Ready)
    val speechState: StateFlow<SpeechState> = _speechState.asStateFlow()

    private val _liveTranscript = MutableStateFlow("")
    val liveTranscript: StateFlow<String> = _liveTranscript.asStateFlow()

    private val _googleAccount = MutableStateFlow<GoogleSignInAccount?>(null)
    val googleAccount: StateFlow<GoogleSignInAccount?> = _googleAccount.asStateFlow()

    private val _isSyncing = MutableStateFlow(false)
    val isSyncing: StateFlow<Boolean> = _isSyncing.asStateFlow()

    private val _statusMessage = MutableStateFlow<String?>(null)
    val statusMessage: StateFlow<String?> = _statusMessage.asStateFlow()

    private var speechHelper: SpeechRecognitionHelper? = null

    init {
        // Initiale Notizen laden
        viewModelScope.launch {
            repository.refreshNotes()
        }

        // Zuletzt angemeldetes Google-Konto abfragen
        val lastAccount = GoogleSignIn.getLastSignedInAccount(application)
        _googleAccount.value = lastAccount

        // Speech Recognition initialisieren
        speechHelper = SpeechRecognitionHelper(
            context = application,
            onStateChanged = { state ->
                _speechState.value = state
                if (state is SpeechState.Error) {
                    _statusMessage.value = state.errorMessage
                }
            },
            onPartialResult = { partial ->
                _liveTranscript.value = partial
            },
            onFinalResult = { final ->
                if (final.isNotBlank()) {
                    _liveTranscript.value = final
                }
            }
        )
    }

    fun startListening() {
        speechHelper?.startListening()
    }

    fun stopListening() {
        speechHelper?.stopListening()
    }

    fun cancelListening() {
        speechHelper?.cancel()
    }

    fun updateTranscript(text: String) {
        _liveTranscript.value = text
    }

    fun discardTranscript() {
        _liveTranscript.value = ""
        _speechState.value = SpeechState.Ready
    }

    fun saveCurrentNote() {
        val text = _liveTranscript.value.trim()
        if (text.isBlank()) return

        viewModelScope.launch {
            val savedNote = repository.saveNote(text)
            _liveTranscript.value = ""
            _speechState.value = SpeechState.Ready
            _statusMessage.value = "Voice Note lokal gespeichert."

            // Hintergrund-Sync via WorkManager registrieren
            DriveSyncWorker.enqueueSync(getApplication())

            // Wenn Google Account bereits verbunden ist, direkten Push versuchen
            val account = _googleAccount.value
            if (account != null) {
                syncPendingNotesNow(account)
            }
        }
    }

    fun setGoogleAccount(account: GoogleSignInAccount?) {
        _googleAccount.value = account
        if (account != null) {
            _statusMessage.value = "Angemeldet als ${account.email}"
            triggerManualSync()
        } else {
            _statusMessage.value = "Google-Konto getrennt"
        }
    }

    fun triggerManualSync() {
        val account = _googleAccount.value
        if (account == null) {
            _statusMessage.value = "Bitte zuerst mit Google anmelden."
            return
        }

        viewModelScope.launch {
            syncPendingNotesNow(account)
        }
    }

    private suspend fun syncPendingNotesNow(account: GoogleSignInAccount) {
        _isSyncing.value = true
        val pendingNotes = repository.getPendingNotes()
        if (pendingNotes.isEmpty()) {
            _isSyncing.value = false
            return
        }

        val result = driveRepository.appendNotesToRoadmap(account, pendingNotes)
        if (result.isSuccess) {
            for (note in pendingNotes) {
                repository.markAsSynced(note.id)
            }
            _statusMessage.value = "${pendingNotes.size} Notiz(en) erfolgreich mit roadmap.md synchronisiert."
        } else {
            val error = result.exceptionOrNull()?.localizedMessage ?: "Unbekannter Fehler"
            _statusMessage.value = "Sync fehlgeschlagen: $error"
            // WorkManager für Retry im Hintergrund anstoßen
            DriveSyncWorker.enqueueSync(getApplication())
        }
        _isSyncing.value = false
    }

    fun deleteNote(noteId: Long) {
        viewModelScope.launch {
            repository.deleteNote(noteId)
        }
    }

    fun clearStatusMessage() {
        _statusMessage.value = null
    }

    override fun onCleared() {
        super.onCleared()
        speechHelper?.destroy()
    }
}
