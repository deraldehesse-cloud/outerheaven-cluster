package com.example.paperwork_scanner.ui.screens

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import com.example.paperwork_scanner.R
import com.example.paperwork_scanner.data.AuthPreferencesRepository
import com.example.paperwork_scanner.tailscale.TailscaleManager
import com.google.mlkit.vision.documentscanner.GmsDocumentScannerOptions
import com.google.mlkit.vision.documentscanner.GmsDocumentScanning
import com.google.mlkit.vision.documentscanner.GmsDocumentScanningResult
import androidx.compose.foundation.background
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

data class CachedDocument(
    val file: File,
    val originalName: String,
    val mimeType: String
)

@Composable
fun ScannerMainScreen(
    modifier: Modifier = Modifier,
    authRepository: AuthPreferencesRepository? = null,
    onNavigateToEmailReview: () -> Unit = {}
) {
    val context = LocalContext.current
    val activity = context as? Activity
    val coroutineScope = rememberCoroutineScope()
    val scrollState = rememberScrollState()

    // Connection state
    var isCheckingConnection by remember { mutableStateOf(false) }
    var isTailscaleConnected by remember { mutableStateOf<Boolean?>(null) }
    var hasAutoLaunchedTailscale by rememberSaveable { mutableStateOf(false) }

    // Upload & document state
    var statusText by remember { mutableStateOf("Bereit zum Erfassen") }
    var isUploading by remember { mutableStateOf(false) }
    var uploadError by remember { mutableStateOf<String?>(null) }
    var uploadSuccess by remember { mutableStateOf(false) }
    var cachedDoc by remember { mutableStateOf<CachedDocument?>(null) }
    var showTokenDialog by remember { mutableStateOf(false) }
    var tokenInputState by remember { mutableStateOf("") }
    var serverUrlInputState by remember { mutableStateOf("") }
    var validationStatus by remember { mutableStateOf<String?>(null) }
    var isValidatingToken by remember { mutableStateOf(false) }

    // Check connectivity function
    fun checkConnection(triggerAutoLaunch: Boolean = false) {
        coroutineScope.launch {
            isCheckingConnection = true
            val reachable = TailscaleManager.checkPaperlessEndpoint()
            isTailscaleConnected = reachable
            isCheckingConnection = false

            if (!reachable) {
                if (triggerAutoLaunch && !hasAutoLaunchedTailscale) {
                    hasAutoLaunchedTailscale = true
                    val launched = TailscaleManager.launchTailscaleApp(context)
                    if (!launched) {
                        Toast.makeText(
                            context,
                            "Tailscale-App nicht installiert. Bitte Tailscale-VPN verbinden.",
                            Toast.LENGTH_SHORT
                        ).show()
                    }
                }
            } else {
                hasAutoLaunchedTailscale = false
            }
        }
    }

    // Lifecycle observer for automatic onResume re-check without cold restart
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                checkConnection(triggerAutoLaunch = !hasAutoLaunchedTailscale)
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
        }
    }

    // ML Kit Scanner options
    val scannerOptions = remember {
        GmsDocumentScannerOptions.Builder()
            .setGalleryImportAllowed(false)
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

    fun startUpload(doc: CachedDocument) {
        cachedDoc = doc
        isUploading = true
        uploadError = null
        uploadSuccess = false
        statusText = "Übertrage \"${doc.originalName}\" an Paperless..."

        uploadToPaperless(context, doc, authRepository) { success, msg, httpCode ->
            isUploading = false
            if (success) {
                uploadSuccess = true
                uploadError = null
                statusText = msg
            } else {
                uploadSuccess = false
                uploadError = msg
                statusText = "Übertragung fehlgeschlagen"
                if (httpCode == 401) {
                    coroutineScope.launch {
                        val activeUrl = TailscaleManager.getActiveBaseUrl(authRepository)
                        val currentToken = authRepository?.getPaperlessToken() ?: AuthPreferencesRepository.DEFAULT_PAPERLESS_TOKEN
                        val validation = authRepository?.validateTokenOnline(currentToken, activeUrl)
                        when (validation) {
                            is AuthPreferencesRepository.TokenValidationResult.Invalid -> {
                                uploadError = "Authentifizierungsfehler (HTTP 401): Der API-Token wurde vom Server abgelehnt."
                            }
                            is AuthPreferencesRepository.TokenValidationResult.NetworkError -> {
                                uploadError = "Authentifizierungsfehler (HTTP 401), Token-Validierung fehlgeschlagen: ${validation.message}"
                            }
                            else -> {}
                        }
                        tokenInputState = currentToken
                        serverUrlInputState = authRepository?.getServerUrl() ?: AuthPreferencesRepository.DEFAULT_SERVER_URL
                        validationStatus = null
                        showTokenDialog = true
                    }
                }
            }
        }
    }

    // Launcher for ML Kit Scanner
    val scannerLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.StartIntentSenderForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            val scanResult = GmsDocumentScanningResult.fromActivityResultIntent(result.data)
            val pdfUri = scanResult?.pdf?.uri

            if (pdfUri != null) {
                val doc = cacheUriToLocalFile(context, pdfUri, "Scan_${System.currentTimeMillis()}.pdf")
                if (doc != null) {
                    startUpload(doc)
                } else {
                    uploadError = "Fehler beim lokalen Zwischenspeichern des Scans."
                    statusText = "Lokaler Cache-Fehler"
                }
            } else {
                uploadError = "Kein PDF vom Scanner generiert."
                statusText = "Scanner-Fehler"
            }
        }
    }

    // Launcher for Gallery & File selection (GetContent)
    val filePickerLauncher = rememberLauncherForActivityResult(
        contract = object : ActivityResultContracts.GetContent() {
            override fun createIntent(context: Context, input: String): Intent {
                val intent = super.createIntent(context, input)
                intent.putExtra(
                    Intent.EXTRA_MIME_TYPES,
                    arrayOf("application/pdf", "image/jpeg", "image/png", "image/webp")
                )
                return intent
            }
        }
    ) { uri: Uri? ->
        if (uri != null) {
            val doc = cacheUriToLocalFile(context, uri)
            if (doc != null) {
                startUpload(doc)
            } else {
                uploadError = "Konnte ausgewählte Datei nicht lesen oder zwischenspeichern."
                statusText = "Dateizugriffs-Fehler"
            }
        }
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(scrollState)
            .padding(horizontal = 24.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.Top,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Spacer(modifier = Modifier.height(8.dp))
        Image(
            painter = painterResource(id = R.drawable.logo_attic_guy),
            contentDescription = "Attic Guy Production Logo",
            modifier = Modifier
                .size(120.dp)
                .padding(bottom = 12.dp),
            contentScale = ContentScale.Fit
        )

        Text(
            text = "Paperless Ingest",
            style = MaterialTheme.typography.headlineMedium.copy(fontWeight = FontWeight.Bold)
        )

        Spacer(modifier = Modifier.height(6.dp))

        // Connection status indicator chip
        when (isTailscaleConnected) {
            true -> {
                Surface(
                    color = MaterialTheme.colorScheme.primaryContainer,
                    shape = RoundedCornerShape(16.dp),
                    modifier = Modifier.padding(vertical = 4.dp)
                ) {
                    Text(
                        text = "● Verbunden mit NAS deraldehesse",
                        color = MaterialTheme.colorScheme.onPrimaryContainer,
                        style = MaterialTheme.typography.labelMedium,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp)
                    )
                }
            }
            false -> {
                Surface(
                    color = MaterialTheme.colorScheme.errorContainer,
                    shape = RoundedCornerShape(16.dp),
                    modifier = Modifier.padding(vertical = 4.dp)
                ) {
                    Text(
                        text = "● Offline (NAS deraldehesse nicht erreichbar)",
                        color = MaterialTheme.colorScheme.onErrorContainer,
                        style = MaterialTheme.typography.labelMedium,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp)
                    )
                }
            }
            null -> {
                if (isCheckingConnection) {
                    Text(
                        text = "Prüfe Verbindung zu NAS deraldehesse...",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        }

        TextButton(
            onClick = {
                tokenInputState = authRepository?.getPaperlessToken() ?: AuthPreferencesRepository.DEFAULT_PAPERLESS_TOKEN
                serverUrlInputState = authRepository?.getServerUrl() ?: AuthPreferencesRepository.DEFAULT_SERVER_URL
                validationStatus = null
                showTokenDialog = true
            }
        ) {
            Text("⚙️ Paperless-Verbindung & Token", fontSize = 12.sp)
        }

        Spacer(modifier = Modifier.height(16.dp))

        // Prominent Tailscale Banner when not connected
        if (isTailscaleConnected == false) {
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.errorContainer
                ),
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(bottom = 16.dp)
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Text(
                        text = "⚠️ Tailscale-VPN verbinden",
                        style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                        color = MaterialTheme.colorScheme.onErrorContainer,
                        textAlign = TextAlign.Center
                    )
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(
                        text = "NAS deraldehesse (Paperless) ist nicht erreichbar. Bitte WLAN oder Tailscale aktivieren, um Dokumente zu übertragen.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onErrorContainer,
                        textAlign = TextAlign.Center
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        if (TailscaleManager.isTailscaleAppInstalled(context)) {
                            Button(
                                onClick = { TailscaleManager.launchTailscaleApp(context) },
                                modifier = Modifier.weight(1f),
                                colors = ButtonDefaults.buttonColors(
                                    containerColor = MaterialTheme.colorScheme.error,
                                    contentColor = MaterialTheme.colorScheme.onError
                                )
                            ) {
                                Text("Tailscale öffnen")
                            }
                        }
                        OutlinedButton(
                            onClick = { checkConnection(triggerAutoLaunch = false) },
                            enabled = !isCheckingConnection,
                            modifier = Modifier.weight(1f)
                        ) {
                            if (isCheckingConnection) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(16.dp),
                                    strokeWidth = 2.dp
                                )
                            } else {
                                Text("Erneut prüfen")
                            }
                        }
                    }
                }
            }
        }

        // Status text
        Text(
            text = statusText,
            style = MaterialTheme.typography.bodyMedium,
            textAlign = TextAlign.Center,
            color = when {
                uploadSuccess -> MaterialTheme.colorScheme.primary
                uploadError != null -> MaterialTheme.colorScheme.error
                else -> MaterialTheme.colorScheme.onSurface
            }
        )

        // Upload progress indicator
        if (isUploading) {
            Spacer(modifier = Modifier.height(16.dp))
            CircularProgressIndicator(modifier = Modifier.size(36.dp))
        }

        // Error card with RETRY BUTTON when upload fails
        if (uploadError != null) {
            Spacer(modifier = Modifier.height(16.dp))
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.7f)
                ),
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Text(
                        text = uploadError ?: "Fehler bei der Übertragung",
                        style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold),
                        color = MaterialTheme.colorScheme.onErrorContainer,
                        textAlign = TextAlign.Center
                    )

                    if (cachedDoc != null) {
                        Spacer(modifier = Modifier.height(6.dp))
                        Text(
                            text = "Dokument im Cache: ${cachedDoc!!.originalName} (${cachedDoc!!.file.length() / 1024} KB)",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onErrorContainer.copy(alpha = 0.85f),
                            textAlign = TextAlign.Center
                        )
                        Spacer(modifier = Modifier.height(12.dp))
                        Button(
                            onClick = {
                                cachedDoc?.let { startUpload(it) }
                            },
                            enabled = !isUploading,
                            modifier = Modifier.fillMaxWidth(),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = MaterialTheme.colorScheme.error,
                                contentColor = MaterialTheme.colorScheme.onError
                            )
                        ) {
                            Text("Erneut versuchen")
                        }
                        Spacer(modifier = Modifier.height(8.dp))
                        OutlinedButton(
                            onClick = {
                                tokenInputState = authRepository?.getPaperlessToken() ?: AuthPreferencesRepository.DEFAULT_PAPERLESS_TOKEN
                                serverUrlInputState = authRepository?.getServerUrl() ?: AuthPreferencesRepository.DEFAULT_SERVER_URL
                                validationStatus = null
                                showTokenDialog = true
                            },
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text("⚙️ Verbindung & API-Token anpassen")
                        }
                    }
                }
            }
        }

        Spacer(modifier = Modifier.height(28.dp))

        // Action Button 1: Dokument scannen
        Button(
            onClick = {
                if (activity != null) {
                    scanner.getStartScanIntent(activity)
                        .addOnSuccessListener { intentSender ->
                            scannerLauncher.launch(
                                IntentSenderRequest.Builder(intentSender).build()
                            )
                        }
                        .addOnFailureListener { e ->
                            Toast.makeText(
                                context,
                                "Scanner-Fehler: ${e.localizedMessage}",
                                Toast.LENGTH_LONG
                            ).show()
                        }
                } else {
                    Toast.makeText(context, "Fehler: Activity nicht verfügbar", Toast.LENGTH_SHORT).show()
                }
            },
            enabled = !isUploading,
            modifier = Modifier
                .fillMaxWidth()
                .height(52.dp),
            shape = RoundedCornerShape(12.dp)
        ) {
            Text(
                if (isUploading) "Wird übertragen..." else "Dokument scannen",
                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold)
            )
        }

        Spacer(modifier = Modifier.height(12.dp))

        // Action Button 2: Aus Galerie / Dateien wählen
        OutlinedButton(
            onClick = {
                filePickerLauncher.launch("*/*")
            },
            enabled = !isUploading,
            modifier = Modifier
                .fillMaxWidth()
                .height(52.dp),
            shape = RoundedCornerShape(12.dp)
        ) {
            Text(
                "Aus Galerie / Dateien wählen",
                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold)
            )
        }

        Spacer(modifier = Modifier.height(12.dp))

        // Action Button 3: E-Mail Review (KI-Verteiler)
        FilledTonalButton(
            onClick = onNavigateToEmailReview,
            enabled = !isUploading,
            modifier = Modifier
                .fillMaxWidth()
                .height(52.dp),
            shape = RoundedCornerShape(12.dp)
        ) {
            Text(
                "✉️ E-Mail Review (Outer Heaven)",
                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold)
            )
        }

        Spacer(modifier = Modifier.height(36.dp))
    }

    if (showTokenDialog) {
        AlertDialog(
            onDismissRequest = { showTokenDialog = false },
            title = {
                Text(
                    text = "Paperless Verbindung & Token",
                    fontWeight = FontWeight.Bold
                )
            },
            text = {
                Column {
                    Text(
                        text = "Server-URL und Paperless API-Token für NAS deraldehesse konfigurieren:",
                        style = MaterialTheme.typography.bodySmall
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    OutlinedTextField(
                        value = serverUrlInputState,
                        onValueChange = {
                            serverUrlInputState = it
                            validationStatus = null
                        },
                        label = { Text("Server-URL") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                    Spacer(modifier = Modifier.height(4.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        TextButton(
                            onClick = {
                                serverUrlInputState = AuthPreferencesRepository.DEFAULT_SERVER_URL
                                validationStatus = null
                            },
                            modifier = Modifier.weight(1f)
                        ) {
                            Text("Tailscale", fontSize = 11.sp)
                        }
                        TextButton(
                            onClick = {
                                serverUrlInputState = AuthPreferencesRepository.FALLBACK_SERVER_URL
                                validationStatus = null
                            },
                            modifier = Modifier.weight(1f)
                        ) {
                            Text("Lokales LAN", fontSize = 11.sp)
                        }
                    }
                    Spacer(modifier = Modifier.height(8.dp))
                    OutlinedTextField(
                        value = tokenInputState,
                        onValueChange = {
                            tokenInputState = it
                            validationStatus = null
                        },
                        label = { Text("API-Token") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                    Spacer(modifier = Modifier.height(4.dp))
                    TextButton(
                        onClick = {
                            tokenInputState = AuthPreferencesRepository.DEFAULT_PAPERLESS_TOKEN
                            validationStatus = null
                        }
                    ) {
                        Text("Standard-Admin-Token einsetzen", fontSize = 11.sp)
                    }

                    if (validationStatus != null) {
                        Spacer(modifier = Modifier.height(6.dp))
                        Text(
                            text = validationStatus!!,
                            style = MaterialTheme.typography.bodySmall,
                            color = if (validationStatus!!.contains("✅")) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error
                        )
                    }
                }
            },
            confirmButton = {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(
                        onClick = {
                            coroutineScope.launch {
                                isValidatingToken = true
                                val res = authRepository?.validateTokenOnline(tokenInputState.trim(), serverUrlInputState.trim())
                                isValidatingToken = false
                                validationStatus = when (res) {
                                    is AuthPreferencesRepository.TokenValidationResult.Valid -> "✅ Token ist gültig!"
                                    is AuthPreferencesRepository.TokenValidationResult.Invalid -> "❌ ${res.message}"
                                    is AuthPreferencesRepository.TokenValidationResult.ServerError -> "❌ ${res.message}"
                                    is AuthPreferencesRepository.TokenValidationResult.NetworkError -> "⚠️ ${res.message}"
                                    null -> "Unbekannter Status"
                                }
                            }
                        },
                        enabled = !isValidatingToken && tokenInputState.isNotBlank() && serverUrlInputState.isNotBlank()
                    ) {
                        if (isValidatingToken) {
                            CircularProgressIndicator(modifier = Modifier.size(14.dp), strokeWidth = 2.dp)
                        } else {
                            Text("Prüfen", fontSize = 12.sp)
                        }
                    }
                    Button(
                        onClick = {
                            val tokenToSave = tokenInputState.trim()
                            val urlToSave = serverUrlInputState.trim()
                            if (tokenToSave.isNotEmpty()) {
                                authRepository?.savePaperlessToken(tokenToSave)
                            }
                            if (urlToSave.isNotEmpty()) {
                                authRepository?.saveServerUrl(urlToSave)
                            }
                            showTokenDialog = false
                            cachedDoc?.let { startUpload(it) }
                        }
                    ) {
                        Text("Speichern")
                    }
                }
            },
            dismissButton = {
                TextButton(onClick = { showTokenDialog = false }) {
                    Text("Abbrechen")
                }
            }
        )
    }
}

fun cacheUriToLocalFile(
    context: Context,
    uri: Uri,
    preferredName: String? = null
): CachedDocument? {
    return try {
        val contentResolver = context.contentResolver
        var fileName = preferredName ?: "Dokument_${System.currentTimeMillis()}"
        var mimeType = contentResolver.getType(uri) ?: "application/octet-stream"

        if (preferredName == null) {
            contentResolver.query(uri, null, null, null, null)?.use { cursor ->
                val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (nameIndex != -1 && cursor.moveToFirst()) {
                    val name = cursor.getString(nameIndex)
                    if (!name.isNullOrBlank()) {
                        fileName = name
                    }
                }
            }
        }

        val extension = when {
            fileName.endsWith(".pdf", ignoreCase = true) || mimeType.contains("pdf", ignoreCase = true) -> ".pdf"
            fileName.endsWith(".png", ignoreCase = true) || mimeType.contains("png", ignoreCase = true) -> ".png"
            fileName.endsWith(".jpg", ignoreCase = true) || fileName.endsWith(".jpeg", ignoreCase = true) ||
                    mimeType.contains("jpeg", ignoreCase = true) || mimeType.contains("jpg", ignoreCase = true) -> ".jpg"
            fileName.endsWith(".webp", ignoreCase = true) || mimeType.contains("webp", ignoreCase = true) -> ".webp"
            else -> ".pdf"
        }

        if (!fileName.contains(".")) {
            fileName += extension
        }

        if (mimeType == "application/octet-stream") {
            mimeType = when (extension) {
                ".pdf" -> "application/pdf"
                ".jpg" -> "image/jpeg"
                ".png" -> "image/png"
                ".webp" -> "image/webp"
                else -> "application/pdf"
            }
        }

        // Speichere dauerhaft im Cache-Verzeichnis der App für Retry-Zwecke
        val cacheFile = File(context.cacheDir, "ingest_${System.currentTimeMillis()}$extension")
        contentResolver.openInputStream(uri)?.use { input ->
            cacheFile.outputStream().use { output ->
                input.copyTo(output)
            }
        }

        if (cacheFile.exists() && cacheFile.length() > 0) {
            CachedDocument(
                file = cacheFile,
                originalName = fileName,
                mimeType = mimeType
            )
        } else {
            null
        }
    } catch (e: Exception) {
        null
    }
}

fun uploadToPaperless(
    context: Context,
    cachedDoc: CachedDocument,
    authRepository: AuthPreferencesRepository?,
    tagIds: List<Int> = emptyList(),
    onResult: (Boolean, String, Int?) -> Unit
) {
    val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    try {
        if (!cachedDoc.file.exists() || cachedDoc.file.length() == 0L) {
            (context as? Activity)?.runOnUiThread { onResult(false, "Lokale Cachedatei nicht gefunden", null) }
                ?: onResult(false, "Lokale Cachedatei nicht gefunden", null)
            return
        }

        // NAS deraldehesse Endpoint (Tailscale Primär / LAN Fallback)
        val baseUrl = TailscaleManager.getActiveBaseUrl(authRepository)
        val serverUrl = "$baseUrl/api/documents/post_document/"
        val authToken = authRepository?.getPaperlessToken() ?: AuthPreferencesRepository.DEFAULT_PAPERLESS_TOKEN

        val mediaType = cachedDoc.mimeType.toMediaTypeOrNull()
        val requestBodyBuilder = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart(
                "document",
                cachedDoc.originalName,
                cachedDoc.file.asRequestBody(mediaType)
            )
        for (tagId in tagIds) {
            requestBodyBuilder.addFormDataPart("tags", tagId.toString())
        }
        val requestBody = requestBodyBuilder.build()

        val request = Request.Builder()
            .url(serverUrl)
            .addHeader("Authorization", "Token $authToken")
            .post(requestBody)
            .build()

        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                (context as? Activity)?.runOnUiThread {
                    onResult(false, "Verbindungsfehler: ${e.localizedMessage}", null)
                } ?: onResult(false, "Verbindungsfehler: ${e.localizedMessage}", null)
            }

            override fun onResponse(call: Call, response: Response) {
                val code = response.code
                val responseText = response.body?.string().orEmpty()
                response.close()

                val success = code in 200..299
                val msg = if (success) {
                    "Dokument \"${cachedDoc.originalName}\" erfolgreich an Paperless übermittelt!"
                } else if (code == 401) {
                    "Authentifizierungsfehler (HTTP 401): API-Token ungültig."
                } else {
                    "Serverfehler (HTTP $code): ${if (responseText.isNotBlank()) responseText.take(150) else "Fehler"}"
                }

                (context as? Activity)?.runOnUiThread {
                    onResult(success, msg, code)
                } ?: onResult(success, msg, code)
            }
        })
    } catch (e: Exception) {
        (context as? Activity)?.runOnUiThread {
            onResult(false, "Upload-Ausnahme: ${e.localizedMessage}", null)
        } ?: onResult(false, "Upload-Ausnahme: ${e.localizedMessage}", null)
    }
}
