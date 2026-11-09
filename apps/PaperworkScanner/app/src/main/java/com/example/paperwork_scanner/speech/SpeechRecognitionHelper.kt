package com.example.paperwork_scanner.speech

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import java.util.Locale

sealed class SpeechState {
    object Ready : SpeechState()
    object Listening : SpeechState()
    object Processing : SpeechState()
    data class Error(val errorMessage: String) : SpeechState()
}

class SpeechRecognitionHelper(
    private val context: Context,
    private val onStateChanged: (SpeechState) -> Unit,
    private val onPartialResult: (String) -> Unit,
    private val onFinalResult: (String) -> Unit
) {
    private var speechRecognizer: SpeechRecognizer? = null

    val isAvailable: Boolean
        get() = SpeechRecognizer.isRecognitionAvailable(context)

    fun startListening(languageLocale: Locale) {
        startListening(if (languageLocale.language == "de") "de-DE" else languageLocale.toLanguageTag())
    }

    fun startListening(languageCode: String = "de-DE") {
        if (!isAvailable) {
            onStateChanged(SpeechState.Error("Spracherkennung auf diesem Gerät nicht verfügbar."))
            return
        }

        destroy()

        speechRecognizer = SpeechRecognizer.createSpeechRecognizer(context).apply {
            setRecognitionListener(object : RecognitionListener {
                override fun onReadyForSpeech(params: Bundle?) {
                    onStateChanged(SpeechState.Listening)
                }

                override fun onBeginningOfSpeech() {
                    onStateChanged(SpeechState.Listening)
                }

                override fun onRmsChanged(rmsdB: Float) {}

                override fun onBufferReceived(buffer: ByteArray?) {}

                override fun onEndOfSpeech() {
                    onStateChanged(SpeechState.Processing)
                }

                override fun onError(error: Int) {
                    val message = getErrorMessage(error)
                    onStateChanged(SpeechState.Error(message))
                }

                override fun onResults(results: Bundle?) {
                    val matches = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    val resultText = matches?.firstOrNull() ?: ""
                    onFinalResult(resultText)
                    onStateChanged(SpeechState.Ready)
                }

                override fun onPartialResults(partialResults: Bundle?) {
                    val matches = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    val partialText = matches?.firstOrNull() ?: ""
                    if (partialText.isNotBlank()) {
                        onPartialResult(partialText)
                    }
                }

                override fun onEvent(eventType: Int, params: Bundle?) {}
            })
        }

        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, languageCode)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, languageCode)
            putExtra(RecognizerIntent.EXTRA_ONLY_RETURN_LANGUAGE_PREFERENCE, languageCode)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
        }

        speechRecognizer?.startListening(intent)
        onStateChanged(SpeechState.Listening)
    }

    fun stopListening() {
        speechRecognizer?.stopListening()
        onStateChanged(SpeechState.Processing)
    }

    fun cancel() {
        speechRecognizer?.cancel()
        onStateChanged(SpeechState.Ready)
    }

    fun destroy() {
        speechRecognizer?.destroy()
        speechRecognizer = null
    }

    private fun getErrorMessage(errorCode: Int): String {
        return when (errorCode) {
            SpeechRecognizer.ERROR_AUDIO -> "Audio-Aufnahmefehler"
            SpeechRecognizer.ERROR_CLIENT -> "Client-Fehler"
            SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Fehlende Audio-Berechtigung"
            SpeechRecognizer.ERROR_NETWORK -> "Netzwerkfehler"
            SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "Netzwerk-Timeout"
            SpeechRecognizer.ERROR_NO_MATCH -> "Keine Sprache erkannt"
            SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "Spracherkennung ausgelastet"
            SpeechRecognizer.ERROR_SERVER -> "Serverfehler"
            SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "Keine Spracheingabe empfangen"
            else -> "Unerwarteter Fehler ($errorCode)"
        }
    }
}
