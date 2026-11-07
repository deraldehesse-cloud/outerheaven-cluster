package com.newhorizon.voiceroadmap.data

import android.content.Context
import com.google.android.gms.auth.api.signin.GoogleSignInAccount
import com.google.api.client.googleapis.extensions.android.gms.auth.GoogleAccountCredential
import com.google.api.client.http.ByteArrayContent
import com.google.api.client.http.javanet.NetHttpTransport
import com.google.api.client.json.gson.GsonFactory
import com.google.api.services.drive.Drive
import com.google.api.services.drive.DriveScopes
import com.google.api.services.drive.model.File
import com.newhorizon.voiceroadmap.data.model.VoiceNote
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.nio.charset.StandardCharsets
import java.util.Collections

class DriveRepository(private val context: Context) {

    companion object {
        const val TARGET_FOLDER_ID = "18WXPVWT-yJq8gFGbSclUEy2BEZxY6wRm"
        const val TARGET_FILE_NAME = "roadmap.md"
        const val APPLICATION_NAME = "New Horizon Voice Roadmap"
        val SCOPES = Collections.singletonList(DriveScopes.DRIVE_FILE)
    }

    fun getDriveService(account: GoogleSignInAccount): Drive {
        val credential = GoogleAccountCredential.usingOAuth2(
            context.applicationContext,
            SCOPES
        ).apply {
            selectedAccount = account.account
        }

        return Drive.Builder(
            NetHttpTransport(),
            GsonFactory.getDefaultInstance(),
            credential
        ).setApplicationName(APPLICATION_NAME).build()
    }

    suspend fun appendNotesToRoadmap(
        account: GoogleSignInAccount,
        notes: List<VoiceNote>
    ): Result<Unit> = withContext(Dispatchers.IO) {
        if (notes.isEmpty()) return@withContext Result.success(Unit)
        try {
            val service = getDriveService(account)
            val roadmapFile = getFileInFolder(service, TARGET_FOLDER_ID, TARGET_FILE_NAME)

            val newEntries = buildString {
                for (note in notes) {
                    append(note.toMarkdownEntry())
                }
            }

            if (roadmapFile == null) {
                val initialContent = "# New Horizon - Voice Roadmap\n\n$newEntries"
                createFile(service, TARGET_FOLDER_ID, TARGET_FILE_NAME, initialContent)
            } else {
                val existingContent = readFileContent(service, roadmapFile.id)
                val updatedContent = if (existingContent.endsWith("\n")) {
                    "$existingContent\n$newEntries"
                } else {
                    "$existingContent\n\n$newEntries"
                }
                updateFile(service, roadmapFile.id, updatedContent)
            }

            Result.success(Unit)
        } catch (e: Exception) {
            e.printStackTrace()
            Result.failure(e)
        }
    }

    fun getOrCreateFolder(service: Drive, folderName: String): String {
        val query = "mimeType = 'application/vnd.google-apps.folder' and name = '$folderName' and trashed = false"
        val result = service.files().list()
            .setQ(query)
            .setSpaces("drive")
            .setFields("files(id, name)")
            .execute()

        val existingFolder = result.files?.firstOrNull()
        if (existingFolder != null) {
            return existingFolder.id
        }

        val folderMetadata = File().apply {
            name = folderName
            mimeType = "application/vnd.google-apps.folder"
        }

        val folder = service.files().create(folderMetadata)
            .setFields("id")
            .execute()

        return folder.id
    }

    private fun getFileInFolder(service: Drive, folderId: String, fileName: String): File? {
        val query = "'$folderId' in parents and name = '$fileName' and trashed = false"
        val result = service.files().list()
            .setQ(query)
            .setSpaces("drive")
            .setFields("files(id, name, mimeType)")
            .execute()

        return result.files?.firstOrNull()
    }

    private fun readFileContent(service: Drive, fileId: String): String {
        val outputStream = ByteArrayOutputStream()
        service.files().get(fileId).executeMediaAndDownloadTo(outputStream)
        return outputStream.toString(StandardCharsets.UTF_8.name())
    }

    private fun createFile(service: Drive, folderId: String, fileName: String, content: String): File {
        val metadata = File().apply {
            name = fileName
            parents = listOf(folderId)
            mimeType = "text/markdown"
        }
        val mediaContent = ByteArrayContent.fromString("text/markdown; charset=UTF-8", content)
        return service.files().create(metadata, mediaContent)
            .setFields("id, name")
            .execute()
    }

    private fun updateFile(service: Drive, fileId: String, content: String): File {
        val mediaContent = ByteArrayContent.fromString("text/markdown; charset=UTF-8", content)
        return service.files().update(fileId, null, mediaContent)
            .setFields("id, name")
            .execute()
    }
}
