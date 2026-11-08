package com.outerheaven.webidecompanion.data.model

import com.google.gson.annotations.SerializedName

// ==========================================
// REA-Sandbox Spezifische Datenmodelle
// ==========================================

data class SandboxStatus(
    val container_name: String = "rea-sandbox",
    val status: String = "unknown",
    val is_running: Boolean = false,
    val started_at: String = "",
    val pid: Int = 0,
    val image: String = "",
    val jadx_version: String = "",
    val error: String? = null
)

data class SandboxJob(
    val id: String = "",
    val apk_name: String = "",
    val target_dir: String = "",
    val filter_keyword: String? = null,
    val status: String = "running",
    val start_time: String = "",
    val end_time: String? = null,
    val exit_code: Int? = null,
    val log_tail: String = ""
)

data class SandboxJobsResponse(
    val count: Int = 0,
    val jobs: List<SandboxJob> = emptyList()
)

data class SandboxOutputFolder(
    val name: String = "",
    val relative_path: String = "",
    val file_count: Int = 0,
    val size_bytes: Long = 0L,
    val size_formatted: String = "",
    val modified_at: String = "",
    val has_sources: Boolean = false,
    val has_findings: Boolean = false
)

data class SandboxOutputsResponse(
    val output_dir: String = "",
    val count: Int = 0,
    val folders: List<SandboxOutputFolder> = emptyList()
)

data class DecompileRequest(
    val apk_name: String,
    val target_subfolder: String? = null,
    val filter_keyword: String? = null
)

data class DecompileResponse(
    val status: String = "",
    val message: String = "",
    val job: SandboxJob = SandboxJob()
)

data class RestartContainerResponse(
    val status: String = "",
    val message: String = "",
    val timestamp: String = ""
)
