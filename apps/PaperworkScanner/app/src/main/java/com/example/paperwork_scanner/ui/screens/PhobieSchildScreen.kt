package com.example.paperwork_scanner.ui.screens

import android.widget.Toast
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Archive
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Security
import androidx.compose.material.icons.filled.Send
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.paperwork_scanner.tailscale.TailscaleManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

data class PhobieReviewItem(
    val id: Int,
    val sender: String,
    val subject: String,
    val receivedAt: String,
    val summary: String,
    val category: String,
    val confidence: Double,
    val attachmentName: String?,
    val attachmentSize: Long,
    val status: String,
    val draftResponse: String,
    val creditorEmail: String,
    val calmSummary: String,
    val referenceNumber: String,
    val amount: String
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PhobieSchildScreen(
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current
    val coroutineScope = rememberCoroutineScope()

    var reviewItems by remember { mutableStateOf<List<PhobieReviewItem>>(emptyList()) }
    var isLoading by remember { mutableStateOf(false) }
    var isPolling by remember { mutableStateOf(false) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    var activeActionItemId by remember { mutableStateOf<Int?>(null) }
    var confirmSendItem by remember { mutableStateOf<Pair<PhobieReviewItem, String>?>(null) }

    val okHttpClient = remember {
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(25, TimeUnit.SECONDS)
            .writeTimeout(25, TimeUnit.SECONDS)
            .build()
    }

    fun loadReviews(showToast: Boolean = false) {
        coroutineScope.launch {
            isLoading = true
            errorMessage = null
            try {
                val baseUrl = withContext(Dispatchers.IO) { TailscaleManager.getHermesBaseUrl() }
                val request = Request.Builder()
                    .url("$baseUrl/api/email_review")
                    .get()
                    .build()

                val response = withContext(Dispatchers.IO) { okHttpClient.newCall(request).execute() }
                val code = response.code
                val bodyStr = response.body?.string().orEmpty()
                response.close()

                if (code == 200) {
                    val json = JSONObject(bodyStr)
                    val array: JSONArray = json.optJSONArray("items") ?: JSONArray()
                    val parsed = mutableListOf<PhobieReviewItem>()
                    for (i in 0 until array.length()) {
                        val obj = array.getJSONObject(i)
                        parsed.add(
                            PhobieReviewItem(
                                id = obj.getInt("id"),
                                sender = obj.optString("sender", "Unbekannt"),
                                subject = obj.optString("subject", "(Kein Betreff)"),
                                receivedAt = obj.optString("received_at", ""),
                                summary = obj.optString("summary", ""),
                                category = obj.optString("category", "REVIEW"),
                                confidence = obj.optDouble("confidence", 0.5),
                                attachmentName = if (obj.has("attachment_name") && !obj.isNull("attachment_name")) obj.getString("attachment_name") else null,
                                attachmentSize = obj.optLong("attachment_size", 0L),
                                status = obj.optString("status", "pending"),
                                draftResponse = obj.optString("draft_response", ""),
                                creditorEmail = obj.optString("creditor_email", ""),
                                calmSummary = obj.optString("calm_summary", ""),
                                referenceNumber = obj.optString("reference_number", ""),
                                amount = obj.optString("amount", "")
                            )
                        )
                    }
                    reviewItems = parsed
                    if (showToast) {
                        Toast.makeText(context, "${parsed.size} Dokument(e) in Quarantäne", Toast.LENGTH_SHORT).show()
                    }
                } else {
                    errorMessage = "Serverfehler: HTTP $code"
                }
            } catch (e: Exception) {
                errorMessage = "Verbindung zu Hermes fehlgeschlagen: ${e.localizedMessage}"
            } finally {
                isLoading = false
            }
        }
    }

    fun triggerPoll() {
        coroutineScope.launch {
            isPolling = true
            try {
                val baseUrl = withContext(Dispatchers.IO) { TailscaleManager.getHermesBaseUrl() }
                val request = Request.Builder()
                    .url("$baseUrl/api/email_review/poll")
                    .post("{}".toRequestBody("application/json".toMediaTypeOrNull()))
                    .build()

                val response = withContext(Dispatchers.IO) { okHttpClient.newCall(request).execute() }
                val code = response.code
                val bodyStr = response.body?.string().orEmpty()
                response.close()

                if (code == 200) {
                    val json = JSONObject(bodyStr)
                    val processed = json.optInt("processed_emails", 0)
                    Toast.makeText(context, "Postfach geprüft ($processed neue E-Mails)", Toast.LENGTH_SHORT).show()
                    loadReviews()
                } else {
                    Toast.makeText(context, "Prüfung fehlgeschlagen (HTTP $code)", Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                Toast.makeText(context, "Fehler beim Abruf: ${e.localizedMessage}", Toast.LENGTH_LONG).show()
            } finally {
                isPolling = false
            }
        }
    }

    fun approveAndSendDraft(item: PhobieReviewItem, editedDraft: String) {
        coroutineScope.launch {
            activeActionItemId = item.id
            try {
                val baseUrl = withContext(Dispatchers.IO) { TailscaleManager.getHermesBaseUrl() }
                val payload = JSONObject().apply {
                    put("draft_response", editedDraft)
                    if (item.creditorEmail.isNotBlank()) {
                        put("recipient", item.creditorEmail)
                    }
                }
                val requestBody = payload.toString().toRequestBody("application/json".toMediaTypeOrNull())
                val request = Request.Builder()
                    .url("$baseUrl/api/reviews/${item.id}/approve-send")
                    .post(requestBody)
                    .build()

                val response = withContext(Dispatchers.IO) { okHttpClient.newCall(request).execute() }
                val code = response.code
                val bodyStr = response.body?.string().orEmpty()
                response.close()

                if (code in 200..299) {
                    reviewItems = reviewItems.filter { it.id != item.id }
                    Toast.makeText(context, "✉️ Fristverlängerung erfolgreich versendet!", Toast.LENGTH_LONG).show()
                } else {
                    Toast.makeText(context, "Fehler beim Senden (HTTP $code): ${bodyStr.take(100)}", Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                Toast.makeText(context, "Übertragungsfehler: ${e.localizedMessage}", Toast.LENGTH_LONG).show()
            } finally {
                activeActionItemId = null
                confirmSendItem = null
            }
        }
    }

    fun executeAction(item: PhobieReviewItem, action: String) {
        coroutineScope.launch {
            activeActionItemId = item.id
            try {
                val baseUrl = withContext(Dispatchers.IO) { TailscaleManager.getHermesBaseUrl() }
                val payload = JSONObject().apply {
                    put("action", action)
                    put("title", item.subject)
                }
                val requestBody = payload.toString().toRequestBody("application/json".toMediaTypeOrNull())
                val request = Request.Builder()
                    .url("$baseUrl/api/email_review/${item.id}/action")
                    .post(requestBody)
                    .build()

                val response = withContext(Dispatchers.IO) { okHttpClient.newCall(request).execute() }
                val code = response.code
                response.close()

                if (code in 200..299) {
                    reviewItems = reviewItems.filter { it.id != item.id }
                    val actionName = if (action == "DELETE") "gelöscht" else "in Paperless archiviert"
                    Toast.makeText(context, "Dokument $actionName", Toast.LENGTH_SHORT).show()
                } else {
                    Toast.makeText(context, "Aktion fehlgeschlagen (HTTP $code)", Toast.LENGTH_SHORT).show()
                }
            } catch (e: Exception) {
                Toast.makeText(context, "Fehler: ${e.localizedMessage}", Toast.LENGTH_SHORT).show()
            } finally {
                activeActionItemId = null
            }
        }
    }

    LaunchedEffect(Unit) {
        loadReviews()
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            "🛡️ Phobie-Schild",
                            style = MaterialTheme.typography.titleLarge.copy(fontWeight = FontWeight.Bold)
                        )
                    }
                },
                actions = {
                    FilledTonalButton(
                        onClick = { triggerPoll() },
                        enabled = !isPolling && !isLoading,
                        contentPadding = PaddingValues(horizontal = 10.dp, vertical = 6.dp),
                        shape = RoundedCornerShape(8.dp)
                    ) {
                        if (isPolling) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(14.dp),
                                strokeWidth = 2.dp
                            )
                            Spacer(modifier = Modifier.width(6.dp))
                        } else {
                            Icon(
                                imageVector = Icons.Default.Refresh,
                                contentDescription = null,
                                modifier = Modifier.size(16.dp)
                            )
                            Spacer(modifier = Modifier.width(4.dp))
                        }
                        Text("Postfach abrufen", fontSize = 12.sp)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface
                )
            )
        }
    ) { innerPadding ->
        Column(
            modifier = modifier
                .fillMaxSize()
                .padding(innerPadding)
                .padding(horizontal = 16.dp, vertical = 8.dp)
        ) {
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.secondaryContainer.copy(alpha = 0.8f)
                ),
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                Row(
                    modifier = Modifier.padding(16.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Box(
                        modifier = Modifier
                            .size(44.dp)
                            .background(MaterialTheme.colorScheme.secondary, CircleShape),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(
                            imageVector = Icons.Default.Shield,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSecondary,
                            modifier = Modifier.size(26.dp)
                        )
                    }
                    Spacer(modifier = Modifier.width(14.dp))
                    Column {
                        Text(
                            text = "Keine Panik! Alles unter Kontrolle.",
                            style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                            color = MaterialTheme.colorScheme.onSecondaryContainer
                        )
                        Spacer(modifier = Modifier.height(2.dp))
                        Text(
                            text = "Hermes schirmt beängstigende Mahnungen ab, fasst Fakten ruhig zusammen und bereitet rechtssicheren Aufschub vor.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSecondaryContainer,
                            lineHeight = 16.sp
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            if (isLoading && reviewItems.isEmpty()) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator()
                }
            } else if (reviewItems.isEmpty()) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f),
                    contentAlignment = Alignment.Center
                ) {
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        modifier = Modifier.padding(32.dp)
                    ) {
                        Text(
                            text = "🎉",
                            fontSize = 48.sp
                        )
                        Spacer(modifier = Modifier.height(12.dp))
                        Text(
                            text = "Keine Mahnungen oder offenen Prüfungen!",
                            style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                            textAlign = TextAlign.Center
                        )
                        Spacer(modifier = Modifier.height(6.dp))
                        Text(
                            text = "Dein Postfach ist sauber. Bei neuen Eingangsschreiben informiert dich Hermes automatisch.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            textAlign = TextAlign.Center
                        )
                        Spacer(modifier = Modifier.height(16.dp))
                        OutlinedButton(
                            onClick = { loadReviews(showToast = true) }
                        ) {
                            Text("Erneut prüfen")
                        }
                    }
                }
            } else {
                LazyColumn(
                    modifier = Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    items(reviewItems, key = { it.id }) { item ->
                        PhobieItemCard(
                            item = item,
                            isActionInProgress = activeActionItemId == item.id,
                            onApproveSend = { editedDraft ->
                                confirmSendItem = Pair(item, editedDraft)
                            },
                            onArchiveOnly = {
                                executeAction(item, "SEND_TO_PAPERLESS")
                            },
                            onDelete = {
                                executeAction(item, "DELETE")
                            }
                        )
                    }
                }
            }
        }
    }

    confirmSendItem?.let { (item, draftText) ->
        AlertDialog(
            onDismissRequest = { confirmSendItem = null },
            icon = {
                Icon(
                    imageVector = Icons.Default.Send,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.primary
                )
            },
            title = {
                Text("Antwort jetzt verbindlich absenden?", fontWeight = FontWeight.Bold)
            },
            text = {
                Column {
                    Text(
                        "Hiermit wird der folgende Antwortentwurf zur Fristverlängerung (ohne Schuldanerkenntnis) per E-Mail an den Absender versendet:\n",
                        style = MaterialTheme.typography.bodySmall
                    )
                    Surface(
                        color = MaterialTheme.colorScheme.surfaceVariant,
                        shape = RoundedCornerShape(8.dp),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Text(
                            text = draftText,
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.padding(10.dp),
                            maxLines = 8,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                }
            },
            confirmButton = {
                Button(
                    onClick = { approveAndSendDraft(item, draftText) }
                ) {
                    Text("1-Tap Freigabe: Jetzt senden")
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmSendItem = null }) {
                    Text("Abbrechen")
                }
            }
        )
    }
}

