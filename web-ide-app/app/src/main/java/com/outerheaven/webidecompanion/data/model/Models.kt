package com.outerheaven.webidecompanion.data.model

import com.google.gson.annotations.SerializedName

data class ClusterStatusResponse(
    val node: String = "",
    val timestamp: String = "",
    val cpu: CpuInfo = CpuInfo(),
    val ram: RamInfo = RamInfo(),
    val disk: DiskInfo? = null,
    val services: ServicesInfo = ServicesInfo(),
    val pipeline: PipelineInfo = PipelineInfo()
)

data class CpuInfo(
    val percent: Float = 0f,
    val cores: Int = 0
)

data class RamInfo(
    val total_bytes: Long = 0L,
    val used_bytes: Long = 0L,
    val available_bytes: Long = 0L,
    val percent: Float = 0f,
    val total_formatted: String = "",
    val used_formatted: String = ""
)

data class DiskInfo(
    val total_bytes: Long = 0L,
    val used_bytes: Long = 0L,
    val free_bytes: Long = 0L,
    val percent: Float = 0f,
    val free_formatted: String = ""
)

data class ServicesInfo(
    val code_server: ServiceDetail = ServiceDetail(),
    val ollama: OllamaDetail = OllamaDetail(),
    val rea_sandbox: SandboxStatus? = null
)

data class ServiceDetail(
    val unit: String = "",
    val status: String = "unknown",
    val is_active: Boolean = false,
    val port: Int = 8443,
    val url: String = "http://100.96.88.84:8443"
)

data class OllamaDetail(
    val unit: String = "",
    val status: String = "unknown",
    val is_active: Boolean = false,
    val reachable: Boolean = false,
    val port: Int = 11434,
    val active_model: String = "",
    val running_models: List<String> = emptyList(),
    val installed_models: List<String> = emptyList()
)

data class PipelineInfo(
    val is_running: Boolean = false,
    val start_time: String? = null,
    val end_time: String? = null,
    val last_exit_code: Int? = null
)

data class DropzoneResponse(
    val dropzone_dir: String = "",
    val count: Int = 0,
    val apk_count: Int = 0,
    val files: List<DropzoneFile> = emptyList()
)

data class DropzoneFile(
    val name: String = "",
    val size_bytes: Long = 0L,
    val size_formatted: String = "",
    val modified_at: String = "",
    val is_apk: Boolean = true
)

data class TriggerResponse(
    val status: String = "",
    val message: String = "",
    val pid: Int? = null,
    val start_time: String? = null,
    val log_file: String? = null
)

data class PipelineStatusResponse(
    val is_running: Boolean = false,
    val start_time: String? = null,
    val end_time: String? = null,
    val last_exit_code: Int? = null,
    val log_tail: String = ""
)

data class FindingsResponse(
    @SerializedName("package")
    val packageName: String = "",
    val filename: String = "",
    val file_path: String = "",
    val modified_at: String = "",
    val content: String = "",
    val available_packages: List<String> = emptyList()
)
