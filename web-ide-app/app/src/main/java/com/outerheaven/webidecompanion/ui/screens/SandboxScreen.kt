package com.outerheaven.webidecompanion.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.outerheaven.webidecompanion.data.model.SandboxJob
import com.outerheaven.webidecompanion.data.model.SandboxOutputFolder
import com.outerheaven.webidecompanion.ui.theme.*
import com.outerheaven.webidecompanion.ui.viewmodel.MainViewModel
import com.outerheaven.webidecompanion.ui.viewmodel.UiState

@Composable
fun SandboxScreen(
    viewModel: MainViewModel,
    modifier: Modifier = Modifier
) {
    val sandboxStatusState by viewModel.sandboxStatusState.collectAsState()
    val sandboxJobsState by viewModel.sandboxJobsState.collectAsState()
    val sandboxOutputsState by viewModel.sandboxOutputsState.collectAsState()
    val dropzoneState by viewModel.dropzoneState.collectAsState()
    val restartState by viewModel.restartContainerState.collectAsState()
    val decompileState by viewModel.decompileState.collectAsState()

    val scrollState = rememberScrollState()
    val apkScrollState = rememberScrollState()
    var selectedApkToDecompile by remember { mutableStateOf<String?>(null) }
    var filterKeyword by remember { mutableStateOf("") }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(DarkBackground)
            .verticalScroll(scrollState)
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // Header
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Text(
                        text = "REA-SANDBOX (DOCKER)",
                        style = MaterialTheme.typography.titleLarge,
                        color = AccentCyan,
                        fontWeight = FontWeight.Bold,
                        letterSpacing = 1.sp
                    )
                    Surface(
                        color = AccentCyan.copy(alpha = 0.2f),
                        shape = RoundedCornerShape(6.dp),
                        border = BorderStroke(1.dp, AccentCyan.copy(alpha = 0.6f))
                    ) {
                        Text(
                            text = "v1.0.6",
                            color = AccentCyan,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Bold,
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                        )
                    }
                }
                Text(
                    text = "Headless JADX & Reverse-Engineering Environment",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary
                )
            }
            IconButton(
                onClick = { viewModel.loadSandboxData() },
                modifier = Modifier
                    .clip(CircleShape)
                    .background(DarkSurfaceVariant)
            ) {
                Icon(
                    imageVector = Icons.Default.Refresh,
                    contentDescription = "Sandbox aktualisieren",
                    tint = AccentCyan
                )
            }
        }

        // 1. Container Health Card
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .border(1.dp, DarkBorder, RoundedCornerShape(12.dp)),
            colors = CardDefaults.cardColors(containerColor = DarkSurface)
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Icon(
                            imageVector = Icons.Default.Dns,
                            contentDescription = null,
                            tint = AccentCyan
                        )
                        Text(
                            text = "Container: rea-sandbox",
                            style = MaterialTheme.typography.titleMedium,
                            color = TextPrimary,
                            fontWeight = FontWeight.SemiBold
                        )
                    }

                    when (val s = sandboxStatusState) {
                        is UiState.Success -> {
                            StatusBadge(
                                isActive = s.data.is_running,
                                activeText = s.data.status.uppercase(),
                                inactiveText = s.data.status.uppercase()
                            )
                        }
                        else -> {
                            StatusBadge(isActive = false, activeText = "...", inactiveText = "PRÜFE...")
                        }
                    }
                }

                when (val s = sandboxStatusState) {
                    is UiState.Success -> {
                        val d = s.data
                        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Text(
                                text = "Image: ${d.image}",
                                style = MaterialTheme.typography.bodyMedium,
                                fontFamily = FontFamily.Monospace,
                                color = TextSecondary
                            )
                            Text(
                                text = "JADX Version: ${d.jadx_version} • PID: ${d.pid}",
                                style = MaterialTheme.typography.bodyMedium,
                                fontFamily = FontFamily.Monospace,
                                color = AccentCyan
                            )
                            if (d.started_at.isNotEmpty()) {
                                Text(
                                    text = "Gestartet: ${d.started_at.take(19).replace("T", " ")}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = TextMuted
                                )
                            }
                        }
                    }
                    is UiState.Error -> {
                        Text(
                            text = "Fehler: ${s.message}",
                            style = MaterialTheme.typography.bodyMedium,
                            color = AccentRed
                        )
                    }
                    else -> {}
                }

                // Restart Container Button
                val isRestarting = restartState is UiState.Loading
                OutlinedButton(
                    onClick = { viewModel.restartSandboxContainer() },
                    enabled = !isRestarting,
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = AccentOrange),
                    border = BorderStroke(1.dp, AccentOrange.copy(alpha = 0.6f)),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    if (isRestarting) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            color = AccentOrange,
                            strokeWidth = 2.dp
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text("Neustart läuft...", fontWeight = FontWeight.Medium)
                    } else {
                        Icon(imageVector = Icons.Default.RestartAlt, contentDescription = null)
                        Spacer(modifier = Modifier.width(8.dp))
                        Text("Container neustarten (Docker)", fontWeight = FontWeight.Medium)
                    }
                }
            }
        }

        // 2. Dekompilierungs-Aktion
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .border(1.dp, DarkBorder, RoundedCornerShape(12.dp)),
            colors = CardDefaults.cardColors(containerColor = DarkSurface)
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Icon(
                        imageVector = Icons.Default.Layers,
                        contentDescription = null,
                        tint = AccentGreen
                    )
                    Text(
                        text = "Einzel-Dekompilierung (JADX)",
                        style = MaterialTheme.typography.titleMedium,
                        color = TextPrimary,
                        fontWeight = FontWeight.SemiBold
                    )
                }

                Text(
                    text = "Wähle eine APK aus der Dropzone zur gezielten Dekompilierung:",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary
                )

                // Gezielte Code-Suche / Element-Filter
                OutlinedTextField(
                    value = filterKeyword,
                    onValueChange = { filterKeyword = it },
                    label = { Text("Gezielte Code-Suche / Element-Filter") },
                    placeholder = { Text("z.B. save, crypto, camera, vibe") },
                    singleLine = true,
                    leadingIcon = {
                        Icon(
                            imageVector = Icons.Default.Search,
                            contentDescription = null,
                            tint = AccentGreen
                        )
                    },
                    trailingIcon = {
                        if (filterKeyword.isNotEmpty()) {
                            IconButton(onClick = { filterKeyword = "" }) {
                                Icon(
                                    imageVector = Icons.Default.Clear,
                                    contentDescription = "Filter leeren",
                                    tint = TextSecondary
                                )
                            }
                        }
                    },
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = AccentGreen,
                        unfocusedBorderColor = DarkBorder,
                        focusedTextColor = TextPrimary,
                        unfocusedTextColor = TextPrimary,
                        cursorColor = AccentGreen,
                        focusedLabelColor = AccentGreen,
                        unfocusedLabelColor = TextSecondary,
                        focusedPlaceholderColor = TextMuted,
                        unfocusedPlaceholderColor = TextMuted
                    ),
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(8.dp)
                )

                // Horizontale APK-Auswahl
                when (val dz = dropzoneState) {
                    is UiState.Success -> {
                        val apks = dz.data.files.filter { it.is_apk }
                        if (apks.isEmpty()) {
                            Text(
                                text = "Keine APKs in der Dropzone verfügbar (/catlord-dropzone).",
                                style = MaterialTheme.typography.bodyMedium,
                                color = TextMuted
                            )
                        } else {
                            Text(
                                text = "Gefundene APKs in Dropzone (${apks.size}):",
                                style = MaterialTheme.typography.labelSmall,
                                color = TextSecondary,
                                fontWeight = FontWeight.Medium
                            )
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .horizontalScroll(apkScrollState),
                                horizontalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                apks.forEach { apk ->
                                    val isSelected = selectedApkToDecompile == apk.name
                                    FilterChip(
                                        selected = isSelected,
                                        onClick = { selectedApkToDecompile = apk.name },
                                        label = { Text("${apk.name} (${apk.size_formatted})") },
                                        colors = FilterChipDefaults.filterChipColors(
                                            selectedContainerColor = AccentGreen,
                                            selectedLabelColor = DarkBackground,
                                            containerColor = DarkSurfaceVariant,
                                            labelColor = TextSecondary
                                        ),
                                        border = FilterChipDefaults.filterChipBorder(
                                            enabled = true,
                                            selected = isSelected,
                                            borderColor = if (isSelected) AccentGreen else DarkBorder
                                        )
                                    )
                                }
                            }
                        }
                    }
                    else -> {}
                }

                val isDecompiling = decompileState is UiState.Loading
                Button(
                    onClick = {
                        selectedApkToDecompile?.let { apk ->
                            viewModel.decompileApk(apk, filterKeyword)
                        }
                    },
                    enabled = selectedApkToDecompile != null && !isDecompiling,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = AccentGreen,
                        contentColor = DarkBackground,
                        disabledContainerColor = DarkSurfaceVariant,
                        disabledContentColor = TextMuted
                    ),
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(44.dp),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    if (isDecompiling) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            color = AccentGreen,
                            strokeWidth = 2.dp
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text("Dekompilierung läuft...", fontWeight = FontWeight.Bold)
                    } else {
                        Icon(imageVector = Icons.Default.PlayArrow, contentDescription = null)
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = if (selectedApkToDecompile != null) "Dekompilieren: ${selectedApkToDecompile}" else "APK oben auswählen",
                            fontWeight = FontWeight.Bold
                        )
                    }
                }

                when (val dec = decompileState) {
                    is UiState.Success -> {
                        Surface(
                            color = AccentGreen.copy(alpha = 0.15f),
                            shape = RoundedCornerShape(6.dp),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text(
                                text = "✓ ${dec.data.message}",
                                color = AccentGreen,
                                style = MaterialTheme.typography.bodyMedium,
                                modifier = Modifier.padding(8.dp)
                            )
                        }
                    }
                    is UiState.Error -> {
                        Surface(
                            color = AccentRed.copy(alpha = 0.15f),
                            shape = RoundedCornerShape(6.dp),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text(
                                text = "✕ ${dec.message}",
                                color = AccentRed,
                                style = MaterialTheme.typography.bodyMedium,
                                modifier = Modifier.padding(8.dp)
                            )
                        }
                    }
                    else -> {}
                }
            }
        }

        // 3. Letzte Jobs
        when (val js = sandboxJobsState) {
            is UiState.Success -> {
                if (js.data.jobs.isNotEmpty()) {
                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .border(1.dp, DarkBorder, RoundedCornerShape(12.dp)),
                        colors = CardDefaults.cardColors(containerColor = DarkSurface)
                    ) {
                        Column(
                            modifier = Modifier.padding(16.dp),
                            verticalArrangement = Arrangement.spacedBy(10.dp)
                        ) {
                            Text(
                                text = "Letzte Jobs (${js.data.count})",
                                style = MaterialTheme.typography.titleMedium,
                                color = TextPrimary,
                                fontWeight = FontWeight.SemiBold
                            )

                            js.data.jobs.take(4).forEach { job ->
                                SandboxJobRow(job = job)
                            }
                        }
                    }
                }
            }
            else -> {}
        }

        // 4. Output-Explorer
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .border(1.dp, DarkBorder, RoundedCornerShape(12.dp)),
            colors = CardDefaults.cardColors(containerColor = DarkSurface)
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Icon(
                        imageVector = Icons.Default.FolderSpecial,
                        contentDescription = null,
                        tint = AccentCyan
                    )
                    Text(
                        text = "Output-Explorer (/output)",
                        style = MaterialTheme.typography.titleMedium,
                        color = TextPrimary,
                        fontWeight = FontWeight.SemiBold
                    )
                }

                when (val out = sandboxOutputsState) {
                    is UiState.Success -> {
                        val folders = out.data.folders
                        if (folders.isEmpty()) {
                            Text(
                                text = "Keine Artefakte im Output-Ordner vorhanden.",
                                style = MaterialTheme.typography.bodyMedium,
                                color = TextMuted
                            )
                        } else {
                            folders.forEach { folder ->
                                OutputFolderRow(folder = folder)
                            }
                        }
                    }
                    is UiState.Loading -> {
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(16.dp),
                            contentAlignment = Alignment.Center
                        ) {
                            CircularProgressIndicator(color = AccentCyan, strokeWidth = 2.dp)
                        }
                    }
                    is UiState.Error -> {
                        Text(
                            text = "Fehler: ${out.message}",
                            style = MaterialTheme.typography.bodyMedium,
                            color = AccentRed
                        )
                    }
                    else -> {}
                }
            }
        }
    }
}