@Composable
fun PhobieItemCard(
    item: PhobieReviewItem,
    isActionInProgress: Boolean,
    onApproveSend: (String) -> Unit,
    onArchiveOnly: () -> Unit,
    onDelete: () -> Unit
) {
    var isExpanded by remember { mutableStateOf(false) }
    var draftText by remember(item.id, item.draftResponse) { mutableStateOf(item.draftResponse) }

    val isMahnung = item.category.contains("MAHNUNG", ignoreCase = true) || item.category.contains("INKASSO", ignoreCase = true)

    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(
            containerColor = if (isMahnung) MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.25f)
            else MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.7f)
        )
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Surface(
                    color = if (isMahnung) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
                    shape = RoundedCornerShape(12.dp)
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Icon(
                            imageVector = if (isMahnung) Icons.Default.Warning else Icons.Default.Security,
                            contentDescription = null,
                            tint = Color.White,
                            modifier = Modifier.size(14.dp)
                        )
                        Spacer(modifier = Modifier.width(4.dp))
                        Text(
                            text = if (isMahnung) "Mahnung / Inkasso" else "Prüfungs-Dokument",
                            color = Color.White,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Bold
                        )
                    }
                }

                if (item.amount.isNotBlank()) {
                    Text(
                        text = item.amount,
                        style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.ExtraBold),
                        color = MaterialTheme.colorScheme.error
                    )
                }
            }

            Spacer(modifier = Modifier.height(10.dp))

            Text(
                text = item.sender,
                style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )

            if (item.referenceNumber.isNotBlank()) {
                Text(
                    text = "Aktenzeichen / Ref: ${item.referenceNumber}",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            Spacer(modifier = Modifier.height(8.dp))

            Surface(
                color = MaterialTheme.colorScheme.surface,
                shape = RoundedCornerShape(10.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(12.dp)) {
                    Text(
                        text = "Sachliche Zusammenfassung:",
                        style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold),
                        color = MaterialTheme.colorScheme.primary
                    )
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = if (item.calmSummary.isNotBlank()) item.calmSummary else item.summary,
                        style = MaterialTheme.typography.bodyMedium,
                        lineHeight = 18.sp
                    )
                }
            }

            Spacer(modifier = Modifier.height(10.dp))

            if (item.draftResponse.isNotBlank()) {
                Surface(
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    shape = RoundedCornerShape(10.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(modifier = Modifier.padding(10.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                text = "Vorbereitete Fristverlängerung",
                                style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold)
                            )
                            IconButton(
                                onClick = { isExpanded = !isExpanded },
                                modifier = Modifier.size(24.dp)
                            ) {
                                Icon(
                                    imageVector = if (isExpanded) Icons.Default.KeyboardArrowUp else Icons.Default.KeyboardArrowDown,
                                    contentDescription = null
                                )
                            }
                        }

                        AnimatedVisibility(visible = isExpanded) {
                            Column {
                                Spacer(modifier = Modifier.height(6.dp))
                                Text(
                                    text = "Rechtssicher ohne Schuldanerkenntnis formuliert. Kann vor dem Absenden editiert werden:",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant
                                )
                                Spacer(modifier = Modifier.height(6.dp))
                                OutlinedTextField(
                                    value = draftText,
                                    onValueChange = { draftText = it },
                                    modifier = Modifier.fillMaxWidth(),
                                    textStyle = MaterialTheme.typography.bodySmall,
                                    minLines = 4,
                                    maxLines = 10
                                )
                            }
                        }
                    }
                }
            }

            Spacer(modifier = Modifier.height(14.dp))

            if (isActionInProgress) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(44.dp),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator(modifier = Modifier.size(24.dp))
                }
            } else {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (item.draftResponse.isNotBlank()) {
                        Button(
                            onClick = { onApproveSend(draftText) },
                            modifier = Modifier.fillMaxWidth(),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = MaterialTheme.colorScheme.primary
                            ),
                            shape = RoundedCornerShape(10.dp)
                        ) {
                            Icon(
                                imageVector = Icons.Default.Send,
                                contentDescription = null,
                                modifier = Modifier.size(16.dp)
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            Text("Fristverlängerung senden (1-Tap)", fontWeight = FontWeight.Bold)
                        }
                    }

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        OutlinedButton(
                            onClick = onArchiveOnly,
                            modifier = Modifier.weight(1f),
                            shape = RoundedCornerShape(10.dp)
                        ) {
                            Icon(
                                imageVector = Icons.Default.Archive,
                                contentDescription = null,
                                modifier = Modifier.size(14.dp)
                            )
                            Spacer(modifier = Modifier.width(4.dp))
                            Text("Nur Archivieren", fontSize = 12.sp)
                        }

                        TextButton(
                            onClick = onDelete,
                            modifier = Modifier.weight(0.7f),
                            colors = ButtonDefaults.textButtonColors(
                                contentColor = MaterialTheme.colorScheme.error
                            )
                        ) {
                            Icon(
                                imageVector = Icons.Default.Delete,
                                contentDescription = null,
                                modifier = Modifier.size(14.dp)
                            )
                            Spacer(modifier = Modifier.width(4.dp))
                            Text("Löschen", fontSize = 12.sp)
                        }
                    }
                }
            }
        }
    }
}
