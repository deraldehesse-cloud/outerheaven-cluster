package com.outerheaven.webidecompanion.ui.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.outerheaven.webidecompanion.data.api.MetalBridgeApi
import com.outerheaven.webidecompanion.data.model.*
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

sealed class UiState<out T> {
    object Idle : UiState<Nothing>()
    object Loading : UiState<Nothing>()
    data class Success<T>(val data: T) : UiState<T>()
    data class Error(val message: String) : UiState<Nothing>()
}

class MainViewModel(
    private val api: MetalBridgeApi = MetalBridgeApi.create()
) : ViewModel() {

    // Status & Dashboard
    private val _statusState = MutableStateFlow<UiState<ClusterStatusResponse>>(UiState.Loading)
    val statusState: StateFlow<UiState<ClusterStatusResponse>> = _statusState.asStateFlow()

    private val _triggerState = MutableStateFlow<UiState<TriggerResponse>>(UiState.Idle)
    val triggerState: StateFlow<UiState<TriggerResponse>> = _triggerState.asStateFlow()

    // Dropzone
    private val _dropzoneState = MutableStateFlow<UiState<DropzoneResponse>>(UiState.Loading)
    val dropzoneState: StateFlow<UiState<DropzoneResponse>> = _dropzoneState.asStateFlow()

    // Findings
    private val _findingsState = MutableStateFlow<UiState<FindingsResponse>>(UiState.Loading)
    val findingsState: StateFlow<UiState<FindingsResponse>> = _findingsState.asStateFlow()

    private val _selectedPackage = MutableStateFlow<String?>(null)
    val selectedPackage: StateFlow<String?> = _selectedPackage.asStateFlow()

    // REA-Sandbox States
    private val _sandboxStatusState = MutableStateFlow<UiState<SandboxStatus>>(UiState.Loading)
    val sandboxStatusState: StateFlow<UiState<SandboxStatus>> = _sandboxStatusState.asStateFlow()

    private val _sandboxJobsState = MutableStateFlow<UiState<SandboxJobsResponse>>(UiState.Loading)
    val sandboxJobsState: StateFlow<UiState<SandboxJobsResponse>> = _sandboxJobsState.asStateFlow()

    private val _sandboxOutputsState = MutableStateFlow<UiState<SandboxOutputsResponse>>(UiState.Loading)
    val sandboxOutputsState: StateFlow<UiState<SandboxOutputsResponse>> = _sandboxOutputsState.asStateFlow()

    private val _restartContainerState = MutableStateFlow<UiState<RestartContainerResponse>>(UiState.Idle)
    val restartContainerState: StateFlow<UiState<RestartContainerResponse>> = _restartContainerState.asStateFlow()

    private val _decompileState = MutableStateFlow<UiState<DecompileResponse>>(UiState.Idle)
    val decompileState: StateFlow<UiState<DecompileResponse>> = _decompileState.asStateFlow()

    private var pollingJob: Job? = null

    init {
        refreshAll()
        startPolling()
    }

    fun startPolling() {
        pollingJob?.cancel()
        pollingJob = viewModelScope.launch {
            while (isActive) {
                delay(6000)
                loadStatusSilent()
                loadSandboxStatusSilent()
            }
        }
    }

    fun refreshAll() {
        loadStatus()
        loadDropzone()
        loadFindings(_selectedPackage.value)
        loadSandboxData()
    }

    fun loadStatus() {
        viewModelScope.launch {
            _statusState.value = UiState.Loading
            try {
                val res = api.getStatus()
                _statusState.value = UiState.Success(res)
            } catch (e: Exception) {
                _statusState.value = UiState.Error(e.localizedMessage ?: "Verbindungsfehler zu metal-dgear")
            }
        }
    }

    private suspend fun loadStatusSilent() {
        try {
            val res = api.getStatus()
            _statusState.value = UiState.Success(res)
        } catch (_: Exception) {}
    }

    fun loadDropzone() {
        viewModelScope.launch {
            _dropzoneState.value = UiState.Loading
            try {
                val res = api.getDropzone()
                _dropzoneState.value = UiState.Success(res)
            } catch (e: Exception) {
                _dropzoneState.value = UiState.Error(e.localizedMessage ?: "Dropzone konnte nicht geladen werden")
            }
        }
    }

    fun loadFindings(pkg: String? = null) {
        viewModelScope.launch {
            _findingsState.value = UiState.Loading
            try {
                val res = api.getFindings(pkg)
                _findingsState.value = UiState.Success(res)
                if (pkg != null) {
                    _selectedPackage.value = pkg
                } else if (res.packageName.isNotEmpty()) {
                    _selectedPackage.value = res.packageName
                }
            } catch (e: Exception) {
                _findingsState.value = UiState.Error(e.localizedMessage ?: "Findings nicht gefunden")
            }
        }
    }

    fun selectPackage(pkg: String) {
        _selectedPackage.value = pkg
        loadFindings(pkg)
    }

    fun triggerPipeline() {
        viewModelScope.launch {
            _triggerState.value = UiState.Loading
            try {
                val res = api.triggerPipeline()
                _triggerState.value = UiState.Success(res)
                loadStatus()
            } catch (e: Exception) {
                _triggerState.value = UiState.Error(e.localizedMessage ?: "Pipeline konnte nicht gestartet werden")
            }
        }
    }

    // ==========================================
    // REA-Sandbox Aktionen
    // ==========================================

    fun loadSandboxData() {
        loadSandboxStatus()
        loadSandboxJobs()
        loadSandboxOutputs()
    }

    fun loadSandboxStatus() {
        viewModelScope.launch {
            _sandboxStatusState.value = UiState.Loading
            try {
                val res = api.getSandboxStatus()
                _sandboxStatusState.value = UiState.Success(res)
            } catch (e: Exception) {
                _sandboxStatusState.value = UiState.Error(e.localizedMessage ?: "Sandbox Status nicht erreichbar")
            }
        }
    }

    private suspend fun loadSandboxStatusSilent() {
        try {
            val res = api.getSandboxStatus()
            _sandboxStatusState.value = UiState.Success(res)
        } catch (_: Exception) {}
    }

    fun loadSandboxJobs() {
        viewModelScope.launch {
            _sandboxJobsState.value = UiState.Loading
            try {
                val res = api.getSandboxJobs()
                _sandboxJobsState.value = UiState.Success(res)
            } catch (e: Exception) {
                _sandboxJobsState.value = UiState.Error(e.localizedMessage ?: "Jobs nicht abrufbar")
            }
        }
    }

    fun loadSandboxOutputs() {
        viewModelScope.launch {
            _sandboxOutputsState.value = UiState.Loading
            try {
                val res = api.getSandboxOutputs()
                _sandboxOutputsState.value = UiState.Success(res)
            } catch (e: Exception) {
                _sandboxOutputsState.value = UiState.Error(e.localizedMessage ?: "Outputs nicht abrufbar")
            }
        }
    }

    fun restartSandboxContainer() {
        viewModelScope.launch {
            _restartContainerState.value = UiState.Loading
            try {
                val res = api.restartSandboxContainer()
                _restartContainerState.value = UiState.Success(res)
                delay(1500)
                loadSandboxStatus()
            } catch (e: Exception) {
                _restartContainerState.value = UiState.Error(e.localizedMessage ?: "Neustart fehlgeschlagen")
            }
        }
    }

    fun decompileApk(apkName: String, filterKeyword: String? = null) {
        viewModelScope.launch {
            _decompileState.value = UiState.Loading
            try {
                val kw = if (filterKeyword.isNullOrBlank()) null else filterKeyword.trim()
                val res = api.decompileApk(DecompileRequest(apk_name = apkName, filter_keyword = kw))
                _decompileState.value = UiState.Success(res)
                loadSandboxJobs()
            } catch (e: Exception) {
                _decompileState.value = UiState.Error(e.localizedMessage ?: "Dekompilierung fehlgeschlagen")
            }
        }
    }

    fun resetDecompileState() {
        _decompileState.value = UiState.Idle
    }

    override fun onCleared() {
        super.onCleared()
        pollingJob?.cancel()
    }
}
