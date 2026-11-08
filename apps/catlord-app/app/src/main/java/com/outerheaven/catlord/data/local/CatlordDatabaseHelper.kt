package com.outerheaven.catlord.data.local

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.outerheaven.catlord.data.model.CatlordVoiceNote
import com.outerheaven.catlord.data.model.SyncStatus

class CatlordDatabaseHelper(context: Context) :
    SQLiteOpenHelper(context, DATABASE_NAME, null, DATABASE_VERSION) {

    companion object {
        const val DATABASE_NAME = "catlord_studio.db"
        const val DATABASE_VERSION = 1

        const val TABLE_NAME = "catlord_voice_notes"
        const val COLUMN_ID = "id"
        const val COLUMN_TITLE = "title"
        const val COLUMN_CATEGORY = "category"
        const val COLUMN_CONTENT = "content"
        const val COLUMN_TIMESTAMP = "timestamp"
        const val COLUMN_SYNC_STATUS = "sync_status"
    }

    override fun onCreate(db: SQLiteDatabase) {
        val createTableQuery = """
            CREATE TABLE $TABLE_NAME (
                $COLUMN_ID INTEGER PRIMARY KEY AUTOINCREMENT,
                $COLUMN_TITLE TEXT NOT NULL,
                $COLUMN_CATEGORY TEXT NOT NULL,
                $COLUMN_CONTENT TEXT NOT NULL,
                $COLUMN_TIMESTAMP INTEGER NOT NULL,
                $COLUMN_SYNC_STATUS TEXT NOT NULL
            )
        """.trimIndent()
        db.execSQL(createTableQuery)
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        db.execSQL("DROP TABLE IF EXISTS $TABLE_NAME")
        onCreate(db)
    }

    fun insertNote(note: CatlordVoiceNote): Long {
        val db = writableDatabase
        val values = ContentValues().apply {
            put(COLUMN_TITLE, note.title)
            put(COLUMN_CATEGORY, note.category)
            put(COLUMN_CONTENT, note.content)
            put(COLUMN_TIMESTAMP, note.timestamp)
            put(COLUMN_SYNC_STATUS, note.syncStatus.name)
        }
        return db.insert(TABLE_NAME, null, values)
    }

    fun getAllNotes(): List<CatlordVoiceNote> {
        val notes = mutableListOf<CatlordVoiceNote>()
        val db = readableDatabase
        val cursor: Cursor = db.query(
            TABLE_NAME,
            null,
            null,
            null,
            null,
            null,
            "$COLUMN_TIMESTAMP DESC"
        )
        cursor.use {
            val idIndex = it.getColumnIndexOrThrow(COLUMN_ID)
            val titleIndex = it.getColumnIndexOrThrow(COLUMN_TITLE)
            val catIndex = it.getColumnIndexOrThrow(COLUMN_CATEGORY)
            val contentIndex = it.getColumnIndexOrThrow(COLUMN_CONTENT)
            val timestampIndex = it.getColumnIndexOrThrow(COLUMN_TIMESTAMP)
            val statusIndex = it.getColumnIndexOrThrow(COLUMN_SYNC_STATUS)

            while (it.moveToNext()) {
                val statusString = it.getString(statusIndex)
                val status = try {
                    SyncStatus.valueOf(statusString)
                } catch (e: Exception) {
                    SyncStatus.PENDING_SYNC
                }
                notes.add(
                    CatlordVoiceNote(
                        id = it.getLong(idIndex),
                        title = it.getString(titleIndex),
                        category = it.getString(catIndex),
                        content = it.getString(contentIndex),
                        timestamp = it.getLong(timestampIndex),
                        syncStatus = status
                    )
                )
            }
        }
        return notes
    }

    fun getPendingNotes(): List<CatlordVoiceNote> {
        val notes = mutableListOf<CatlordVoiceNote>()
        val db = readableDatabase
        val cursor: Cursor = db.query(
            TABLE_NAME,
            null,
            "$COLUMN_SYNC_STATUS != ?",
            arrayOf(SyncStatus.SYNCED.name),
            null,
            null,
            "$COLUMN_TIMESTAMP ASC"
        )
        cursor.use {
            val idIndex = it.getColumnIndexOrThrow(COLUMN_ID)
            val titleIndex = it.getColumnIndexOrThrow(COLUMN_TITLE)
            val catIndex = it.getColumnIndexOrThrow(COLUMN_CATEGORY)
            val contentIndex = it.getColumnIndexOrThrow(COLUMN_CONTENT)
            val timestampIndex = it.getColumnIndexOrThrow(COLUMN_TIMESTAMP)

            while (it.moveToNext()) {
                notes.add(
                    CatlordVoiceNote(
                        id = it.getLong(idIndex),
                        title = it.getString(titleIndex),
                        category = it.getString(catIndex),
                        content = it.getString(contentIndex),
                        timestamp = it.getLong(timestampIndex),
                        syncStatus = SyncStatus.PENDING_SYNC
                    )
                )
            }
        }
        return notes
    }

    fun updateSyncStatus(noteId: Long, status: SyncStatus): Int {
        val db = writableDatabase
        val values = ContentValues().apply {
            put(COLUMN_SYNC_STATUS, status.name)
        }
        return db.update(TABLE_NAME, values, "$COLUMN_ID = ?", arrayOf(noteId.toString()))
    }

    fun deleteNote(noteId: Long): Int {
        val db = writableDatabase
        return db.delete(TABLE_NAME, "$COLUMN_ID = ?", arrayOf(noteId.toString()))
    }
}
