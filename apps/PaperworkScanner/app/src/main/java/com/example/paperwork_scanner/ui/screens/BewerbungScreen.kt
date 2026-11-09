package com.example.paperwork_scanner.ui.screens

import android.Manifest
import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.print.PrintAttributes
import android.print.PrintManager
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Lightbulb
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Print
import androidx.compose.material.icons.filled.Send
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import com.example.paperwork_scanner.data.AuthPreferencesRepository
import com.example.paperwork_scanner.speech.SpeechRecognitionHelper
import com.example.paperwork_scanner.speech.SpeechState
import com.example.paperwork_scanner.tailscale.TailscaleManager
import com.google.mlkit.vision.documentscanner.GmsDocumentScannerOptions
import com.google.mlkit.vision.documentscanner.GmsDocumentScanning
import com.google.mlkit.vision.documentscanner.GmsDocumentScanningResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

data class ApplicationResult(
    val coverLetter: String,
    val cv: String,
    val tips: String,
    val docsFound: Int,
    val modelUsed: String
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BewerbungScreen(
    modifier: Modifier = Modifier,
    authRepository: AuthPreferencesRepository? = null
) {
    val context = LocalContext.current
    val coroutineScope = rememberCoroutineScope()
    val scrollState = rememberScrollState()

    var jobDescription by remember { mutableStateOf("") }
    var userNotes by remember { mutableStateOf("") }
    var selectedModel by remember { mutableStateOf("catlord-hermes:latest") }
    var isGenerating by remember { mutableStateOf(false) }
    var generationStatusText by remember { mutableStateOf<String?>(null) }
    var resultData by remember { mutableStateOf<ApplicationResult?>(null) }
    var selectedResultTab by remember { mutableIntStateOf(0) }

    // Upload & Scan state
    var isScanningOrUploading by remember { mutableStateOf(false) }
    var scanStatusMessage by remember { mutableStateOf<String?>(null) }

    // Speech state
    var speechState by remember { mutableStateOf<SpeechState>(SpeechState.Ready) }
    var speechHelper by remember { mutableStateOf<SpeechRecognitionHelper?>(null) }

    val okHttpClient = remember {
        OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(180, TimeUnit.SECONDS) // LLM Inferenz kann 30-90s dauern
            .writeTimeout(60, TimeUnit.SECONDS)
            .build()
    }

    // Initialize speech helper
    DisposableEffect(Unit) {
        val helper = SpeechRecognitionHelper(
            context = context,
            onStateChanged = { state -> speechState = state },
            onPartialResult = { partial ->
                // Live preview if needed
            },
            onFinalResult = { text ->
                if (text.isNotBlank()) {
                    if (userNotes.isBlank()) {
                        userNotes = text
                    } else {
                        userNotes += " $text"
                    }
                }
            }
        )
        speechHelper = helper
        onDispose {
            helper.destroy()
        }
    }

    // Permission launcher for microphone
    val micPermissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission()
    ) { isGranted ->
        if (isGranted) {
            speechHelper?.startListening("de-DE")
        } else {
            Toast.makeText(context, "Mikrofon-Berechtigung erforderlich für Spracheingabe", Toast.LENGTH_SHORT).show()
        }
    }

    fun toggleSpeech() {
        if (speechState is SpeechState.Listening) {
            speechHelper?.stopListening()
        } else {
            val hasPermission = ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.RECORD_AUDIO
            ) == PackageManager.PERMISSION_GRANTED

            if (hasPermission) {
                speechHelper?.startListening("de-DE")
            } else {
                micPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
            }
        }
    }

    // Document Scanner for "Bewerbungsunterlagen" (Tag ID 1)
    val scannerOptions = remember {
        GmsDocumentScannerOptions.Builder()
            .setGalleryImportAllowed(true)
            .setPageLimit(25)
            .setResultFormats(
                GmsDocumentScannerOptions.RESULT_FORMAT_JPEG,
                GmsDocumentScannerOptions.RESULT_FORMAT_PDF
            )
            .setScannerMode(GmsDocumentScannerOptions.SCANNER_MODE_FULL)
            .build()
    }

    val scanner = remember(scannerOptions) {
        GmsDocumentScanning.getClient(scannerOptions)
    }

    val scannerLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.StartIntentSenderForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            val scanResult = GmsDocumentScanningResult.fromActivityResultIntent(result.data)
            val pdfUri = scanResult?.pdf?.uri

            if (pdfUri != null) {
                val doc = cacheUriToLocalFile(context, pdfUri, "Bewerbung_${System.currentTimeMillis()}.pdf")
                if (doc != null) {
                    isScanningOrUploading = true
                    scanStatusMessage = "Lade Dokument mit Tag 'Bewerbungsunterlagen' hoch..."
                    uploadToPaperless(
                        context = context,
                        cachedDoc = doc,
                        authRepository = authRepository,
                        tagIds = listOf(1) // Tag ID 1 = Bewerbungsunterlagen
                    ) { success, msg, _ ->
                        isScanningOrUploading = false
                        scanStatusMessage = if (success) "✅ In Paperless als Bewerbungsunterlage gesichert!" else "❌ Fehler: $msg"
                        Toast.makeText(context, scanStatusMessage, Toast.LENGTH_SHORT).show()
                    }
                } else {
                    scanStatusMessage = "Fehler beim lokalen Zwischenspeichern."
                }
            }
        }
    }

    fun generateApplication() {
        if (jobDescription.isBlank()) {
            Toast.makeText(context, "Bitte gib zuerst eine Stellenanzeige oder Beschreibung ein.", Toast.LENGTH_SHORT).show()
            return
        }

        coroutineScope.launch {
            isGenerating = true
            generationStatusText = "Hermes sucht nach deinen Unterlagen in Paperless und generiert Anschreiben..."
            try {
                val baseUrl = withContext(Dispatchers.IO) { TailscaleManager.getHermesBaseUrl() }
                val payload = JSONObject().apply {
                    put("job_description", jobDescription)
                    put("notes", userNotes)
                    put("preferred_model", selectedModel)
                }

                val reqBody = payload.toString().toRequestBody("application/json".toMediaTypeOrNull())
                val request = Request.Builder()
                    .url("$baseUrl/api/applications/generate")
                    .post(reqBody)
                    .build()

                val response = withContext(Dispatchers.IO) { okHttpClient.newCall(request).execute() }
                val code = response.code
                val bodyStr = response.body?.string().orEmpty()
                response.close()

                if (code == 200) {
                    val json = JSONObject(bodyStr)
                    resultData = ApplicationResult(
                        coverLetter = json.optString("cover_letter", ""),
                        cv = json.optString("cv", ""),
                        tips = json.optString("tips", ""),
                        docsFound = json.optInt("paperless_docs_found", 0),
                        modelUsed = json.optString("model_used", selectedModel)
                    )
                    generationStatusText = null
                    Toast.makeText(context, "Bewerbung erfolgreich generiert!", Toast.LENGTH_SHORT).show()
                } else {
                    generationStatusText = "Fehler bei der Generierung (HTTP $code): ${bodyStr.take(120)}"
                }
            } catch (e: Exception) {
                generationStatusText = "Verbindungsfehler: ${e.localizedMessage}"
            } finally {
                isGenerating = false
            }
        }
    }

    fun copyToClipboard(text: String, label: String = "Bewerbungstext") {
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clip = ClipData.newPlainText(label, text)
        clipboard.setPrimaryClip(clip)
        Toast.makeText(context, "$label in Zwischenablage kopiert", Toast.LENGTH_SHORT).show()
    }

    fun shareText(text: String, title: String = "Bewerbung teilen") {
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(Intent.EXTRA_TEXT, text)
            putExtra(Intent.EXTRA_SUBJECT, "Bewerbungsunterlagen")
        }
        context.startActivity(Intent.createChooser(intent, title))
    }

    fun printText(text: String, jobName: String = "Bewerbung_Druck") {
        try {
            val webView = WebView(context)
            val html = """
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="utf-8">
                    <style>
                        body {
                            font-family: Arial, sans-serif;
                            font-size: 12pt;
                            line-height: 1.6;
                            padding: 25mm 20mm;
                            color: #111;
                        }
                        p { margin-bottom: 1em; white-space: pre-wrap; }
                    </style>
                </head>
                <body>
                    <p>${text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")}</p>
                </body>
                </html>
            """.trimIndent()

            webView.webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView?, url: String?) {
                    val printManager = context.getSystemService(Context.PRINT_SERVICE) as? PrintManager
                    val adapter = webView.createPrintDocumentAdapter(jobName)
                    printManager?.print(jobName, adapter, PrintAttributes.Builder().build())
                }
            }
            webView.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
        } catch (e: Exception) {
            Toast.makeText(context, "Drucken fehlgeschlagen: ${e.localizedMessage}", Toast.LENGTH_SHORT).show()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        "💼 Bewerbungs-Assistent",
                        style = MaterialTheme.typography.titleLarge.copy(fontWeight = FontWeight.Bold)
                    )
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
                .verticalScroll(scrollState)
                .padding(horizontal = 16.dp, vertical = 8.dp)
        ) {
            // Header Info Card
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.7f)
                ),
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        text = "Vollautomatisches Anschreiben & Lebenslauf",
                        style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold),
                        color = MaterialTheme.colorScheme.onPrimaryContainer
                    )
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = "Scanne Zeugnisse direkt in Paperless, sprich deine Wünsche ein und lass Catlord & Hermes eine passgenaue Bewerbung erstellen.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onPrimaryContainer
                    )
                }
            }

            Spacer(modifier = Modifier.height(14.dp))

            // Action: Zeugnis / Stellenanzeige scannen
            OutlinedButton(
                onClick = {
                    val activity = context as? Activity
                    if (activity != null) {
                        scanner.getStartScanIntent(activity)
                            .addOnSuccessListener { intentSender ->
                                scannerLauncher.launch(
                                    androidx.activity.result.IntentSenderRequest.Builder(intentSender).build()
                                )
                            }
                            .addOnFailureListener { e ->
                                Toast.makeText(context, "Scanner-Start fehlgeschlagen: ${e.localizedMessage}", Toast.LENGTH_SHORT).show()
                            }
                    }
                },
                enabled = !isScanningOrUploading,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(50.dp),
                shape = RoundedCornerShape(12.dp)
            ) {
                if (isScanningOrUploading) {
                    CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                    Spacer(modifier = Modifier.width(8.dp))
                } else {
                    Icon(
                        imageVector = Icons.Default.Description,
                        contentDescription = null,
                        modifier = Modifier.size(20.dp)
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                }
                Text("📄 Zeugnis / Unterlagen scannen", fontWeight = FontWeight.SemiBold)
            }

            if (scanStatusMessage != null) {
                Spacer(modifier = Modifier.height(6.dp))
                Text(
                    text = scanStatusMessage!!,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.padding(horizontal = 4.dp)
                )
            }

            Spacer(modifier = Modifier.height(16.dp))

            // Input: Stellenanzeige
            Text(
                text = "1. Stellenanzeige oder Anforderungsprofil:",
                style = MaterialTheme.typography.labelLarge.copy(fontWeight = FontWeight.Bold)
            )
            Spacer(modifier = Modifier.height(6.dp))
            OutlinedTextField(
                value = jobDescription,
                onValueChange = { jobDescription = it },
                placeholder = { Text("Hier Text der Stellenanzeige hineinkopieren oder Stichworte eingeben...") },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(120.dp),
                textStyle = MaterialTheme.typography.bodySmall,
                shape = RoundedCornerShape(12.dp)
            )

            Spacer(modifier = Modifier.height(14.dp))

            // Input: Eigene Notizen & Spracheingabe
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "2. Eigene Notizen & Wünsche:",
                    style = MaterialTheme.typography.labelLarge.copy(fontWeight = FontWeight.Bold)
                )

                // Spracheingabe Button
                val isListening = speechState is SpeechState.Listening
                FilledTonalButton(
                    onClick = { toggleSpeech() },
                    colors = ButtonDefaults.filledTonalButtonColors(
                        containerColor = if (isListening) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.secondaryContainer,
                        contentColor = if (isListening) MaterialTheme.colorScheme.onError else MaterialTheme.colorScheme.onSecondaryContainer
                    ),
                    shape = RoundedCornerShape(20.dp),
                    contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp)
                ) {
                    Icon(
                        imageVector = if (isListening) Icons.Default.Mic else Icons.Default.MicOff,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp)
                    )
                    Spacer(modifier = Modifier.width(4.dp))
                    Text(
                        text = if (isListening) "Zuhören..." else "Einsprechen",
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold
                    )
                }
            }

            Spacer(modifier = Modifier.height(6.dp))
            OutlinedTextField(
                value = userNotes,
                onValueChange = { userNotes = it },
                placeholder = { Text("z. B. 'Betone meine Zuverlässigkeit und Linux-Erfahrung, Teilzeit bevorzugt...'") },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(90.dp),
                textStyle = MaterialTheme.typography.bodySmall,
                shape = RoundedCornerShape(12.dp)
            )

            Spacer(modifier = Modifier.height(14.dp))

            // Modell-Auswahl
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "KI-Modell:",
                    style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold)
                )
                FilterChip(
                    selected = selectedModel == "catlord-hermes:latest",
                    onClick = { selectedModel = "catlord-hermes:latest" },
                    label = { Text("Catlord (Metal-DGear)", fontSize = 11.sp) }
                )
                FilterChip(
                    selected = selectedModel == "llama3.2:3b",
                    onClick = { selectedModel = "llama3.2:3b" },
                    label = { Text("Llama 3.2 (Fox)", fontSize = 11.sp) }
                )
            }

            Spacer(modifier = Modifier.height(18.dp))

            // Action Button: 🚀 Generieren
            Button(
                onClick = { generateApplication() },
                enabled = !isGenerating,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(52.dp),
                shape = RoundedCornerShape(14.dp)
            ) {
                if (isGenerating) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(20.dp),
                        color = MaterialTheme.colorScheme.onPrimary,
                        strokeWidth = 2.dp
                    )
                    Spacer(modifier = Modifier.width(10.dp))
                } else {
                    Text("🚀 ", fontSize = 18.sp)
                }
                Text("Bewerbung jetzt generieren", style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold))
            }

            if (generationStatusText != null) {
                Spacer(modifier = Modifier.height(10.dp))
                Text(
                    text = generationStatusText!!,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.secondary,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.fillMaxWidth()
                )
            }

            // Results Section
            resultData?.let { res ->
                Spacer(modifier = Modifier.height(24.dp))
                Divider()
                Spacer(modifier = Modifier.height(16.dp))

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "Generierte Bewerbung",
                        style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold)
                    )
                    Text(
                        text = "${res.docsFound} Paperless-Beleg(e) genutzt",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.primary
                    )
                }

                Spacer(modifier = Modifier.height(12.dp))

                // Result Tabs
                TabRow(
                    selectedTabIndex = selectedResultTab,
                    containerColor = MaterialTheme.colorScheme.surfaceVariant,
                    modifier = Modifier.clip(RoundedCornerShape(12.dp))
                ) {
                    Tab(
                        selected = selectedResultTab == 0,
                        onClick = { selectedResultTab = 0 },
                        text = { Text("📝 Anschreiben", fontSize = 12.sp) }
                    )
                    Tab(
                        selected = selectedResultTab == 1,
                        onClick = { selectedResultTab = 1 },
                        text = { Text("📋 Lebenslauf", fontSize = 12.sp) }
                    )
                    Tab(
                        selected = selectedResultTab == 2,
                        onClick = { selectedResultTab = 2 },
                        text = { Text("💡 Tipps", fontSize = 12.sp) }
                    )
                }

                Spacer(modifier = Modifier.height(12.dp))

                val currentContent = when (selectedResultTab) {
                    0 -> res.coverLetter
                    1 -> res.cv
                    else -> res.tips
                }

                val currentTitle = when (selectedResultTab) {
                    0 -> "Anschreiben"
                    1 -> "Lebenslauf"
                    else -> "Bewerbungs-Tipps"
                }

                // Action Toolbar (Kopieren, Teilen, Drucken)
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    FilledTonalButton(
                        onClick = { copyToClipboard(currentContent, currentTitle) },
                        modifier = Modifier.weight(1f),
                        shape = RoundedCornerShape(10.dp)
                    ) {
                        Icon(imageVector = Icons.Default.ContentCopy, contentDescription = null, modifier = Modifier.size(16.dp))
                        Spacer(modifier = Modifier.width(4.dp))
                        Text("Kopieren", fontSize = 12.sp)
                    }

                    FilledTonalButton(
                        onClick = { shareText(currentContent, currentTitle) },
                        modifier = Modifier.weight(1f),
                        shape = RoundedCornerShape(10.dp)
                    ) {
                        Icon(imageVector = Icons.Default.Share, contentDescription = null, modifier = Modifier.size(16.dp))
                        Spacer(modifier = Modifier.width(4.dp))
                        Text("Teilen", fontSize = 12.sp)
                    }

                    FilledTonalButton(
                        onClick = { printText(currentContent, "Bewerbung_${currentTitle}") },
                        modifier = Modifier.weight(1f),
                        shape = RoundedCornerShape(10.dp)
                    ) {
                        Icon(imageVector = Icons.Default.Print, contentDescription = null, modifier = Modifier.size(16.dp))
                        Spacer(modifier = Modifier.width(4.dp))
                        Text("Druck/PDF", fontSize = 12.sp)
                    }
                }

                Spacer(modifier = Modifier.height(10.dp))

                // Content View
                Surface(
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text(
                        text = currentContent,
                        style = MaterialTheme.typography.bodyMedium,
                        lineHeight = 22.sp,
                        modifier = Modifier.padding(16.dp)
                    )
                }
            }

            Spacer(modifier = Modifier.height(32.dp))
        }
    }
}
