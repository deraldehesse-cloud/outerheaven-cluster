package com.newhorizon.voiceroadmap.data

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.google.android.gms.auth.api.signin.GoogleSignIn
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class DriveSyncWorker(
    private val context: Context,
    workerParams: WorkerParameters
) : CoroutineWorker(context, workerParams) {

    companion object {
        const val WORK_NAME = "drive_sync_worker"

        fun enqueueSync(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()

            val syncRequest = OneTimeWorkRequestBuilder<DriveSyncWorker>()
                .setConstraints(constraints)
                .build()

            WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(
                WORK_NAME,
                ExistingWorkPolicy.REPLACE,
                syncRequest
            )
        }
    }

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val account = GoogleSignIn.getLastSignedInAccount(context)
        if (account == null) {
            // Noch kein Google-Konto autorisiert -> Später versuchen wenn eingeloggt
            return@withContext Result.retry()
        }

        val repository = VoiceNoteRepository.getInstance(context)
        val pendingNotes = repository.getPendingNotes()

        if (pendingNotes.isEmpty()) {
            return@withContext Result.success()
        }

        val driveRepo = DriveRepository(context)
        val result = driveRepo.appendNotesToRoadmap(account, pendingNotes)

        if (result.isSuccess) {
            for (note in pendingNotes) {
                repository.markAsSynced(note.id)
            }
            Result.success()
        } else {
            result.exceptionOrNull()?.printStackTrace()
            Result.retry()
        }
    }
}
