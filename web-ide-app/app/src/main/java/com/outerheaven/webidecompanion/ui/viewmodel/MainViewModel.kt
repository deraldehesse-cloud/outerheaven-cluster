package com.outerheaven.webidecompanion.ui.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.outerheaven.webidecompanion.data.api.MetalBridgeApi
import com.outerheaven.webidecompanion.data.model.*
import retrofit2.HttpException
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

    private val _availablePackages = MutableStateFlow<List<String>>(emptyList())
    val availablePackages: StateFlow<List<String>> = _availablePackages.asStateFlow()

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
        loadSandboxData()
        val currentPkg = _selectedPackage.value
        if (currentPkg != null) {
            loadFindings(currentPkg)
        }
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

    private fun updateAvailablePackages(newPkgs: List<String>) {
        val current = _availablePackages.value.toMutableList()
        var changed = false
        for (p in newPkgs) {
            val clean = p.trim().removeSuffix(".apk")
            if (clean.isNotBlank() && clean != "catlord" && !current.contains(clean)) {
                current.add(clean)
                changed = true
            }
        }
        if (changed) {
            _availablePackages.value = current
        }
    }

    fun loadDropzone() {
        viewModelScope.launch {
            _dropzoneState.value = UiState.Loading
            try {
                val res = api.getDropzone()
                _dropzoneState.value = UiState.Success(res)
                val apkPkgs = res.files.filter { it.is_apk }.map { it.name.removeSuffix(".apk") }
                updateAvailablePackages(apkPkgs)
            } catch (e: Exception) {
                _dropzoneState.value = UiState.Error(e.localizedMessage ?: "Dropzone konnte nicht geladen werden")
            }
        }
    }

    fun loadFindings(pkg: String? = null) {
        if (pkg != null) {
            _selectedPackage.value = pkg
            updateAvailablePackages(listOf(pkg))
        }
        val targetPkg = pkg ?: _selectedPackage.value
        viewModelScope.launch {
            _findingsState.value = UiState.Loading
            try {
                val res = api.getFindings(targetPkg)
                _findingsState.value = UiState.Success(res)
                if (_selectedPackage.value == null && res.packageName.isNotEmpty()) {
                    _selectedPackage.value = res.packageName
                }
                updateAvailablePackages(res.available_packages)
            } catch (e: HttpException) {
                if (e.code() == 404) {
                    _findingsState.value = UiState.Error("NOT_FOUND")
                } else {
                    _findingsState.value = UiState.Error(e.localizedMessage ?: "Fehler beim Laden (${e.code()})")
                }
            } catch (e: Exception) {
                val msg = e.localizedMessage ?: "Findings nicht gefunden"
                if (msg.contains("404")) {
                    _findingsState.value = UiState.Error("NOT_FOUND")
                } else {
                    _findingsState.value = UiState.Error(msg)
                }
            }
        }
    }

    fun selectPackage(pkg: String) {
        val clean = pkg.trim().removeSuffix(".apk")
        _selectedPackage.value = clean
        updateAvailablePackages(listOf(clean))
        loadFindings(clean)
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
        loadDropzone()
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
                val folderPkgs = res.folders.map { it.name }.filter { it.isNotBlank() && it != "catlord" }
                updateAvailablePackages(folderPkgs)

                // Beim ersten Laden bevorzugt das neueste Paket auswählen, das has_findings == true besitzt
                if (_selectedPackage.value == null) {
                    val candidateFolders = res.folders.filter { it.name != "catlord" }
                    val preferredPkg = candidateFolders.firstOrNull { it.has_findings }?.name
                        ?: candidateFolders.firstOrNull()?.name
                    if (preferredPkg != null) {
                        selectPackage(preferredPkg)
                    } else {
                        loadFindings(null)
                    }
                }
            } catch (e: Exception) {
                _sandboxOutputsState.value = UiState.Error(e.localizedMessage ?: "Outputs nicht abrufbar")
                if (_selectedPackage.value == null) {
                    loadFindings(null)
                }
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