@Composable
fun SandboxJobRow(job: SandboxJob) {
    Surface(
        color = DarkSurfaceVariant,
        shape = RoundedCornerShape(8.dp),
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(
            modifier = Modifier.padding(10.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = job.apk_name,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = TextPrimary
                )
                val isCompleted = job.status == "completed"
                val isRunning = job.status == "running"
                Surface(
                    color = if (isCompleted) AccentGreen.copy(alpha = 0.2f) else if (isRunning) AccentOrange.copy(alpha = 0.2f) else AccentRed.copy(alpha = 0.2f),
                    shape = RoundedCornerShape(4.dp)
                ) {
                    Text(
                        text = job.status.uppercase(),
                        color = if (isCompleted) AccentGreen else if (isRunning) AccentOrange else AccentRed,
                        style = MaterialTheme.typography.labelSmall,
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                        fontWeight = FontWeight.Bold
                    )
                }
            }
            Text(
                text = "Ziel: /output/${job.target_dir} • ${job.start_time.take(19).replace("T", " ")}",
                style = MaterialTheme.typography.labelSmall,
                color = TextMuted
            )
        }
    }
}

@Composable
fun OutputFolderRow(folder: SandboxOutputFolder) {
    Surface(
        color = DarkSurfaceVariant,
        shape = RoundedCornerShape(8.dp),
        modifier = Modifier.fillMaxWidth()
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                Text(
                    text = folder.name,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Medium,
                    color = TextPrimary
                )
                Text(
                    text = "${folder.file_count} Dateien • ${folder.size_formatted}",
                    style = MaterialTheme.typography.labelSmall,
                    color = TextSecondary
                )
            }

            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                if (folder.has_sources) {
                    Surface(
                        color = AccentCyan.copy(alpha = 0.15f),
                        shape = RoundedCornerShape(4.dp)
                    ) {
                        Text(
                            text = "SRC",
                            color = AccentCyan,
                            style = MaterialTheme.typography.labelSmall,
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                            fontWeight = FontWeight.Bold
                        )
                    }
                }
                if (folder.has_findings) {
                    Surface(
                        color = AccentGreen.copy(alpha = 0.15f),
                        shape = RoundedCornerShape(4.dp)
                    ) {
                        Text(
                            text = "FINDINGS",
                            color = AccentGreen,
                            style = MaterialTheme.typography.labelSmall,
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                            fontWeight = FontWeight.Bold
                        )
                    }
                }
            }
        }
    }
}
