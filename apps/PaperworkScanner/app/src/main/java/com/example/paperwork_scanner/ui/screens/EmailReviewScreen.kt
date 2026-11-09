package com.example.paperwork_scanner.ui.screens

import android.widget.Toast
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.paperwork_scanner.data.AuthPreferencesRepository
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

data class EmailReviewItem(
    val id: Int,
    val sender: String,
    val subject: String,
    val receivedAt: String,
    val summary: String,
    val category: String,
    val confidence: Double,
    val attachmentName: String?,
    val attachmentSize: Long,
    val status: String
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun EmailReviewScreen(
    modifier: Modifier = Modifier,
    onNavigateBack: () -> Unit = {},
    authRepository: AuthPreferencesRepository? = null
) {
    val context = LocalContext.current
    val coroutineScope = rememberCoroutineScope()

    var reviewItems by remember { mutableStateOf<List<EmailReviewItem>>(emptyList()) }
    var isLoading by remember { mutableStateOf(false) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    var activeActionItemId by remember { mutableStateOf<Int?>(null) }

    val okHttpClient = remember {
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)
            .writeTimeout(20, TimeUnit.SECONDS)
            .build()
    }

    // Lädt unklare E-Mails von Hermes
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
                    val parsed = mutableListOf<EmailReviewItem>()
                    for (i in 0 until array.length()) {
                        val obj = array.getJSONObject(i)
                        parsed.add(
                            EmailReviewItem(
                                id = obj.getInt("id"),
                                sender = obj.optString("sender", "Unbekannt"),
                                subject = obj.optString("subject", "(Kein Betreff)"),
                                receivedAt = obj.optString("received_at", ""),
                                summary = obj.optString("summary", ""),
                                category = obj.optString("category", "REVIEW"),
                                confidence = obj.optDouble("confidence", 0.5),
                                attachmentName = if (obj.has("attachment_name") && !obj.isNull("attachment_name")) obj.getString("attachment_name") else null,
                                attachmentSize = obj.optLong("attachment_size", 0L),
                                status = obj.optString("status", "pending")
                            )
                        )
                    }
                    reviewItems = parsed
                    if (showToast) {
                        Toast.makeText(context, "${parsed.size} E-Mail(s) zur Prüfung geladen", Toast.LENGTH_SHORT).show()
                    }
                } else {
                    errorMessage = "Serverfehler (HTTP $code): ${bodyStr.take(120)}"
                }
            } catch (e: Exception) {
                errorMessage = "Verbindungsfehler: ${e.localizedMessage}"
            } finally {
                isLoading = false
            }
        }
    }

    // Führt Aktion aus: DELETE oder SEND_TO_PAPERLESS
    fun executeAction(item: EmailReviewItem, action: String) {
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
                val bodyStr = response.body?.string().orEmpty()
                response.close()

                if (code in 200..299) {
                    reviewItems = reviewItems.filter { it.id != item.id }
                    val actionName = if (action == "DELETE") "gelöscht" else "an Paperless übergeben"
                    Toast.makeText(context, "E-Mail erfolgreich $actionName!", Toast.LENGTH_SHORT).show()
                } else {
                    Toast.makeText(context, "Fehler (HTTP $code): ${bodyStr.take(100)}", Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                Toast.makeText(context, "Aktions-Fehler: ${e.localizedMessage}", Toast.LENGTH_LONG).show()
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
                    Column {
                        Text(
                            text = "E-Mail Review",
                            style = MaterialTheme.typography.titleLarge.copy(fontWeight = FontWeight.Bold)
                        )
                        Text(
                            text = "Outer Heaven KI-Verteiler",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onNavigateBack) {
                        Text(text = "←", fontSize = 22.sp, fontWeight = FontWeight.Bold)
                    }
                },
                actions = {
                    IconButton(
                        onClick = { loadReviews(showToast = true) },
                        enabled = !isLoading
                    ) {
                        Text(text = "🔄", fontSize = 18.sp)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface
                )
            )
        }
    ) { innerPadding ->
        Box(
            modifier = modifier
                .fillMaxSize()
                .padding(innerPadding)
        ) {
            when {
                isLoading && reviewItems.isEmpty() -> {
                    Column(
                        modifier = Modifier.fillMaxSize(),
                        verticalArrangement = Arrangement.Center,
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        CircularProgressIndicator()
                        Spacer(modifier = Modifier.height(16.dp))
                        Text("Lade unklare E-Mails aus Outer Heaven...")
                    }
                }

                errorMessage != null && reviewItems.isEmpty() -> {
                    Column(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(24.dp),
                        verticalArrangement = Arrangement.Center,
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Text(
                            text = "⚠️ Verbindungsfehler",
                            style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                            color = MaterialTheme.colorScheme.error
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        Text(
                            text = errorMessage ?: "",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        Spacer(modifier = Modifier.height(16.dp))
                        Button(onClick = { loadReviews(showToast = true) }) {
                            Text("Erneut versuchen")
                        }
                    }
                }

                reviewItems.isEmpty() -> {
                    Column(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(32.dp),
                        verticalArrangement = Arrangement.Center,
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Surface(
                            shape = CircleShape,
                            color = MaterialTheme.colorScheme.primaryContainer,
                            modifier = Modifier.size(80.dp)
                        ) {
                            Box(contentAlignment = Alignment.Center) {
                                Text("✉️", fontSize = 36.sp)
                            }
                        }
                        Spacer(modifier = Modifier.height(16.dp))
                        Text(
                            text = "Keine E-Mails zur Überprüfung",
                            style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold)
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        Text(
                            text = "Der KI-Ingest hat alle eingehenden E-Mails entweder direkt archiviert oder gefiltert.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            textAlign = androidx.compose.ui.text.style.TextAlign.Center
                        )
                        Spacer(modifier = Modifier.height(20.dp))
                        OutlinedButton(onClick = { loadReviews(showToast = true) }) {
                            Text("Jetzt aktualisieren")
                        }
                    }
                }

                else -> {
                    LazyColumn(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(horizontal = 16.dp, vertical = 8.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        items(
                            items = reviewItems,
                            key = { it.id }
                        ) { item ->
                            val isActionRunning = activeActionItemId == item.id

                            AnimatedVisibility(
                                visible = true,
                                exit = fadeOut(tween(300)) + shrinkVertically(tween(300))
                            ) {
                                EmailReviewCard(
                                    item = item,
                                    isProcessing = isActionRunning,
                                    onDelete = { executeAction(item, "DELETE") },
                                    onSendToPaperless = { executeAction(item, "SEND_TO_PAPERLESS") }
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun EmailReviewCard(
    item: EmailReviewItem,
    isProcessing: Boolean,
    onDelete: () -> Unit,
    onSendToPaperless: () -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f)
        ),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp)
        ) {
            // Header: Sender & Badge
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = item.sender,
                    style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold),
                    color = MaterialTheme.colorScheme.primary,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f)
                )
                Spacer(modifier = Modifier.width(8.dp))
                Surface(
                    color = MaterialTheme.colorScheme.secondaryContainer,
                    shape = RoundedCornerShape(8.dp)
                ) {
                    Text(
                        text = "KI-REVIEW",
                        style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold),
                        color = MaterialTheme.colorScheme.onSecondaryContainer,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp)
                    )
                }
            }

            Spacer(modifier = Modifier.height(6.dp))

            // Betreff
            Text(
                text = item.subject,
                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold),
                color = MaterialTheme.colorScheme.onSurface
            )

            // Zusammenfassung / Triage-Notiz
            if (item.summary.isNotBlank()) {
                Spacer(modifier = Modifier.height(8.dp))
                Surface(
                    color = MaterialTheme.colorScheme.surface.copy(alpha = 0.8f),
                    shape = RoundedCornerShape(8.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Row(
                        modifier = Modifier.padding(10.dp),
                        verticalAlignment = Alignment.Top
                    ) {
                        Text(text = "🤖 ", fontSize = 14.sp)
                        Text(
                            text = item.summary,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }

            // Anhang-Info falls vorhanden
            if (!item.attachmentName.isNullOrBlank()) {
                Spacer(modifier = Modifier.height(8.dp))
                Surface(
                    color = MaterialTheme.colorScheme.tertiaryContainer.copy(alpha = 0.5f),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(text = "📎 ", fontSize = 12.sp)
                        Text(
                            text = "${item.attachmentName} (${item.attachmentSize / 1024} KB)",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onTertiaryContainer,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(14.dp))

            // Aktionen: 🗑️ Löschen (Swipe Links / Button) & 📄 An Paperless senden (Swipe Rechts / Button)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                OutlinedButton(
                    onClick = onDelete,
                    enabled = !isProcessing,
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(10.dp),
                    colors = ButtonDefaults.outlinedButtonColors(
                        contentColor = MaterialTheme.colorScheme.error
                    )
                ) {
                    Text("🗑️ Löschen", fontSize = 13.sp)
                }

                Button(
                    onClick = onSendToPaperless,
                    enabled = !isProcessing,
                    modifier = Modifier.weight(1.3f),
                    shape = RoundedCornerShape(10.dp),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = MaterialTheme.colorScheme.primary
                    )
                ) {
                    if (isProcessing) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(16.dp),
                            color = MaterialTheme.colorScheme.onPrimary,
                            strokeWidth = 2.dp
                        )
                        Spacer(modifier = Modifier.width(6.dp))
                        Text("Verarbeite...", fontSize = 13.sp)
                    } else {
                        Text("📄 An Paperless", fontSize = 13.sp)
                    }
                }
            }
        }
    }
}
