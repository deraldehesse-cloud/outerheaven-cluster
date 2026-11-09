package com.outerheaven.catlord.data.model

import com.google.gson.annotations.SerializedName
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

enum class SyncStatus {
    PENDING_SYNC,
    SYNCED,
    FAILED
}

data class CatlordVoiceNote(
    val id: Long = 0,
    val title: String,
    val category: String,
    val content: String,
    val timestamp: Long = System.currentTimeMillis(),
    val syncStatus: SyncStatus = SyncStatus.PENDING_SYNC
) {
    fun formattedDate(): String {
        val sdf = SimpleDateFormat("dd.MM.yyyy HH:mm", Locale.getDefault())
        return sdf.format(Date(timestamp))
    }
}

data class VoiceNotePostRequest(
    @SerializedName("title") val title: String?,
    @SerializedName("category") val category: String,
    @SerializedName("content") val content: String
)

data class VoiceNotePostResponse(
    @SerializedName("status") val status: String,
    @SerializedName("filename") val filename: String? = null,
    @SerializedName("title") val title: String? = null,
    @SerializedName("category") val category: String? = null,
    @SerializedName("local_path") val localPath: String? = null,
    @SerializedName("nfs_copied") val nfsCopied: Boolean = false,
    @SerializedName("storage_path") val storagePath: String? = null
)

data class DocItem(
    @SerializedName("folder") val folder: String,
    @SerializedName("filename") val filename: String,
    @SerializedName("relative_path") val relativePath: String,
    @SerializedName("title") val title: String,
    @SerializedName("size_bytes") val sizeBytes: Long,
    @SerializedName("size_formatted") val sizeFormatted: String,
    @SerializedName("modified_at") val modifiedAt: String
)

data class DocsListResponse(
    @SerializedName("count") val count: Int,
    @SerializedName("docs") val docs: List<DocItem>
)

data class DocDetailResponse(
    @SerializedName("folder") val folder: String,
    @SerializedName("filename") val filename: String,
    @SerializedName("relative_path") val relativePath: String,
    @SerializedName("content") val content: String,
    @SerializedName("size_bytes") val sizeBytes: Long,
    @SerializedName("size_formatted") val sizeFormatted: String,
    @SerializedName("modified_at") val modifiedAt: String
)

data class MediaTotals(
    @SerializedName("elliot_images") val elliotImages: Int = 0,
    @SerializedName("elliot_videos") val elliotVideos: Int = 0,
    @SerializedName("kaech_images") val kaechImages: Int = 0,
    @SerializedName("kaech_videos") val kaechVideos: Int = 0,
    @SerializedName("total_images") val totalImages: Int = 0,
    @SerializedName("total_videos") val totalVideos: Int = 0
)

data class MediaFolderDetail(
    @SerializedName("name") val name: String,
    @SerializedName("relative_path") val relativePath: String,
    @SerializedName("images") val images: Int,
    @SerializedName("videos") val videos: Int,
    @SerializedName("size_formatted") val sizeFormatted: String
)

data class MediaCategoryDetail(
    @SerializedName("exists") val exists: Boolean = false,
    @SerializedName("images") val images: Int = 0,
    @SerializedName("videos") val videos: Int = 0,
    @SerializedName("total_files") val totalFiles: Int = 0,
    @SerializedName("size_formatted") val sizeFormatted: String = "0 B",
    @SerializedName("subfolders") val subfolders: List<MediaFolderDetail> = emptyList()
)

data class MediaStatsResponse(
    @SerializedName("media_dir") val mediaDir: String? = null,
    @SerializedName("accessible") val accessible: Boolean = false,
    @SerializedName("totals") val totals: MediaTotals = MediaTotals(),
    @SerializedName("details") val details: Map<String, MediaCategoryDetail>? = null
)

data class ClusterStatusResponse(
    @SerializedName("node") val node: String? = null,
    @SerializedName("timestamp") val timestamp: String? = null,
    @SerializedName("hostname") val hostname: String? = null,
    @SerializedName("status") val status: String? = null,
    @SerializedName("uptime_seconds") val uptimeSeconds: Double? = null
)

data class FindingsResponse(
    @SerializedName("package") val pkg: String? = null,
    @SerializedName("filename") val filename: String? = null,
    @SerializedName("content") val content: String? = null,
    @SerializedName("file_path") val filePath: String? = null,
    @SerializedName("modified_at") val modifiedAt: String? = null,
    @SerializedName("available_packages") val availablePackages: List<String> = emptyList()
)

data class DropzoneFile(
    @SerializedName("name") val name: String = "",
    @SerializedName("size_bytes") val sizeBytes: Long = 0L,
    @SerializedName("size_formatted") val sizeFormatted: String = "",
    @SerializedName("modified_at") val modifiedAt: String = "",
    @SerializedName("is_apk") val isApk: Boolean = true
)

data class DropzoneResponse(
    @SerializedName("dropzone_dir") val dropzoneDir: String = "",
    @SerializedName("count") val count: Int = 0,
    @SerializedName("apk_count") val apkCount: Int = 0,
    @SerializedName("files") val files: List<DropzoneFile> = emptyList()
)

data class DecompileRequest(
    @SerializedName("apk_name") val apkName: String,
    @SerializedName("target_subfolder") val targetSubfolder: String? = null,
    @SerializedName("filter_keyword") val filterKeyword: String? = null
)

data class DecompileResponse(
    @SerializedName("status") val status: String = "",
    @SerializedName("message") val message: String = ""
)

data class AskDeepSeekRequest(
    @SerializedName("prompt") val prompt: String,
    @SerializedName("model") val model: String = "catlord-hermes:latest",
    @SerializedName("package") val pkg: String? = null,
    @SerializedName("include_lore") val includeLore: Boolean = false,
    @SerializedName("save_to_findings") val saveToFindings: Boolean = false,
    @SerializedName("save_to_drafts") val saveToDrafts: Boolean = true
)

data class AskDeepSeekResponse(
    @SerializedName("status") val status: String = "",
    @SerializedName("model") val model: String? = null,
    @SerializedName("package") val pkg: String? = null,
    @SerializedName("prompt") val prompt: String = "",
    @SerializedName("response") val response: String = "",
    @SerializedName("include_lore") val includeLore: Boolean = false,
    @SerializedName("saved_to_findings") val savedToFindings: Boolean = false,
    @SerializedName("saved_to_drafts") val savedToDrafts: Boolean = false,
    @SerializedName("file_name") val fileName: String? = null,
    @SerializedName("file_path") val filePath: String? = null,
    @SerializedName("timestamp") val timestamp: String = ""
)

data class CatlordAgentInfo(
    val id: String,
    val name: String,
    val title: String,
    val icon: String,
    val description: String
)
